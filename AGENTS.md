# NTU Knowledge Crawler

Build a reproducible crawler and knowledge-base pipeline for official Nha Trang
University academic information.

## Core rules

- Use only domains listed in `config/sources.yaml`.
- Never invent missing facts or infer cohorts from document dates.
- Never infer prerequisites; store `null` when the source does not state them.
- Preserve provenance, retrieval timestamps, source history, and unresolved conflicts.
- Never collect student personal information or rosters.
- Save raw sources before extraction and use SHA-256 to detect changes.
- SQLite is the source of truth; Markdown, JSONL, and OWL are generated outputs.
- A failed recoverable task must not stop unrelated pipeline tasks.

## Explicit exclusions

Do not use QĐ 1052 dated 17/07/2025, QĐ 1965 amendment, QĐ 626 dated
29/04/2026, QĐ 753/2021, QĐ 729 tuition, QĐ 317 scholarship, or the full
Phòng Đào tạo forms list as factual bases.

## Priority

1. Undergraduate curricula K63-K68
2. Course-level data
3. Graduate programs and student procedures
4. Finance, services, discipline, and admissions
5. Versioning and exports

Before declaring success, run tests and check malformed URLs, duplicates,
missing provenance, missing retrieval dates, and conflicting current facts.
