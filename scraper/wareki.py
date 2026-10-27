"""和暦・各種日付表記を ISO 形式に変換する。"""

import datetime as dt
import re
import unicodedata

ERAS = {
    "令和": 2018, "R": 2018,
    "平成": 1988, "H": 1988,
    "昭和": 1925, "S": 1925,
}

_ERA_RE = re.compile(
    r"(令和|平成|昭和|R|H|S)\s*(元|\d{1,2})\s*[年.\-/／]\s*(\d{1,2})\s*[月.\-/／]\s*(\d{1,2})\s*日?"
)
_ISO_RE = re.compile(r"(20\d{2}|19\d{2})\s*[年.\-/／]\s*(\d{1,2})\s*[月.\-/／]\s*(\d{1,2})\s*日?")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def find_dates(value) -> list[str]:
    """文字列（または datetime）に含まれる日付をすべて ISO 文字列で返す。"""
    if value is None:
        return []
    if isinstance(value, (dt.datetime, dt.date)):
        d = value.date() if isinstance(value, dt.datetime) else value
        return [d.isoformat()]
    s = _norm(value)
    found: list[tuple[int, str]] = []
    for m in _ERA_RE.finditer(s):
        era, y, mo, d = m.groups()
        year = ERAS[era] + (1 if y == "元" else int(y))
        try:
            found.append((m.start(), dt.date(year, int(mo), int(d)).isoformat()))
        except ValueError:
            pass
    for m in _ISO_RE.finditer(s):
        y, mo, d = m.groups()
        try:
            found.append((m.start(), dt.date(int(y), int(mo), int(d)).isoformat()))
        except ValueError:
            pass
    found.sort()
    return [d for _, d in found]


def to_wareki(iso: str | None) -> str | None:
    if not iso:
        return None
    d = dt.date.fromisoformat(iso)
    if d >= dt.date(2019, 5, 1):
        y = d.year - 2018
        return f"令和{'元' if y == 1 else y}年{d.month}月{d.day}日"
    if d >= dt.date(1989, 1, 8):
        y = d.year - 1988
        return f"平成{'元' if y == 1 else y}年{d.month}月{d.day}日"
    return f"昭和{d.year - 1925}年{d.month}月{d.day}日"


def reiwa_month_to_iso(era_year: str, month: str) -> str:
    """「令和8年9月」→ "2026-09" """
    y = 2018 + (1 if era_year == "元" else int(era_year))
    return f"{y:04d}-{int(month):02d}"
