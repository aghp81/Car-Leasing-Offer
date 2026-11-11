"""Fetch and cache market prices from the public car.ir prices page."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
SOURCE_URL = "https://car.ir/prices"
CACHE_PATH = ROOT / "prices_cache.json"
FRESH_FOR = timedelta(hours=24)
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _digits(value: str) -> str:
    return value.translate(PERSIAN_DIGITS)


def _recent_enough(text: str) -> bool:
    """Require a visible page age and exclude prices older than one day."""
    text = _digits(text)
    age = re.search(r"(یک|\d+)\s*(دقیقه|ساعت|روز|ماه|سال)\s*پیش", text)
    if not age:
        return False
    amount = 1 if age.group(1) == "یک" else int(age.group(1))
    unit = age.group(2)
    if unit in ("ماه", "سال"):
        return False
    if unit == "روز":
        return amount <= 1
    if unit == "ساعت":
        return amount <= 24
    return True


def _market_price(cell_text: str) -> int | None:
    text = _digits(cell_text)
    match = re.search(r"([\d,٬]+)\s*تومان", text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def parse_price_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cars: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for row in soup.select("table tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        name = " ".join(cells[0].stripped_strings)
        if not name or "مدل" not in name:
            continue
        # Market price is the last cell containing a toman amount (factory is earlier).
        priced_cells = []
        for cell in cells:
            cell_text = " ".join(cell.stripped_strings)
            price = _market_price(cell_text)
            if price is not None:
                priced_cells.append((price, cell_text))
        if not priced_cells:
            continue
        price, market_cell_text = priced_cells[-1]
        if not _recent_enough(market_cell_text):
            continue
        key = (name, price)
        if key in seen:
            continue
        seen.add(key)
        age = re.search(r"(?:یک|\d+)\s*(?:دقیقه|ساعت|روز)\s*پیش", _digits(market_cell_text))
        cars.append({"name": name, "price_toman": price,
                     "source_age": age.group(0) if age else "", "source": SOURCE_URL})
    return cars


def refresh_prices() -> tuple[list[dict], bool, str]:
    """Return (cars, is_stale, fetched_at); use last cache if source is unavailable."""
    now = datetime.now(timezone.utc)
    cached = None
    if CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(cached["fetched_at"])
            if now - fetched < FRESH_FOR and cached.get("cars"):
                return cached["cars"], False, cached["fetched_at"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            cached = None

    try:
        response = requests.get(SOURCE_URL, timeout=20, headers={"User-Agent": "CarPriceTelegramBot/1.0"})
        response.raise_for_status()
        cars = parse_price_page(response.text)
        if not cars:
            raise ValueError("No recent car prices were found on the source page")
        fetched_at = now.isoformat(timespec="seconds")
        CACHE_PATH.write_text(json.dumps({"source": SOURCE_URL, "fetched_at": fetched_at, "cars": cars},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
        return cars, False, fetched_at
    except (requests.RequestException, ValueError, OSError):
        if cached and cached.get("cars"):
            return cached["cars"], True, cached.get("fetched_at", "")
        return [], True, ""
