"""実物の PDF（令和8年9月処理分・東京都）での解析結果を確認する。"""

from collections import Counter
from pathlib import Path

from scraper.parse_pdf import parse_pdf
from scraper.update import enrich

PDF = Path(__file__).parent / "fixtures" / "131shinki_tokyo_r0810.pdf"


def test_real_pdf():
    recs, diag = parse_pdf(str(PDF))
    out = [enrich(r, {"month": "2026-09", "label": "令和8年9月処理分"}, "pdf", "x") for r in recs]
    # PDF 内の区分ごとの件数（医科 36+17、歯科 10+13、薬局 12+10）
    assert Counter((r["category"], r["section"]) for r in out) == {
        ("医科", "指定分"): 36, ("医科", "遡及指定分"): 17, ("歯科", "指定分"): 10,
        ("歯科", "遡及指定分"): 13, ("薬局", "指定分"): 12, ("薬局", "遡及指定分"): 10,
    }
    assert all(r["municipality"] and r["tel"] and r["designated"] and r["reason"] and r["status"] for r in out)
    first = out[0]
    assert first["code"] == "01,3834,7"
    assert first["address"].startswith("千代田区外神田1-17-6")
    assert first["designated"] == "2026-10-01" and first["period_end"] == "2032-09-30"
    assert first["staff"] == "医師 常勤 2"
    second = out[1]
    assert second["departments_full"] == ["心療内科", "精神科", "思春期精神科", "児童精神科"]
    third = out[2]
    assert third["founder"] == "医療法人社団CFI 理事長 田島 敬也"
    assert third["manager"] == "谷川 万里子"
    retro = [r for r in out if r["retroactive"]]
    assert all(r["old_code"] for r in retro if r["reason"] in ("交代", "移動"))
