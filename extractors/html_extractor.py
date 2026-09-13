"""Dependency-free extraction helpers for official NTU HTML pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, urljoin


BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th"}


@dataclass(frozen=True)
class Link:
    text: str
    url: str


class _DocumentParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.blocks: list[str] = []
        self.links: list[Link] = []
        self._block_parts: list[str] | None = None
        self._link_parts: list[str] | None = None
        self._link_url: str | None = None
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        attributes = dict(attrs)
        if tag in BLOCK_TAGS and self._block_parts is None:
            self._block_parts = []
        if tag == "a" and attributes.get("href"):
            self._link_parts = []
            self._link_url = quote(
                urljoin(self.base_url, attributes["href"] or ""),
                safe=":/%?=&+#",
            )

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag == "a" and self._link_url is not None:
            text = _clean(" ".join(self._link_parts or []))
            if text:
                self.links.append(Link(text=text, url=self._link_url))
            self._link_parts = None
            self._link_url = None
        if tag in BLOCK_TAGS and self._block_parts is not None:
            text = _clean(" ".join(self._block_parts))
            if text:
                self.blocks.append(text)
            self._block_parts = None

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._block_parts is not None:
            self._block_parts.append(data)
        if self._link_parts is not None:
            self._link_parts.append(data)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_html(html: str, base_url: str) -> tuple[list[str], list[Link]]:
    parser = _DocumentParser(base_url)
    parser.feed(html)
    return parser.blocks, parser.links


def section(blocks: list[str], heading_pattern: str, next_heading_pattern: str) -> str | None:
    """Return explicit blocks after a heading and before the next numbered heading."""

    start = next(
        (index for index, value in enumerate(blocks) if re.search(heading_pattern, value, re.I)),
        None,
    )
    if start is None:
        return None
    selected: list[str] = []
    for value in blocks[start + 1 :]:
        if re.search(next_heading_pattern, value, re.I):
            break
        selected.append(value)
    return "\n".join(selected) or None


def year_from_text(value: str) -> int | None:
    years = re.findall(r"\b(?:19|20)\d{2}\b", value)
    return int(years[-1]) if years else None


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table" and self._table is None:
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(_clean(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def parse_tables(html: str) -> list[list[list[str]]]:
    parser = _TableParser()
    parser.feed(html)
    return parser.tables
