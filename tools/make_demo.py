"""架空のサンプルデータを埋め込んだプレビュー用ページを作る（実データではありません）。

python tools/make_demo.py <出力先.html> [実物の新規指定PDF]
"""

import datetime as dt
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.regions import MUNI_TO_REGION, area_of, expand_departments  # noqa: E402
from scraper.wareki import to_wareki  # noqa: E402

# 市区町村のおおよその中心（プレビュー用）
CENTER = {
    "千代田区": (35.694, 139.754), "中央区": (35.670, 139.772), "港区": (35.658, 139.751), "新宿区": (35.694, 139.703),
    "文京区": (35.708, 139.752), "台東区": (35.713, 139.780), "墨田区": (35.711, 139.801), "江東区": (35.673, 139.817),
    "品川区": (35.609, 139.730), "目黒区": (35.641, 139.698), "大田区": (35.561, 139.716), "世田谷区": (35.646, 139.653),
    "渋谷区": (35.664, 139.698), "中野区": (35.708, 139.664), "杉並区": (35.700, 139.637), "豊島区": (35.726, 139.716),
    "北区": (35.753, 139.734), "荒川区": (35.736, 139.783), "板橋区": (35.751, 139.709), "練馬区": (35.736, 139.652),
    "足立区": (35.775, 139.804), "葛飾区": (35.744, 139.847), "江戸川区": (35.707, 139.868), "八王子市": (35.656, 139.324),
    "立川市": (35.714, 139.408), "武蔵野市": (35.718, 139.566), "三鷹市": (35.683, 139.560), "府中市": (35.669, 139.478),
    "調布市": (35.652, 139.541), "町田市": (35.548, 139.446), "小平市": (35.729, 139.478), "西東京市": (35.726, 139.538),
    "青梅市": (35.788, 139.276), "多摩市": (35.637, 139.446), "日野市": (35.671, 139.395), "国分寺市": (35.711, 139.462),
    "大島町": (34.750, 139.355), "東村山市": (35.754, 139.468), "国立市": (35.684, 139.441),
}
TOWNS = ["本町", "東町", "緑が丘", "中央", "南口", "栄町", "桜台", "富士見", "旭町", "若葉"]
DEPTS = {
    "医科": [["内", "小", "アレ"], ["皮"], ["眼"], ["整外", "リハ"], ["精", "心内"], ["耳い"], ["内", "消内", "外"], ["産婦"], ["小"]],
    "歯科": [["歯", "小歯"], ["歯", "矯歯"], ["歯", "歯外"]],
    "薬局": [None],
}
PREFIX = ["あおば", "みなと", "さくら", "ひだまり", "つばさ", "かえで", "ことり", "ひかり", "そよかぜ", "いずみ", "わかば", "ほしの"]
KIND = {"医科": ["内科クリニック", "こどもクリニック", "皮ふ科", "眼科", "整形外科", "メンタルクリニック", "耳鼻咽喉科"],
        "歯科": ["歯科医院", "デンタルクリニック", "こども歯科"], "薬局": ["薬局", "調剤薬局"]}
SURNAMES = ["青木", "石田", "上野", "江口", "大森", "加納", "木下", "久保", "小池", "坂本", "島田", "杉本"]
GIVEN = ["一郎", "花子", "健", "美咲", "誠", "恵", "大輔", "由紀"]


def person(r):
    return f"{r.choice(SURNAMES)}　{r.choice(GIVEN)}"


def write_page(records, meta, out):
    page = (Path(__file__).resolve().parent.parent / "site" / "index.html").read_text("utf-8")
    seed = json.dumps({"records": records, "meta": meta}, ensure_ascii=False).replace("</", "<\\/")
    page = page.replace("</body>", f'<script id="seed" type="application/json">{seed}</script>\n</body>')
    # main() より前に seed を置く
    page = page.replace('<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/', f'<script id="seed" type="application/json">{seed}</script>\n<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/', 1)
    page = page.replace(f'<script id="seed" type="application/json">{seed}</script>\n</body>', "</body>")
    # Artifact 用: 文書の外枠タグを外す
    page = re.sub(r"<!doctype html>\s*<html[^>]*>\s*<head>\s*<meta charset=\"utf-8\">\s*", "", page, flags=re.I)
    page = page.replace("</head>\n<body>\n", "").replace("</body>\n</html>\n", "")
    page = page.replace('<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n', "")
    Path(out).write_text(page, "utf-8")


def main_real(pdf, out):
    """実物の PDF から作る。位置は通信できないため市区町村の代表点（点線の丸）で表示する。"""
    from scraper.parse_pdf import parse_pdf
    from scraper.update import enrich

    m = re.search(r"_r(\d{2})(\d{2})", Path(pdf).name)
    pub_y, pub_m = 2018 + int(m.group(1)), int(m.group(2))
    y, mo = (pub_y, pub_m - 1) if pub_m > 1 else (pub_y - 1, 12)
    entry = {"month": f"{y}-{mo:02d}", "label": f"令和{y - 2018}年{mo}月処理分"}
    url = f"https://kouseikyoku.mhlw.go.jp/kantoshinetsu/{Path(pdf).name}"
    recs, _ = parse_pdf(pdf)
    records = [enrich(r, entry, "pdf", url) for r in recs]
    for r in records:
        c = CENTER.get(r["municipality"])
        if c:
            r.update(lat=c[0], lng=c[1], geo_precision="municipality")
    meta = {"demo": True, "demo_note": f"{entry['label']}の実データ（{len(records)}件）で表示しています。プレビューでは位置を市区町村の代表点に置いています。GitHub で公開すると住所から番地まで求めて表示します。",
            "updated_at": "2026-10-05T09:23:00+09:00", "months": {entry["month"]: {"label": entry["label"]}}}
    write_page(records, meta, out)
    print(f"{len(records)} records -> {out}")


