"""東京都の市区町村と二次保健医療圏の対応表。"""

import re
import unicodedata

# 二次保健医療圏（東京都保健医療計画）: 13圏域
MEDICAL_REGIONS = {
    "区中央部": ["千代田区", "中央区", "港区", "文京区", "台東区"],
    "区南部": ["品川区", "大田区"],
    "区西南部": ["目黒区", "世田谷区", "渋谷区"],
    "区西部": ["新宿区", "中野区", "杉並区"],
    "区西北部": ["豊島区", "北区", "板橋区", "練馬区"],
    "区東北部": ["荒川区", "足立区", "葛飾区"],
    "区東部": ["墨田区", "江東区", "江戸川区"],
    "西多摩": ["青梅市", "福生市", "羽村市", "あきる野市", "瑞穂町", "日の出町", "檜原村", "奥多摩町"],
    "南多摩": ["八王子市", "町田市", "日野市", "多摩市", "稲城市"],
    "北多摩西部": ["立川市", "昭島市", "国分寺市", "国立市", "東大和市", "武蔵村山市"],
    "北多摩南部": ["武蔵野市", "三鷹市", "府中市", "調布市", "小金井市", "狛江市"],
    "北多摩北部": ["小平市", "東村山市", "清瀬市", "東久留米市", "西東京市"],
    "島しょ": ["大島町", "利島村", "新島村", "神津島村", "三宅村", "御蔵島村", "八丈町", "青ヶ島村", "小笠原村"],
}

MUNI_TO_REGION = {m: r for r, ms in MEDICAL_REGIONS.items() for m in ms}

# 長い名前から照合（「東村山市」より先に「村山」系を誤爆しないよう、長さ降順）
_MUNIS_BY_LEN = sorted(MUNI_TO_REGION, key=len, reverse=True)

# 表記ゆれ（ケ/ヶ など）
_ALIASES = {"青ケ島村": "青ヶ島村", "青が島村": "青ヶ島村"}


def area_of(muni: str | None) -> str | None:
    if not muni:
        return None
    region = MUNI_TO_REGION.get(muni)
    if region is None:
        return None
    if region.startswith("区"):
        return "23区"
    if region == "島しょ":
        return "島しょ"
    return "多摩"


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def strip_postal(address: str) -> tuple[str | None, str]:
    """先頭の〒・郵便番号を取り除き (郵便番号, 残り) を返す。"""
    a = normalize(address).strip()
    m = re.match(r"^\s*〒?\s*(\d{3})\s*[-‐－ー―−]?\s*(\d{4})\s*", a)
    if m:
        return f"{m.group(1)}-{m.group(2)}", a[m.end():].strip()
    return None, a.lstrip("〒 ").strip()


def find_municipality(address: str) -> str | None:
    """住所の先頭（〒・東京都を除いた位置）から市区町村を特定する。

    先頭一致にしているのは、横浜市港北区のような他県住所を「港区」と誤判定しないため。
    """
    _, a = strip_postal(address)
    a = re.sub(r"\s+", "", a)
    if a.startswith("東京都"):
        a = a[3:]
    for alias, canon in _ALIASES.items():
        if a.startswith(alias):
            return canon
    for m in _MUNIS_BY_LEN:
        if a.startswith(m):
            return m
    # 「西多摩郡瑞穂町」「大島支庁」のような郡・支庁表記
    m = re.match(r"^(?:西多摩郡|[^\s]{1,4}支庁)?(.+?[町村])", a)
    if m and m.group(1) in MUNI_TO_REGION:
        return m.group(1)
    for m2 in _MUNIS_BY_LEN:
        if a.startswith("西多摩郡" + m2):
            return m2
    return None


def is_tokyo_address(address: str) -> bool:
    _, a = strip_postal(address)
    a = re.sub(r"\s+", "", a)
    if a.startswith("東京都"):
        return True
    return find_municipality(address) is not None


# 一覧表で使われる診療科名の略称（わからないものはそのまま表示する）
DEPT_ABBR = {
    "内": "内科", "外": "外科", "小": "小児科", "整外": "整形外科", "皮": "皮膚科", "ひ": "泌尿器科",
    "産婦": "産婦人科", "産": "産科", "婦": "婦人科", "眼": "眼科", "耳い": "耳鼻いんこう科", "耳": "耳鼻いんこう科",
    "精": "精神科", "心内": "心療内科", "神内": "脳神経内科", "神": "神経科", "脳外": "脳神経外科",
    "呼内": "呼吸器内科", "呼外": "呼吸器外科", "消内": "消化器内科", "消外": "消化器外科", "胃": "胃腸科",
    "循内": "循環器内科", "循": "循環器科", "心外": "心臓血管外科", "形外": "形成外科", "美外": "美容外科",
    "美皮": "美容皮膚科", "放": "放射線科", "麻": "麻酔科", "リハ": "リハビリテーション科", "アレ": "アレルギー科",
    "リウ": "リウマチ科", "病理": "病理診断科", "臨検": "臨床検査科", "救急": "救急科", "腎内": "腎臓内科",
    "糖内": "糖尿病内科", "肛外": "肛門外科", "肛": "肛門科", "乳外": "乳腺外科", "気食": "気管食道科",
    "歯": "歯科", "小歯": "小児歯科", "矯歯": "矯正歯科", "歯外": "歯科口腔外科",
}


def expand_departments(depts: list[str] | None) -> list[str] | None:
    if not depts:
        return None
    return [DEPT_ABBR.get(d, d) for d in depts]
