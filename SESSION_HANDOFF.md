# Session handoff

Last updated: 2026-09-13 (Asia/Ho_Chi_Minh)

## Status

All six requested implementation milestones are complete. The last full command
`python3 main.py` exited successfully, all 26 tests passed, every stage in the
latest crawl run has status `success`, and all JSON/JSONL/RDF/XML artifacts parse.

## Current snapshot

- 334 discovered URLs, 333 completed, 1 permanent official-source 404.
- 294 CTĐT rows: 129 parsed and 165 explicitly marked missing for K63–K68.
- 6,854 course-program relations.
- 28 graduate programs and 12 graduate admissions.
- 12 procedures: 3 verified workflows, 8 verified official forms, and 1 legacy
  `.doc` candidate whose content is retained without assuming a portable parser.
- 170 year-scoped undergraduate admissions: 50 regular and 120 across VLVH,
  articulation, and second-degree categories.
- 18 financial-policy records: 4 current, 8 historical, and 6 unresolved with
  explicit verification notes. Debt and payment-extension rules are distinct.
- 4 service units and 12 verified services with per-service provenance, covering
  library, BHYT guidance, employment, internship, entrepreneurship, and dormitory.
- 20 atomic discipline rules manually checked against QĐ 1351 scan pages; OCR
  corrections are reproducible in `crawlers/discipline.py`.
- 7,241 current facts, 10 historical facts, 370 current source documents, and 0
  unresolved conflicts.

## Expected unresolved items

- `output/DATA_QUALITY_REPORT.md` warns about 6 unresolved financial policies.
  These are generic category/listing pages lacking a year, actionable condition,
  deadline, or NTU-specific amount; the reason is stored per record.
- `https://ctdt.ntu.edu.vn/ctdt/7580205_CTGT_K64.pdf` returns HTTP 404. It reached
  the three-attempt cap and remains visible in `CRAWL_AUDIT.md` without endless
  retries.
- 165 program/cohort combinations are explicit `crawl_status=missing`; they are
  source coverage gaps, not inferred claims that a curriculum does not exist.
  See `output/CTDT_GAP_AUDIT.md` for cohort totals and every combination.

## Next-session operation

Use `python3 main.py` for a full update or `python3 main.py --skip-pdfs` for the
weekly-style update. Review the audit and quality report before promoting any
currently unresolved finance record. Do not clean files outside this project;
the enclosing Git repository contains unrelated user data.
