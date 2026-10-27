"""関東信越厚生局の「新規指定一覧」から東京都分を取り込み、data/ を更新する。

使い方:
  python -m scraper.update                 # 未取り込みの処理月だけ取得
  python -m scraper.update --force 2026-09 # 指定月を取り直す（all で全月）
  python -m scraper.update --reparse       # data/raw に保存済みの原本から作り直す（通信なし）
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .geocode import Geocoder
from .parse_excel import parse_zip
from .parse_pdf import parse_pdf
from .regions import MUNI_TO_REGION, area_of, expand_departments, find_municipality, strip_postal
from .wareki import reiwa_month_to_iso, to_wareki

INDEX_URL = "https://kouseikyoku.mhlw.go.jp/kantoshinetsu/chousa/shitei.html"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
RECORDS = DATA / "records.json"
META = DATA / "meta.json"
NOTIFY = ROOT / "notify.md"

UA = "tokyo-hoken-shitei-map/1.0 (+https://github.com; monthly public data collector)"
CATEGORY_BY_TABLE = {"医": "医科", "歯": "歯科", "調": "薬局", "薬": "薬局"}
CODE_DIGIT = {"医科": "1", "歯科": "3", "薬局": "4"}


def jst_now() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds")


def get(session: requests.Session, url: str) -> bytes:
    for attempt in range(3):
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            time.sleep(2)  # 相手先サーバーへの配慮
            return r.content
        except requests.RequestException as e:
            print(f"  retry {attempt + 1}: {url} ({e})")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"download failed: {url}")


def find_months(html: bytes) -> list[dict]:
    """ページ内の「令和X年Y月処理分」ごとに、東京都の新規 PDF と Excel ZIP の URL を拾う。"""
    soup = BeautifulSoup(html, "html.parser")
    months: dict[str, dict] = {}
    pat = re.compile(r"令和\s*(元|\d+)\s*年\s*(\d+)\s*月\s*処理分")
    for node in soup.find_all(string=pat):
        m = pat.search(node)
        month = reiwa_month_to_iso(m.group(1), m.group(2))
        if month in months:
            continue
        table = node.find_next("table")
        if table is None:
            continue
        entry = {"month": month, "label": m.group(0).replace(" ", ""), "pdf": None, "zip": None}
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            head = cells[0].get_text(strip=True)
            links = [a for a in tr.find_all("a", href=True) if "新規" in a.get_text()]
            if not links:
                # 列位置で判定（見出し行の「新規」列）
                continue
            href = urljoin(INDEX_URL, links[0]["href"])
            if "東京" in head:
                entry["pdf"] = href
            elif "エクセル" in head or "Excel" in head or href.lower().endswith(".zip"):
                entry["zip"] = href
        if entry["pdf"] or entry["zip"]:
            months[month] = entry
    return sorted(months.values(), key=lambda e: e["month"])


def category_of(rec: dict) -> str | None:
    t = rec.get("table_code") or ""
    for k, v in CATEGORY_BY_TABLE.items():
        if k in t:
            return v
    return rec.get("section_category")


def enrich(rec: dict, month_entry: dict, source: str, source_url: str) -> dict:
    cat = category_of(rec)
    postal, addr = strip_postal(rec.get("address_raw") or "")
    addr = re.sub(r"\s*\n\s*", "", addr).strip()
    addr = re.sub(r"(?<=\d)[‐－ー―−](?=\d)", "-", addr)
    muni = find_municipality(addr)
    code = rec.get("code")
    code10 = None
    if code and cat in CODE_DIGIT:
        code10 = "13" + CODE_DIGIT[cat] + code.replace(",", "")
    section = rec.get("section") or ("遡及指定分" if rec.get("old_code") else None)
    ident = "|".join([month_entry["month"], cat or "", code or "", rec.get("name") or "", rec.get("no") or ""])
    out = {
        "id": hashlib.sha1(ident.encode()).hexdigest()[:12],
        "month": month_entry["month"],
        "month_label": month_entry["label"],
        "category": cat,
        "section": section,
        "retroactive": section == "遡及指定分",
        "no": rec.get("no"),
        "code": code,
        "code10": code10,
        "name": rec.get("name"),
        "postal": postal,
        "address": addr or None,
        "municipality": muni,
        "area": area_of(muni),
        "region": MUNI_TO_REGION.get(muni) if muni else None,
        "tel": rec.get("tel"),
        "founder": rec.get("founder"),
        "manager": rec.get("manager"),
        "designated": rec.get("designated"),
        "designated_wareki": to_wareki(rec.get("designated")),
        "period_start": rec.get("period_start"),
        "period_end": rec.get("period_end"),
        "staff": rec.get("staff"),
        "departments": rec.get("departments"),
        "departments_full": expand_departments(rec.get("departments")),
        "beds": rec.get("beds"),
        "reason": rec.get("reason"),
        "status": rec.get("status"),
        "old_code": rec.get("old_code"),
        "note": rec.get("note"),
        "source": source,
        "source_url": source_url,
        "raw": rec.get("raw"),
    }
    return out


def parse_month(month_dir: Path, entry: dict) -> tuple[list[dict], dict]:
    """保存済み原本から1か月分を解析。Excel を優先し、だめなら PDF。"""
    diag: dict = {}
    results: dict[str, list[dict]] = {}
    zip_path = month_dir / "shinki.zip"
    pdf_path = month_dir / "shinki_tokyo.pdf"
    if zip_path.exists():
        try:
            recs, d = parse_zip(zip_path.read_bytes())
            results["excel"] = recs
            diag["excel"] = d
        except Exception as e:  # noqa: BLE001
            diag["excel_error"] = repr(e)
    if pdf_path.exists():
        try:
            recs, d = parse_pdf(str(pdf_path))
            results["pdf"] = recs
            diag["pdf"] = {k: v for k, v in d.items() if k != "page_diags"}
        except Exception as e:  # noqa: BLE001
            diag["pdf_error"] = repr(e)

    def quality(recs):
        if not recs:
            return 0
        filled = sum(bool(r.get("code")) + bool(r.get("name")) + bool(r.get("address_raw")) for r in recs)
        return filled / (3 * len(recs)) * len(recs)

    source = max(results, key=lambda k: (quality(results[k]), k == "excel"), default=None)
    if source is None or not results[source]:
        return [], {**diag, "source": None}
    if len(results) == 2 and len(results["excel"]) != len(results["pdf"]):
        diag["count_mismatch"] = {k: len(v) for k, v in results.items()}
    url = entry.get("zip") if source == "excel" else entry.get("pdf")
    out = [enrich(r, entry, source, url) for r in results[source]]
    return out, {**diag, "source": source}


def write_notify(new_months: list[str], records: list[dict]):
    lines = []
    for m in new_months:
        recs = [r for r in records if r["month"] == m]
        if not recs:
            continue
        label = recs[0]["month_label"]
        fresh = [r for r in recs if not r["retroactive"]]
        lines.append(f"## {label}（東京都）: {len(recs)}件（うち指定分 {len(fresh)}件）\n")
        by_region: dict[str, int] = {}
        for r in recs:
            by_region[r.get("region") or "不明"] = by_region.get(r.get("region") or "不明", 0) + 1
        lines.append("医療圏別: " + " / ".join(f"{k} {v}" for k, v in sorted(by_region.items(), key=lambda x: -x[1])) + "\n")
        if len(new_months) <= 2:
            lines.append("| 区分 | 名称 | 所在地 | 指定日 | 理由 |")
            lines.append("|---|---|---|---|---|")
            for r in sorted(recs, key=lambda r: (r["retroactive"], r.get("region") or "", r.get("municipality") or "")):
                cat = (r.get("category") or "") + ("（遡及）" if r["retroactive"] else "")
                lines.append(
                    f"| {cat} | {r.get('name') or ''} | {r.get('address') or ''} | {r.get('designated_wareki') or ''} | {r.get('reason') or ''} |"
                )
            lines.append("")
    if lines:
        NOTIFY.write_text("\n".join(lines), "utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", help="取り直す処理月（YYYY-MM）または all")
    ap.add_argument("--reparse", action="store_true", help="通信せず data/raw から作り直す")
    ap.add_argument("--no-geocode", action="store_true")
    args = ap.parse_args(argv)

    DATA.mkdir(exist_ok=True)
    meta = json.loads(META.read_text("utf-8")) if META.exists() else {"months": {}}
    records = json.loads(RECORDS.read_text("utf-8")) if RECORDS.exists() else []
    session = requests.Session()
    session.headers["User-Agent"] = UA

    if args.reparse:
        entries = [
            {"month": m, "label": v.get("label", m), "pdf": v.get("pdf"), "zip": v.get("zip")}
            for m, v in sorted(meta["months"].items())
        ]
        targets = entries
    else:
        html = get(session, INDEX_URL)
        entries = find_months(html)
        print(f"index: {len(entries)} months found: {[e['month'] for e in entries]}")
        if not entries:
            print("ERROR: 処理月の一覧を見つけられませんでした（ページ構成が変わった可能性）", file=sys.stderr)
            sys.exit(2)
        targets = []
        for e in entries:
            known = meta["months"].get(e["month"])
            forced = args.force in ("all", e["month"])
            # URL が差し替わった（-1 付きの再掲など）場合も取り直す
            changed = known and (known.get("pdf") != e["pdf"] or known.get("zip") != e["zip"])
            if forced or not known or changed:
                targets.append(e)

    if not targets:
        print("no new months")
        meta["checked_at"] = jst_now()
        META.write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
        return

    geocoder = None if args.no_geocode else Geocoder(DATA / "geocode_cache.json")
    new_months = []
    for e in targets:
        print(f"== {e['label']} ({e['month']})")
        mdir = RAW / e["month"]
        mdir.mkdir(parents=True, exist_ok=True)
        if not args.reparse:
            if e.get("zip"):
                (mdir / "shinki.zip").write_bytes(get(session, e["zip"]))
            if e.get("pdf"):
                (mdir / "shinki_tokyo.pdf").write_bytes(get(session, e["pdf"]))
        recs, diag = parse_month(mdir, e)
        print(f"  source={diag.get('source')} records={len(recs)}")
        if diag.get("count_mismatch"):
            print(f"  WARNING Excel/PDF 件数不一致: {diag['count_mismatch']}")
        if not recs:
            print("  WARNING: 0 件。既存データは残します。diagnostics を確認してください。")
            meta["months"].setdefault(e["month"], {}).update({"diagnostics": diag, "error": "no records"})
            continue
        if geocoder:
            for r in recs:
                if r.get("address"):
                    g = geocoder.locate(r["address"], r.get("municipality"))
                    if g:
                        r.update(lat=g["lat"], lng=g["lng"], geo_precision=g["precision"])
            geocoder.save()
        else:
            cached = Geocoder(DATA / "geocode_cache.json")
            for r in recs:
                g = cached.cached(r.get("address") or "")
                if g:
                    r.update(lat=g["lat"], lng=g["lng"], geo_precision=g["precision"])

        was_known = e["month"] in meta["months"] and not meta["months"][e["month"]].get("error")
        records = [r for r in records if r["month"] != e["month"]] + recs
        meta["months"][e["month"]] = {
            "label": e["label"],
            "pdf": e.get("pdf"),
            "zip": e.get("zip"),
            "count": len(recs),
            "fetched_at": jst_now(),
            "diagnostics": diag,
        }
        if not was_known:
            new_months.append(e["month"])

    records.sort(key=lambda r: (r["month"], r.get("category") or "", int(r["no"]) if (r.get("no") or "").isdigit() else 0), reverse=False)
    RECORDS.write_text(json.dumps(records, ensure_ascii=False, indent=0), "utf-8")
    meta["checked_at"] = jst_now()
    meta["updated_at"] = jst_now()
    meta["total"] = len(records)
    meta["source_page"] = INDEX_URL
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    if new_months and not args.reparse:
        write_notify(new_months, records)
    print(f"done. total={len(records)} new_months={new_months} geocode_calls={geocoder.calls if geocoder else 0}")


if __name__ == "__main__":
    main()
