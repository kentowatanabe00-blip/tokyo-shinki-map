"""update.py を通信なしで通しで動かすテスト（索引ページ・原本はすべて合成）。"""

import json

import openpyxl

import scraper.update as U
from tests.test_parsers import _pdf, _two_row_sheet, _wb_bytes, _zip_of

INDEX = """
<html><body>
<h3>保険医療機関・保険薬局の新規指定・廃止・辞退・取消一覧</h3>
<p>令和8年9月処理分</p>
<table>
<tr><th>都県</th><th>新規</th><th>廃止</th><th>辞退</th><th>取消</th></tr>
<tr><td>埼玉県</td><td><a href="/kantoshinetsu/111shinki_saitama_r0810.pdf">新規（PDF）</a></td><td><a href="/x.pdf">廃止（PDF）</a></td><td>該当なし</td><td>該当なし</td></tr>
<tr><td>東京都</td><td><a href="/kantoshinetsu/131shinki_tokyo_r0810.pdf">新規（PDF）［328KB］</a></td><td><a href="/kantoshinetsu/132haishi_tokyo_r0810.pdf">廃止（PDF）</a></td><td>該当なし</td><td>該当なし</td></tr>
<tr><td>各都県分<br>エクセルデータ</td><td><a href="/kantoshinetsu/shinki_r0810-1.zip">新規（ZIP）</a></td><td><a href="/kantoshinetsu/haishi_r0810.zip">廃止（ZIP）</a></td><td>該当なし</td><td>該当なし</td></tr>
</table>
<p>令和8年8月処理分</p>
<table>
<tr><td>東京都</td><td><a href="/kantoshinetsu/131shinki_tokyo_r0809.pdf">新規（PDF）</a></td></tr>
<tr><td>各都県分<br>エクセルデータ</td><td><a href="/kantoshinetsu/shinki_r0809.zip">新規（ZIP）</a></td></tr>
</table>
</body></html>
"""


def test_end_to_end(tmp_path, monkeypatch):
    wb = openpyxl.Workbook()
    _two_row_sheet(wb.active)
    zbytes = _zip_of({"131shinki_tokyo.xlsx": _wb_bytes(wb)})
    pdfp = tmp_path / "x.pdf"
    _pdf(pdfp, ruled=False)
    files = {"shitei.html": INDEX.encode(), ".zip": zbytes, ".pdf": pdfp.read_bytes()}
    calls = []

    def fake_get(session, url):
        calls.append(url)
        for k, v in files.items():
            if url.endswith(k):
                return v
        raise AssertionError(url)

    monkeypatch.setattr(U, "get", fake_get)
    for name in ("DATA", "RAW", "RECORDS", "META", "NOTIFY"):
        pass
    monkeypatch.setattr(U, "DATA", tmp_path / "data")
    monkeypatch.setattr(U, "RAW", tmp_path / "data" / "raw")
    monkeypatch.setattr(U, "RECORDS", tmp_path / "data" / "records.json")
    monkeypatch.setattr(U, "META", tmp_path / "data" / "meta.json")
    monkeypatch.setattr(U, "NOTIFY", tmp_path / "notify.md")

    entries = U.find_months(INDEX.encode())
    assert [e["month"] for e in entries] == ["2026-08", "2026-09"]
    assert entries[1]["pdf"].endswith("131shinki_tokyo_r0810.pdf")
    assert entries[1]["zip"].endswith("shinki_r0810-1.zip")

    U.main(["--no-geocode"])
    recs = json.loads((tmp_path / "data" / "records.json").read_text())
    assert len(recs) == 6  # 2か月 × 3件
    r = next(r for r in recs if r["code"] == "06,1234,5" and r["month"] == "2026-09")
    assert r["municipality"] == "調布市" and r["region"] == "北多摩南部" and r["area"] == "多摩"
    assert r["category"] == "医科" and r["code10"] == "1310612345"
    assert r["source"] == "excel" and r["postal"] == "182-0024"
    assert (tmp_path / "notify.md").exists()

    # 2回目は新しい月がないので何も取らない（索引ページのみ）
    calls.clear()
    (tmp_path / "notify.md").unlink()
    U.main(["--no-geocode"])
    assert calls == [U.INDEX_URL]
    assert not (tmp_path / "notify.md").exists()
