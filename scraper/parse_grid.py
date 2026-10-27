"""表（Excel のシート、PDF から抽出した表）を医療機関レコードに変換する。

表の細かいレイアウトは事前に確定できないため、見出し語で列を特定する方式にしている。
- 見出し行: 「医療機関番号」「名称」「所在地」などの語を3つ以上含む行（複数行にまたがってもよい）
- レコード開始行: 項番列に整数がある行（項番列が無ければ機関番号列に値がある行）
- 次の開始行までの行は同じレコードの続き（セル内改行・2段組みに対応）
- 「医科　指定分」「歯科　遡及指定分」のような区分見出しは、以降のレコードに引き継ぐ
各レコードには、見出し→値の組（raw）を元表の列順のまま保存し、表示側で全項目を出せるようにする。
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field

from .regions import DEPT_ABBR
from .wareki import find_dates

FIELD_PATTERNS: dict[str, re.Pattern] = {
    "no": re.compile(r"^(項番|No\.?|NO\.?|番号|連番)$"),
    "code": re.compile(r"(機関|薬局).*(番号|コード)|^コード$"),
    "name": re.compile(r"名称|機関名|薬局名"),
    "address": re.compile(r"所在地|住所"),
    "tel": re.compile(r"電話"),
    "founder": re.compile(r"開設者"),
    "manager": re.compile(r"管理者"),
    "table": re.compile(r"点数表|医療機関区分|^区分$"),
    "date": re.compile(r"指定年月日|指定日|指定年月"),
    "period": re.compile(r"指定期間|期間"),
    "staff": re.compile(r"勤務|医師数|常勤|従事者"),
    "dept": re.compile(r"診療科|科目|標榜"),
    "beds": re.compile(r"病床"),
    "note": re.compile(r"備考"),
    "reason": re.compile(r"登録理由|理由|異動事由|事由"),
    "status": re.compile(r"現存|状態|状況"),
}

CODE_RE = re.compile(r"(\d{2})\s*[,，、.]?\s*(\d{4})\s*[,，、.]?\s*(\d)")
REASON_RE = re.compile(r"新規|交代|移動|移転|組織変更|再指定|その他|継続")
STATUS_RE = re.compile(r"現存|廃止|休止|辞退|取消")
SECTION_RE = re.compile(r"(医科|歯科|薬局|調剤)\s*[^\]］]{0,6}?(遡及指定分|指定分|遡及)")


def norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    s = unicodedata.normalize("NFKC", str(v))
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def header_hits(row: list) -> dict[int, list[str]]:
    hits: dict[int, list[str]] = {}
    for i, cell in enumerate(row):
        t = squash(norm(cell))
        if not t or len(t) > 30:
            continue
        for key, pat in FIELD_PATTERNS.items():
            if pat.search(t):
                hits.setdefault(i, []).append(key)
    return hits


def is_header_row(row: list) -> bool:
    hits = header_hits(row)
    keys = {k for ks in hits.values() for k in ks}
    return len(hits) >= 3 and bool(keys & {"code", "name", "address"})


def is_int_cell(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, float):
        return v.is_integer()
    return bool(re.fullmatch(r"\d{1,5}", squash(norm(v))))


@dataclass
class Layout:
    headers: list[str]
    cols: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: list[list]) -> "Layout":
        width = max(len(r) for r in rows)
        headers = []
        for c in range(width):
            parts = []
            for r in rows:
                t = norm(r[c]) if c < len(r) else ""
                # 見出しの空白（「医 療 機 関 番 号」）は除き、セル内の改行は「／」に
                for piece in re.split(r"[\n/／]", t):
                    piece = squash(piece)
                    if piece and piece not in parts:
                        parts.append(piece)
            headers.append("／".join(parts))
        lay = cls(headers=headers)
        for key, pat in FIELD_PATTERNS.items():
            for c, h in enumerate(headers):
                # 見出しは「／」で複数行を結合しているので、各片で照合する
                if any(pat.search(p) for p in h.split("／") if p):
                    lay.cols[key] = c
                    break
        # 「医療機関番号」が name パターン（機関名）に誤一致しないよう、code と同列なら name を外す
        if lay.cols.get("name") is not None and lay.cols.get("name") == lay.cols.get("code"):
            for c, h in enumerate(headers):
                if c != lay.cols["code"] and re.search(r"名称", h):
                    lay.cols["name"] = c
                    break
            else:
                lay.cols.pop("name")
        return lay


def detect_section(text: str) -> str | None:
    t = squash(norm(text))
    m = SECTION_RE.search(t)
    if not m:
        return None
    cat = {"調剤": "薬局"}.get(m.group(1), m.group(1))
    sec = "遡及指定分" if "遡及" in m.group(2) else "指定分"
    return f"{cat}|{sec}"


def grid_to_records(grid: list[list], context: dict | None = None) -> tuple[list[dict], dict]:
    """grid（行のリスト）からレコードを抽出する。戻り値は (records, diagnostics)。"""
    context = dict(context or {})
    section = context.get("section")
    layout: Layout | None = None
    records: list[dict] = []
    current: dict | None = None
    header_buf: list[list] = []
    diag = {"headers": None, "rows": len(grid), "records": 0, "skipped_rows": 0}

    def flush():
        nonlocal current
        if current is not None:
            records.append(current)
        current = None

    def start_col() -> int | None:
        if layout is None:
            return None
        return layout.cols.get("no", layout.cols.get("code"))

    i = 0
    while i < len(grid):
        row = list(grid[i])
        texts = [norm(c) for c in row]
        nonempty = [t for t in texts if t]
        if not nonempty:
            i += 1
            continue

        # 区分見出し（少数セルの行）
        if len(nonempty) <= 3:
            sec = detect_section(" ".join(nonempty))
            if sec:
                flush()
                section = sec
                i += 1
                continue

        if is_header_row(row):
            flush()
            header_buf = [row]
            # 直後の行も見出しの続きなら取り込む（レコード開始行が来るまで）
            j = i + 1
            while j < len(grid):
                nxt = list(grid[j])
                nxt_texts = [norm(c) for c in nxt]
                if not any(nxt_texts):
                    j += 1
                    continue
                first_int = next((c for c in nxt if norm(c)), None)
                if is_int_cell(first_int):
                    break
                if header_hits(nxt) or all(len(t) <= 12 for t in nxt_texts if t):
                    if detect_section(" ".join(t for t in nxt_texts if t)):
                        break
                    header_buf.append(nxt)
                    j += 1
                    continue
                break
            new_layout = Layout.from_rows(header_buf)
            if layout is None or new_layout.cols != layout.cols:
                layout = new_layout
                diag["headers"] = layout.headers
                diag["columns"] = layout.cols
            i = j
            continue

        if layout is None:
            diag["skipped_rows"] += 1
            i += 1
            continue

        sc = start_col()
        starts = False
        if sc is not None and sc < len(row):
            cell = row[sc]
            if sc == layout.cols.get("no"):
                starts = is_int_cell(cell)
                # 項番列が結合セル等で空でも、機関番号が新たに現れたら新レコード
                if not starts and "code" in layout.cols:
                    cc = layout.cols["code"]
                    starts = cc < len(row) and bool(CODE_RE.search(norm(row[cc]))) and (
                        current is None or current["_cells"].get(cc)
                    )
            else:
                starts = bool(CODE_RE.search(norm(cell)))

        if starts:
            flush()
            current = {"_cells": {}, "_section": section}
        if current is None:
            diag["skipped_rows"] += 1
            i += 1
            continue
        for c, v in enumerate(row):
            t = norm(v)
            if not t:
                continue
            if isinstance(v, (dt.datetime, dt.date)):
                current.setdefault("_dates", {}).setdefault(c, []).append(v)
            prev = current["_cells"].get(c)
            current["_cells"][c] = f"{prev}\n{t}" if prev else t
        i += 1
    flush()

    out = [build_record(r, layout) for r in records] if layout else []
    out = [r for r in out if r.get("code") or r.get("name")]
    diag["records"] = len(out)
    return out, diag


def _cell(rec: dict, layout: Layout, key: str) -> str:
    c = layout.cols.get(key)
    if c is None:
        return ""
    return rec["_cells"].get(c, "")


TITLE_RE = re.compile(r"(理事長|代表取締役社長|代表取締役|代表理事|代表社員|代表執行役|代表者|理事|会長|学長|総長|院長|社長|知事|市長|区長|町長|村長)")


def join_person(text: str) -> str | None:
    """セル幅で折り返された開設者・管理者名を1行に戻す（肩書きの前後は空白で区切る）。"""
    if not text:
        return None
    s = "".join(ln.strip() for ln in text.split("\n"))
    s = TITLE_RE.sub(r" \1 ", s)
    return re.sub(r"\s+", " ", s).strip() or None


def split_departments(text: str) -> list[str] | None:
    """診療科名を分ける。セル幅で途中改行された長い科名（「児童精神」+「科」）はつなぎ直す。"""
    tokens: list[str] = []
    lines = [ln.split() for ln in (text or "").split("\n") if ln.strip()]
    carry = None
    for words in lines:
        for i, w in enumerate(words):
            if carry is not None:
                w = carry + w
                carry = None
            last = i == len(words) - 1
            if last and w not in DEPT_ABBR and not re.search(r"(科|外来|内科|クリニック内科)$", w) and len(w) >= 2:
                carry = w
                continue
            tokens.append(w)
    if carry:
        tokens.append(carry)
    return tokens or None


def build_record(rec: dict, layout: Layout) -> dict:
    cells = rec["_cells"]
    raw = []
    for c in sorted(cells):
        h = layout.headers[c] if c < len(layout.headers) and layout.headers[c] else f"列{c + 1}"
        raw.append([h, cells[c]])

    out: dict = {"raw": raw}
    sec = rec.get("_section")
    if sec:
        cat, s = sec.split("|")
        out["section_category"] = cat
        out["section"] = s

    out["no"] = _cell(rec, layout, "no").split("\n")[0] or None

    code_txt = _cell(rec, layout, "code")
    m = CODE_RE.search(code_txt)
    if m:
        out["code"] = f"{m.group(1)},{m.group(2)},{m.group(3)}"
    elif re.fullmatch(r"\d{5,7}", squash(code_txt)):
        d = squash(code_txt).zfill(7)
        out["code"] = f"{d[:2]},{d[2:6]},{d[6]}"
    elif re.fullmatch(r"\d{10}", squash(code_txt)):
        d = squash(code_txt)[3:]
        out["code"] = f"{d[:2]},{d[2:6]},{d[6]}"

    out["name"] = re.sub(r"\s*\n\s*", "", _cell(rec, layout, "name")) or None

    addr_txt = _cell(rec, layout, "address")
    tel_txt = _cell(rec, layout, "tel")
    # 所在地と電話番号が同じ列（2段）になっている場合に分離
    tel_re = re.compile(r"(?<![\d〒])\(?0\d{1,4}\)?[-‐－ー\s]?\d{1,4}[-‐－ー\s]?\d{3,4}(?!\d)")
    same_col = layout.cols.get("tel") is not None and layout.cols.get("tel") == layout.cols.get("address")
    if same_col or not tel_txt:
        lines = addr_txt.split("\n")
        tel_lines = [ln for ln in lines if tel_re.fullmatch(squash(ln))]
        if tel_lines:
            tel_txt = tel_lines[0]
            addr_txt = "\n".join(ln for ln in lines if ln not in tel_lines)
        elif same_col:
            tel_txt = ""
    out["address_raw"] = addr_txt or None
    tm = tel_re.search(tel_txt or "")
    out["tel"] = tm.group(0) if tm else (tel_txt or None)

    founder = _cell(rec, layout, "founder")
    manager = _cell(rec, layout, "manager")
    if layout.cols.get("founder") is not None and layout.cols.get("founder") == layout.cols.get("manager"):
        parts = founder.split("\n")
        founder, manager = "\n".join(parts[:-1]) or parts[0], parts[-1] if len(parts) > 1 else ""
    out["founder"] = join_person(founder)
    out["manager"] = join_person(manager)

    table = squash(_cell(rec, layout, "table"))
    out["table_code"] = table or None

    # 日付: 指定年月日と指定期間が同じ列のこともある
    date_col = layout.cols.get("date")
    period_col = layout.cols.get("period")
    date_dates = find_dates(cells.get(date_col, "")) if date_col is not None else []
    period_dates = find_dates(cells.get(period_col, "")) if period_col is not None else []
    if date_col is not None and date_col == period_col:
        out["designated"] = date_dates[0] if date_dates else None
        out["period_end"] = date_dates[-1] if len(date_dates) > 1 else None
    else:
        out["designated"] = date_dates[0] if date_dates else None
        hdr = layout.headers[period_col] if period_col is not None else ""
        if period_dates:
            out["period_start"] = period_dates[0] if "始" in hdr or len(period_dates) > 1 else None
            out["period_end"] = period_dates[-1] if ("終" in hdr or len(period_dates) > 1) else None
            if out.get("period_start") is None and out.get("period_end") is None:
                out["period_start"] = period_dates[0]
        if not out.get("designated") and out.get("period_start"):
            out["designated"] = out["period_start"]
    if not out.get("designated"):
        # 列特定に失敗した場合はレコード全体から拾う
        all_dates = find_dates("\n".join(cells.values()))
        if all_dates:
            out["designated"] = all_dates[0]
            if len(all_dates) > 1:
                out["period_end"] = all_dates[-1]

    staff = _cell(rec, layout, "staff")
    dept = _cell(rec, layout, "dept")
    staff_re = re.compile(r"常\s*勤|非常勤|^[医歯薬]\s*\d|\(\s*\d*\s*\)")
    if layout.cols.get("staff") is not None and layout.cols.get("staff") == layout.cols.get("dept"):
        staff_lines, dept_lines = [], []
        for ln in staff.split("\n"):
            if not ln or tel_re.fullmatch(squash(ln)):
                continue
            (staff_lines if staff_re.search(ln) else dept_lines).append(ln)
        staff, dept = "\n".join(staff_lines), "\n".join(dept_lines)
    else:
        staff = "\n".join(ln for ln in staff.split("\n") if ln and not tel_re.fullmatch(squash(ln)))
    staff = re.sub(r"常\s+勤", "常勤", staff)
    staff = re.sub(r"\s+", " ", staff).strip()
    full = re.search(r"(?<!非)常勤\s*[:：]?\s*(\d+)", staff)
    part = re.search(r"非常勤\s*[:：]?\s*(\d+)", staff)
    kind = re.search(r"([医歯薬])\s*\d", staff)
    if full or part:
        who = {"医": "医師", "歯": "歯科医師", "薬": "薬剤師"}.get(kind.group(1), "") if kind else ""
        bits = [f"常勤 {full.group(1)}" if full else None, f"非常勤 {part.group(1)}" if part else None]
        out["staff"] = (f"{who} " if who else "") + "・".join(b for b in bits if b)
    else:
        out["staff"] = staff or None
    out["departments"] = split_departments(dept)

    # 病床数と登録理由、備考と現存/廃止が同じ列に入ることがあるので、値の中身で振り分ける
    beds_lines = [ln for ln in _cell(rec, layout, "beds").split("\n")
                  if ln and not REASON_RE.fullmatch(squash(ln)) and not STATUS_RE.fullmatch(squash(ln))]
    out["beds"] = " ".join(beds_lines) or None
    note_lines = [ln for ln in _cell(rec, layout, "note").split("\n")
                  if ln and not STATUS_RE.fullmatch(squash(ln)) and not REASON_RE.fullmatch(squash(ln))]
    note = "".join(note_lines)
    out["note"] = re.sub(r"(旧機関コード)\s*[:：]?\s*", r"\1：", note) or None

    allt = "\n".join(cells.values())
    reason_txt = _cell(rec, layout, "reason") + "\n" + _cell(rec, layout, "status")
    rm = REASON_RE.search(reason_txt) or REASON_RE.search(allt)
    out["reason"] = rm.group(0) if rm else None
    status_lines = [squash(ln) for ln in allt.split("\n")]
    out["status"] = next((ln for ln in status_lines if STATUS_RE.fullmatch(ln)), None)

    # 旧機関コード（遡及指定分の備考など、どの列にあっても拾う）
    om = re.search(r"旧機関コード\s*[:：]?\s*(\d{2}\s*,\s*\d{4}\s*,\s*\d)", allt)
    if om:
        out["old_code"] = squash(om.group(1))
    return out
