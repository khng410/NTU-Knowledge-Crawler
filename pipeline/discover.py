"""Utilities for classifying and recording discovered NTU URLs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlsplit


RULES: dict[str, tuple[str, ...]] = {
    "ctdt": ("ctdt.ntu.edu.vn",),
    "graduate": ("pdtsaudaihoc.ntu.edu.vn",),
    "finance": ("phongkhtc.ntu.edu.vn",),
    "student_procedure": (
        "phongctsv.ntu.edu.vn",
        "pdtdaihoc.ntu.edu.vn",
    ),
    "services": (
        "thuvien.ntu.edu.vn",
        "htdnhtsv.ntu.edu.vn",
    ),
    "admission": (
        "tuyensinh.ntu.edu.vn",
        "xettuyen.ntu.edu.vn",
        "trungtamdtbd.ntu.edu.vn",
    ),
}

VIETNAM_TIMEZONE = timezone(timedelta(hours=7))


class DiscoveredURL(TypedDict):
    """JSON-serializable representation of a discovered URL."""

    url: str
    category: str
    domain: str
    retrieved_at: str


def _domain_category_index(
    rules: Mapping[str, Sequence[str]],
) -> dict[str, str]:
    index: dict[str, str] = {}
    for category, domains in rules.items():
        for domain in domains:
            normalized_domain = domain.lower().rstrip(".")
            if normalized_domain in index:
                raise ValueError(
                    f"Domain {normalized_domain!r} belongs to multiple categories"
                )
            index[normalized_domain] = category
    return index


def get_domain(url: str) -> str:
    """Return a normalized hostname from an absolute HTTP(S) URL."""

    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Expected an absolute HTTP(S) URL, got {url!r}")
    return parsed.hostname.lower().rstrip(".")


def classify_url(
    url: str,
    rules: Mapping[str, Sequence[str]] = RULES,
) -> str | None:
    """Return the category for *url*, or ``None`` when no rule matches."""

    return _domain_category_index(rules).get(get_domain(url))


def create_url_record(
    url: str,
    *,
    retrieved_at: datetime | None = None,
    rules: Mapping[str, Sequence[str]] = RULES,
) -> DiscoveredURL:
    """Create the normalized record stored for a discovered, classified URL."""

    domain = get_domain(url)
    category = _domain_category_index(rules).get(domain)
    if category is None:
        raise ValueError(f"No category configured for domain {domain!r}")

    timestamp = retrieved_at or datetime.now(VIETNAM_TIMEZONE)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=VIETNAM_TIMEZONE)
    else:
        timestamp = timestamp.astimezone(VIETNAM_TIMEZONE)

    return {
        "url": url,
        "category": category,
        "domain": domain,
        "retrieved_at": timestamp.isoformat(timespec="seconds"),
    }


def save_discovered_urls(
    urls: Iterable[str],
    destination: str | Path,
    *,
    retrieved_at: datetime | None = None,
) -> list[DiscoveredURL]:
    """Append classified URLs to a UTF-8 JSON Lines file and return the records."""

    records = [
        create_url_record(url, retrieved_at=retrieved_at)
        for url in urls
    ]
    output_path = Path(destination)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return records
