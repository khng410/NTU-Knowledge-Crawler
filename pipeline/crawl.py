"""Shared, whitelist-enforcing HTTP source fetcher."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from crawlers.ctdt import now_iso
from database import KnowledgeStore


def load_approved_domains(path: str | Path = "config/sources.yaml") -> set[str]:
    domains: set[str] = set()
    active = False
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == "allowed_domains:" or line == "legal_sources:":
            active = True
            continue
        if active and line.startswith("- "):
            domains.add(line[2:].strip().lower().rstrip("."))
        elif line and not line.startswith("#"):
            active = False
    return domains


def content_hash(content: bytes, content_type: str) -> str:
    hash_content = content
    if content_type == "text/html":
        html = content.decode("utf-8-sig", errors="replace")
        html = re.sub(
            r"<input\b[^>]*(?:__VIEWSTATE|__EVENTVALIDATION|__VIEWSTATEGENERATOR|__RequestVerificationToken)[^>]*>",
            "",
            html,
            flags=re.I,
        )
        # Visitor counters and the rendered current date change on every request,
        # but are not source-document revisions.  Keep the surrounding markup so
        # a genuine page change still produces a new semantic hash.
        html = re.sub(
            r"(<li\b[^>]*class=[\"'][^\"']*(?:today|online-now|online-day|online-month|online-all)[^\"']*[\"'][^>]*>).*?(</li>)",
            r"\1[DYNAMIC_COUNTER]\2",
            html,
            flags=re.I | re.S,
        )
        html = re.sub(
            r"((?:Hôm nay|Hôm qua|Tháng này|Năm nay|Tổng)\s*:</span>\s*<span\b[^>]*>)[\d.]+",
            r"\1[DYNAMIC_COUNTER]",
            html,
            flags=re.I,
        )
        # Some NTU DNN pages return a pagination widget from an unrelated
        # category nondeterministically. It is navigation chrome, not page data.
        html = re.sub(
            r"<div\b[^>]*class=[\"'][^\"']*mbp_pagination[^\"']*[\"'][^>]*>.*?</div>",
            "<div class=\"mbp_pagination\">[DYNAMIC_PAGINATION]</div>",
            html,
            flags=re.I | re.S,
        )
        hash_content = html.encode("utf-8")
    return hashlib.sha256(hash_content).hexdigest()


class SourceFetcher:
    def __init__(
        self,
        store: KnowledgeStore,
        raw_directory: str | Path = "data/raw",
        *,
        sources_config: str | Path = "config/sources.yaml",
        timeout: float = 30,
        retries: int = 2,
    ) -> None:
        self.store = store
        self.raw_directory = Path(raw_directory)
        self.approved_domains = load_approved_domains(sources_config)
        self.timeout = timeout
        self.retries = retries
        default_paths = ssl.get_default_verify_paths()
        system_ca = Path("/etc/ssl/cert.pem")
        self.ssl_context = (
            ssl.create_default_context(cafile=str(system_ca))
            if default_paths.cafile is None and system_ca.exists()
            else ssl.create_default_context()
        )

    def validate(self, url: str) -> str:
        parsed = urlsplit(url)
        domain = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or domain not in self.approved_domains:
            raise ValueError(f"URL is outside approved HTTPS sources: {url!r}")
        return domain

    def fetch(
        self,
        url: str,
        *,
        category: str,
        source_type: str,
    ) -> tuple[bytes, bool]:
        domain = self.validate(url)
        request = Request(url, headers={"User-Agent": "NTUKnowledgeCrawler/0.1"})
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout, context=self.ssl_context) as response:
                    self.validate(response.geturl())
                    content = response.read()
                    content_type = response.headers.get_content_type()
                break
            except (HTTPError, URLError, TimeoutError) as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(0.5 * (2**attempt))
        else:
            assert last_error is not None
            raise last_error

        raw_digest = hashlib.sha256(content).hexdigest()
        digest = content_hash(content, content_type)
        extension = {
            "application/json": ".json",
            "application/pdf": ".pdf",
            "text/html": ".html",
        }.get(content_type, Path(urlsplit(url).path).suffix or ".bin")
        target = self.raw_directory / category / f"{raw_digest}{extension}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
        changed = self.store.save_source(
            {
                "url": url,
                "domain": domain,
                "source_type": source_type,
                "category": category,
                "content_hash": digest,
                "raw_path": str(target),
                "status": "success",
                "retrieved_at": now_iso(),
            }
        )
        return content, changed

    def json(self, url: str, *, category: str, source_type: str = "official_api") -> object:
        content, _ = self.fetch(url, category=category, source_type=source_type)
        return json.loads(content.decode("utf-8-sig"))

    def text(self, url: str, *, category: str, source_type: str = "official_unit_page") -> str:
        content, _ = self.fetch(url, category=category, source_type=source_type)
        return content.decode("utf-8-sig", errors="replace")