def main(out):
    r = random.Random(7)
    months = [("2026-07", "令和8年7月処理分", "r0808"), ("2026-08", "令和8年8月処理分", "r0809"), ("2026-09", "令和8年9月処理分", "r0810")]
    weights = {m: (1 if m in CENTER else 0) for m in MUNI_TO_REGION}
    munis = [m for m in weights if weights[m]]
    records = []
    for month, label, suf in months:
        y, mo = map(int, month.split("-"))
        nmo = 1 if mo == 12 else mo + 1
        nyear = y + (mo == 12)
        n = r.randint(70, 105)
        for i in range(n):
            cat = r.choices(["医科", "歯科", "薬局"], [0.55, 0.22, 0.23])[0]
            retro = r.random() < 0.3
            muni = r.choices(munis, [3 if m.endswith("区") else 1 for m in munis])[0]
            lat0, lng0 = CENTER[muni]
            approx = r.random() < 0.06
            lat, lng = (lat0, lng0) if approx else (lat0 + r.uniform(-.018, .018), lng0 + r.uniform(-.022, .022))
            town = r.choice(TOWNS)
            addr = f"{muni}{town}{r.randint(1, 5)}-{r.randint(1, 30)}-{r.randint(1, 20)}" + (f" {r.choice(PREFIX)}ビル{r.randint(1, 6)}F" if r.random() < .5 else "")
            name = f"{r.choice(PREFIX)}{r.choice(KIND[cat])}" + (f" {town}店" if cat == "薬局" else "")
            corp = r.random() < .5
            founder = f"医療法人社団{r.choice(PREFIX)}会　理事長　{person(r)}" if corp and cat != "薬局" else (f"株式会社{r.choice(PREFIX)}ファーマ　代表取締役　{person(r)}" if cat == "薬局" else person(r))
            manager = person(r) if corp or cat == "薬局" else founder
            designated = f"{nyear}-{nmo:02d}-01" if not retro else f"{y}-{mo:02d}-01"
            dd = dt.date.fromisoformat(designated)
            end = (dt.date(dd.year + 6, dd.month, 1) - dt.timedelta(days=1)).isoformat()
            code = f"{r.randint(1, 99):02d},{r.randint(1000, 9999)},{r.randint(0, 9)}"
            digit = {"医科": "1", "歯科": "3", "薬局": "4"}[cat]
            depts = r.choice(DEPTS[cat])
            staff = None if cat == "薬局" else f"常勤 {r.randint(1, 3)}" + (f"／非常勤 {r.randint(1, 4)}" if r.random() < .4 else "")
            if cat == "薬局":
                staff = f"薬 常勤 {r.randint(1, 3)}"
            reason = r.choice(["交代", "移動", "組織変更"]) if retro else "新規"
            postal = f"1{r.randint(0, 9)}{r.randint(0, 9)}-{r.randint(0, 9999):04d}"
            old = f"{r.randint(1, 99):02d},{r.randint(1000, 9999)},{r.randint(0, 9)}" if retro else None
            rec = {
                "id": f"{month}-{i}", "month": month, "month_label": label, "category": cat,
                "section": "遡及指定分" if retro else "指定分", "retroactive": retro, "no": str(i + 1),
                "code": code, "code10": "13" + digit + code.replace(",", ""), "name": name, "postal": postal,
                "address": addr, "municipality": muni, "area": area_of(muni), "region": MUNI_TO_REGION[muni],
                "tel": f"0{r.choice(['3', '42'])}-{r.randint(100, 9999)}-{r.randint(1000, 9999)}",
                "founder": founder, "manager": manager, "designated": designated, "designated_wareki": to_wareki(designated),
                "period_end": end, "staff": staff, "departments": depts, "departments_full": expand_departments(depts), "beds": None, "reason": reason, "status": "現存",
                "old_code": old, "note": f"旧機関コード：{old}" if old else None,
                "source": "pdf", "source_url": f"https://kouseikyoku.mhlw.go.jp/kantoshinetsu/131shinki_tokyo_{suf}.pdf",
                "lat": round(lat, 6), "lng": round(lng, 6), "geo_precision": "municipality" if approx else "address",
            }
            rec["raw"] = [
                ["項番", rec["no"]], ["医療機関番号", code], ["医療機関名称", name], ["医療機関所在地", f"〒{postal}\n{addr}"],
                ["電話番号", rec["tel"]], ["開設者氏名", founder], ["管理者氏名", manager],
                ["点数表", {"医科": "医", "歯科": "歯", "薬局": "調"}[cat]],
                ["指定年月日／指定期間終", f"{to_wareki(designated)}\n{to_wareki(end)}"],
                ["勤務医数／診療科名", " ".join(filter(None, [staff, "　".join(depts or [])]))],
                ["備考", rec["note"] or ""], ["登録理由／現存", f"{reason}\n現存"],
            ]
            records.append(rec)
    meta = {"demo": True, "updated_at": "2026-10-05T09:23:00+09:00", "checked_at": "2026-10-28T09:23:00+09:00",
            "months": {m: {"label": l} for m, l, _ in months}}
    write_page(records, meta, out)
    print(f"{len(records)} records -> {out}")


if __name__ == "__main__":
    if len(sys.argv) > 2:
        main_real(sys.argv[2], sys.argv[1])
    else:
        main(sys.argv[1])
