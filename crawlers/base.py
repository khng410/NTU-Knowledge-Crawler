"""Reusable keyword source discovery for later structured extractors."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from urllib.parse import urlsplit, urlunsplit

from crawlers.ctdt import now_iso
from database import KnowledgeStore
from extractors.html_extractor import parse_html
from pipeline.crawl import SourceFetcher


PERSONAL_DATA_LABELS = (
    "danh sách",
    "danh sách sinh viên",
    "danh sách nợ",
    "sinh viên nợ",
    "danh sách trúng tuyển",
    "kết quả trúng tuyển",
)


class KeywordCandidateCrawler:
    def __init__(
        self,
        store: KnowledgeStore,
        *,
        category: str,
        seeds: tuple[str, ...],
        keywords: tuple[str, ...],
        exclusions: tuple[str, ...] = (),
        direct_sources: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
        raw_directory: str = "data/raw",
    ) -> None:
        self.store = store
        self.category = category
        self.seeds = seeds
        self.keywords = keywords
        self.exclusions = exclusions + PERSONAL_DATA_LABELS
        self.direct_sources = direct_sources
        self.fetcher = SourceFetcher(store, raw_directory)
        self.stats: Counter[str] = Counter()

    @staticmethod
    def _https(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))

    def discover(self) -> dict[str, int]:
        for seed in self.seeds:
            try:
                html = self.fetcher.text(seed, category=f"{self.category}_index")
            except Exception as error:
                self.store.connection.execute(
                    "INSERT INTO crawl_errors(url, error, created_at) VALUES (?, ?, ?)",
                    (seed, str(error), now_iso()),
                )
                self.store.connection.commit()
                self.stats[f"{self.category}_errors"] += 1
                continue
            _, links = parse_html(html, seed)
            for link in links:
                label = link.text.casefold()
                matched = [keyword for keyword in self.keywords if keyword in label]
                if not matched or any(value in label for value in self.exclusions):
                    continue
                url = self._https(link.url)
                try:
                    domain = self.fetcher.validate(url)
                except ValueError:
                    continue
                candidate_id = hashlib.sha256(
                    f"{self.category}|{url}".encode("utf-8")
                ).hexdigest()
                cursor = self.store.connection.execute(
                    """INSERT OR IGNORE INTO source_candidates
                       (candidate_id, category, title, matched_keywords_json,
                        source_url, source_domain, retrieved_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        candidate_id,
                        self.category,
                        link.text,
                        json.dumps(matched, ensure_ascii=False),
                        url,
                        domain,
                        now_iso(),
                    ),
                )
                self.store.enqueue(url, f"source_candidate_{self.category}")
                self.stats[f"{self.category}_candidates_discovered"] += cursor.rowcount
        for title, url, matched in self.direct_sources:
            domain = self.fetcher.validate(url)
            candidate_id = hashlib.sha256(
                f"{self.category}|{url}".encode("utf-8")
            ).hexdigest()
            cursor = self.store.connection.execute(
                """INSERT OR IGNORE INTO source_candidates
                   (candidate_id, category, title, matched_keywords_json,
                    source_url, source_domain, retrieved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate_id,
                    self.category,
                    title,
                    json.dumps(matched, ensure_ascii=False),
                    url,
                    domain,
                    now_iso(),
                ),
            )
            self.store.enqueue(url, f"source_candidate_{self.category}")
            self.stats[f"{self.category}_candidates_discovered"] += cursor.rowcount
        self.store.connection.commit()
        return dict(self.stats)

    def crawl(self, limit: int | None = None) -> dict[str, int]:
        for item in self.store.queued([f"source_candidate_{self.category}"], limit):
            url = item["url"]
            self.store.mark_queue(url, "running", now_iso())
            try:
                suffix = urlsplit(url).path.casefold()
                source_type = (
                    "official_document"
                    if suffix.endswith((".pdf", ".doc", ".docx"))
                    else "official_unit_page"
                )
                self.fetcher.fetch(
                    url,
                    category=f"{self.category}_candidate",
                    source_type=source_type,
                )
                self.store.mark_queue(url, "done", now_iso())
                self.stats[f"{self.category}_candidates_crawled"] += 1
            except Exception as error:
                self.store.mark_queue(url, "failed", now_iso(), str(error))
                self.store.connection.execute(
                    "INSERT INTO crawl_errors(url, error, created_at) VALUES (?, ?, ?)",
                    (url, str(error), now_iso()),
                )
                self.store.connection.commit()
                self.stats[f"{self.category}_errors"] += 1
        return dict(self.stats)
