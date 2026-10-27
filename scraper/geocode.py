"""国土地理院の住所検索 API で緯度経度を求める（結果は data/geocode_cache.json に保存）。

API: https://msearch.gsi.go.jp/address-search/AddressSearch?q=<住所>
キー不要。負荷をかけないよう 1 秒以上間隔を空け、同じ住所は二度と問い合わせない。
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

import requests

API = "https://msearch.gsi.go.jp/address-search/AddressSearch"
_last_call = 0.0


def clean_address(addr: str) -> str:
    a = unicodedata.normalize("NFKC", addr or "")
    a = re.sub(r"〒?\s*\d{3}\s*[-‐－ー―]?\s*\d{4}", "", a)
    a = re.sub(r"\s+", "", a)
    a = re.sub(r"[‐－ー―−]", "-", a)
    return a


def street_part(addr: str) -> str:
    """建物名などを落として「〜丁目〜番〜号」までを取り出す。"""
    m = re.match(r"^(.*?\d+(?:丁目)?(?:[-番]\d+(?:号)?)*(?:[-番]\d+)?(?:号)?)", addr)
    return m.group(1) if m else addr


def _query(q: str, session: requests.Session):
    global _last_call
    wait = 1.1 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()
    r = session.get(API, params={"q": q}, timeout=20)
    r.raise_for_status()
    return r.json() or []


class Geocoder:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache: dict = json.loads(cache_path.read_text("utf-8")) if cache_path.exists() else {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "tokyo-hoken-shitei-map (GitHub Actions)"
        self.calls = 0

    def save(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(dict(sorted(self.cache.items())), ensure_ascii=False, indent=1), "utf-8"
        )

    def cached(self, address: str) -> dict | None:
        a = clean_address(address)
        if not a:
            return None
        return self.cache.get(a if a.startswith("東京都") else "東京都" + a)

    def locate(self, address: str, municipality: str | None) -> dict | None:
        """{"lat","lng","precision","matched"} を返す。precision は address / municipality。"""
        a = clean_address(address)
        if not a:
            return None
        full = a if a.startswith("東京都") else "東京都" + a
        if full in self.cache:
            return self.cache[full]

        result = None
        for q in dict.fromkeys([street_part(full), full]):
            try:
                hits = _query(q, self.session)
                self.calls += 1
            except Exception as e:  # noqa: BLE001
                print(f"  geocode error {q}: {e}")
                return None  # キャッシュせず次回再試行
            hit = self._pick(hits, municipality)
            if hit:
                lng, lat = hit["geometry"]["coordinates"]
                title = hit["properties"].get("title", "")
                result = {"lat": round(lat, 6), "lng": round(lng, 6), "precision": "address", "matched": title}
                break
        if result is None and municipality:
            result = self.municipality(municipality)
        self.cache[full] = result
        return result

    def municipality(self, muni: str) -> dict | None:
        key = f"@{muni}"
        if key in self.cache:
            return self.cache[key]
        try:
            hits = _query(f"東京都{muni}", self.session)
            self.calls += 1
        except Exception as e:  # noqa: BLE001
            print(f"  geocode error {muni}: {e}")
            return None
        res = None
        if hits:
            lng, lat = hits[0]["geometry"]["coordinates"]
            res = {"lat": round(lat, 6), "lng": round(lng, 6), "precision": "municipality", "matched": muni}
        self.cache[key] = res
        return res

    @staticmethod
    def _pick(hits: list, municipality: str | None):
        for h in hits:
            title = unicodedata.normalize("NFKC", h.get("properties", {}).get("title", ""))
            if "東京都" not in title:
                continue
            if municipality and municipality not in title:
                continue
            # 町名レベル以上まで一致しているもの（市区町村名だけの一致は除外）
            rest = title.split(municipality, 1)[-1] if municipality else title
            if len(rest) >= 2:
                return h
        return None
