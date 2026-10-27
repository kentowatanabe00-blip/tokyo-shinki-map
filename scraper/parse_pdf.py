"""PDF 版の一覧からレコードを抽出する（Excel が取れない・読めない場合の予備）。

1. 罫線つきの表として抽出できればそれを使う
2. できなければ、単語の座標から列とレコードの範囲を復元する
どちらも最終的には parse_grid.grid_to_records に渡す。
"""

from __future__ import annotations

import re

import pdfplumber

from .parse_grid import CODE_RE, FIELD_PATTERNS, detect_section, grid_to_records, header_hits, norm

FOOTER_RE = re.compile(r"^\s*(\d+\s*頁|[-–]\s*\d+\s*[-–]|作成日.*|令和\s*\d+年\s*\d+月\s*\d+日\s*作成)\s*$")
HEADER_WORD_RE = re.compile(r"項|番|医療機関|名称|所在地|電話|開設者|管理者|点数|指定|期間|登録|理由|現存|勤務|診療科|病床|備考|氏名")


def _lines(words, tol=2.5):
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(ln, key=lambda w: w["x0"]) for ln in lines]


def _text(line) -> str:
    return " ".join(w["text"] for w in line)


def _is_anchor(line) -> bool:
    """項番（整数）で始まり、機関番号を含む行 = レコードの基準行"""
    if not line:
        return False
    return bool(re.fullmatch(r"\d{1,5}", norm(line[0]["text"]))) and bool(CODE_RE.search(norm(_text(line))))


def _is_header_line(line) -> bool:
    t = norm(_text(line))
    if _is_anchor(line) or CODE_RE.search(t) or "一覧" in t or re.search(r"〒|\d{2,}-\d|\d+年", t):
        return False
    words = [norm(w["text"]) for w in line]
    return sum(bool(HEADER_WORD_RE.search(w)) for w in words) >= max(1, len(words) // 2)


def _segment_grid(lines: list[list[dict]]) -> list[list[str]] | None:
    """1つの区分（見出し＋レコード群）の行から、1レコード1行の表を作る。"""
    anchors = [i for i, ln in enumerate(lines) if _is_anchor(ln)]
    if not anchors:
        return None
    # 見出し帯 = 最初の基準行より前の「見出しらしい行」
    head_idx = [i for i in range(anchors[0]) if _is_header_line(lines[i])]
    if not head_idx:
        return None
    last_head = max(head_idx)
    head_words = [w for i in head_idx for w in lines[i]]
    clusters: list[dict] = []
    for w in sorted(head_words, key=lambda w: w["x0"]):
        for c in clusters:
            if w["x0"] <= c["x1"] + 1 and w["x1"] >= c["x0"] - 1:
                c["x0"], c["x1"] = min(c["x0"], w["x0"]), max(c["x1"], w["x1"])
                c["words"].append(w)
                break
        else:
            clusters.append({"x0": w["x0"], "x1": w["x1"], "words": [w]})
    clusters.sort(key=lambda c: c["x0"])
    headers = ["".join(w["text"] for w in sorted(c["words"], key=lambda w: (round(w["top"]), w["x0"]))) for c in clusters]
    if len(header_hits(headers)) < 3:
        return None
    bounds = [(clusters[i]["x1"] + clusters[i + 1]["x0"]) / 2 for i in range(len(clusters) - 1)]

    def col_of(x):
        for i, b in enumerate(bounds):
            if x < b:
                return i
        return len(bounds)

    body = [ln for ln in lines[last_head + 1:] if not FOOTER_RE.match(_text(ln))]
    anchors = [i for i, ln in enumerate(body) if _is_anchor(ln)]
    # 各行をどのレコードに属させるか: 基準行どうしの間では、行間が最も広い所で区切る
    owner = [0] * len(body)
    for k, a in enumerate(anchors):
        owner[a] = k
    for i in range(0, anchors[0]):
        owner[i] = 0
    for i in range(anchors[-1] + 1, len(body)):
        owner[i] = len(anchors) - 1
    for k in range(len(anchors) - 1):
        a, b = anchors[k], anchors[k + 1]
        if b - a <= 1:
            continue
        gaps = [(body[j + 1][0]["top"] - body[j][0]["bottom"], j) for j in range(a, b)]
        split = max(gaps)[1]  # split 行までが k、その次の行から k+1
        for j in range(a + 1, b):
            owner[j] = k if j <= split else k + 1

    rows = [[[] for _ in clusters] for _ in anchors]
    for i, ln in enumerate(body):
        for w in ln:
            c = col_of((w["x0"] + w["x1"]) / 2)
            rows[owner[i]][c].append((w["top"], w["x0"], w["text"]))
    grid: list[list[str]] = [headers]
    for r in rows:
        cells = []
        for parts in r:
            parts.sort()
            out, last_top = [], None
            for top, _, t in parts:
                if last_top is not None and abs(top - last_top) <= 2.5:
                    out[-1] += " " + t
                else:
                    out.append(t)
                last_top = top
            cells.append("\n".join(out))
        grid.append(cells)
    return grid


def _words_records(page, section, context):
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5)
    lines = _lines(words)
    segments: list[tuple[str | None, list]] = [(section, [])]
    for ln in lines:
        sec = detect_section(_text(ln))
        if sec:
            segments.append((sec, []))
        else:
            segments[-1][1].append(ln)
    recs, last = [], section
    for sec, seg in segments:
        last = sec or last
        grid = _segment_grid(seg)
        if grid:
            r, _ = grid_to_records(grid, {**context, "section": last})
            recs.extend(r)
    return recs, last


def _table_records(page, section, context):
    tables = [t for t in page.find_tables() if t.bbox and len(t.extract() or []) and max(len(r) for r in t.extract()) >= 5]
    if not tables:
        return [], section
    recs, prev_bottom = [], 0
    for t in sorted(tables, key=lambda t: t.bbox[1]):
        x0, top, x1, bottom = t.bbox
        if top - prev_bottom > 2:
            above = page.crop((0, prev_bottom, page.width, top)).extract_text() or ""
            for line in above.splitlines():
                sec = detect_section(line)
                if sec:
                    section = sec
        r, _ = grid_to_records(t.extract(), {**context, "section": section})
        recs.extend(r)
        prev_bottom = bottom
    return recs, section


def parse_pdf(path: str, context: dict | None = None) -> tuple[list[dict], dict]:
    context = dict(context or {})
    records: list[dict] = []
    diag: dict = {"pages": 0, "mode": []}
    section = context.get("section")
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            diag["pages"] += 1
            recs, sec_t = _table_records(page, section, context)
            mode = "table"
            if not recs:
                recs, sec_t = _words_records(page, section, context)
                mode = "words" if recs else "none"
            section = sec_t
            diag["mode"].append(mode)
            records.extend(recs)
    diag["records"] = len(records)
    return records, diag
