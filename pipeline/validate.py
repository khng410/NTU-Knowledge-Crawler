"""Data-quality validation queries."""

from urllib.parse import urlsplit

from database import KnowledgeStore
from pipeline.crawl import load_approved_domains
from pipeline.deduplicate import duplicate_counts


def quality_checks(store: KnowledgeStore) -> dict[str, int | str]:
    approved = load_approved_domains()
    urls = [
        row[0]
        for row in store.connection.execute(
            "SELECT source_url FROM facts UNION SELECT url FROM sources"
        )
    ]
    malformed = sum(
        not urlsplit(url).hostname
        or urlsplit(url).scheme != "https"
        or urlsplit(url).hostname not in approved
        for url in urls
    )
    checks: dict[str, int | str] = {
        "database_integrity": store.connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_violations": len(store.connection.execute("PRAGMA foreign_key_check").fetchall()),
        "malformed_or_unapproved_urls": malformed,
        "facts_missing_provenance": store.connection.execute(
            "SELECT COUNT(*) FROM facts WHERE source_url IS NULL OR source_domain IS NULL"
        ).fetchone()[0],
        "facts_missing_retrieved_at": store.connection.execute(
            "SELECT COUNT(*) FROM facts WHERE retrieved_at IS NULL"
        ).fetchone()[0],
        "admissions_missing_year": store.connection.execute(
            "SELECT COUNT(*) FROM admissions WHERE year IS NULL"
        ).fetchone()[0],
        "admissions_missing_category": store.connection.execute(
            "SELECT COUNT(*) FROM admissions WHERE admission_category IS NULL OR admission_category=''"
        ).fetchone()[0],
        "units_missing_contact": store.connection.execute(
            "SELECT COUNT(*) FROM units WHERE phone IS NULL AND email IS NULL"
        ).fetchone()[0],
        "student_services_missing_evidence": store.connection.execute(
            """SELECT COUNT(*) FROM student_services
               WHERE source_url IS NULL OR citation IS NULL OR retrieved_at IS NULL"""
        ).fetchone()[0],
        "discipline_rules_missing_evidence": store.connection.execute(
            """SELECT COUNT(*) FROM discipline_rules
               WHERE source_url IS NULL OR citation IS NULL OR retrieved_at IS NULL"""
        ).fetchone()[0],
        "partial_discipline_rules": store.connection.execute(
            "SELECT COUNT(*) FROM discipline_rules WHERE status='partial_ocr'"
        ).fetchone()[0],
        "unresolved_financial_policies": store.connection.execute(
            "SELECT COUNT(*) FROM financial_policies WHERE status='unresolved'"
        ).fetchone()[0],
        "potential_personal_data_sources": store.connection.execute(
            """SELECT COUNT(*) FROM source_candidates
               WHERE lower(title) LIKE 'danh sách%'"""
        ).fetchone()[0],
        "excluded_decisions_used": store.connection.execute(
            """SELECT COUNT(*) FROM sources WHERE
               lower(url) LIKE '%qd%20317%' OR lower(url) LIKE '%qd%20729%' OR
               lower(url) LIKE '%qd%201052%' OR lower(url) LIKE '%qd%20626%'"""
        ).fetchone()[0],
        "unresolved_conflicts": store.connection.execute(
            "SELECT COUNT(*) FROM conflicts WHERE status='unresolved'"
        ).fetchone()[0],
        "multiple_current_source_versions": store.connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT url FROM source_versions WHERE status='current'
                   GROUP BY url HAVING COUNT(*) > 1
               )"""
        ).fetchone()[0],
        "duplicate_current_logical_keys": store.connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT logical_key FROM facts WHERE status='current'
                   GROUP BY logical_key HAVING COUNT(*) > 1
               )"""
        ).fetchone()[0],
        "current_facts_missing_citation": store.connection.execute(
            """SELECT COUNT(*) FROM facts
               WHERE status='current' AND (citation IS NULL OR trim(citation)='')"""
        ).fetchone()[0],
        "draft_programs_without_courses": store.connection.execute(
            """SELECT COUNT(*) FROM programs p
               WHERE p.crawl_status='success'
                 AND p.content_status='draft_empty'
                 AND NOT EXISTS (SELECT 1 FROM courses c WHERE c.program_id=p.program_id)"""
        ).fetchone()[0],
        "complete_programs_without_courses": store.connection.execute(
            """SELECT COUNT(*) FROM programs p
               WHERE p.crawl_status='success'
                 AND p.content_status='complete'
                 AND NOT EXISTS (SELECT 1 FROM courses c WHERE c.program_id=p.program_id)"""
        ).fetchone()[0],
        "complete_programs_missing_total_credits": store.connection.execute(
            """SELECT COUNT(*) FROM programs
               WHERE crawl_status='success' AND content_status='complete'
                 AND total_credits IS NULL"""
        ).fetchone()[0],
        "program_gaps_without_classification": store.connection.execute(
            """SELECT COUNT(*) FROM programs p
               LEFT JOIN program_gaps g
                 ON g.major_code=p.major_code AND g.cohort=p.cohort
               WHERE p.crawl_status='missing' AND g.gap_id IS NULL"""
        ).fetchone()[0],
        "program_gap_conclusions_without_evidence": store.connection.execute(
            """SELECT COUNT(*) FROM program_gaps
               WHERE gap_status IN ('not_applicable', 'located')
                 AND (citation IS NULL OR trim(citation)='')"""
        ).fetchone()[0],
        "credit_values_missing_evidence": store.connection.execute(
            """SELECT COUNT(*) FROM programs
               WHERE total_credits IS NOT NULL
                 AND (credit_method IS NULL OR credit_citation IS NULL)"""
        ).fetchone()[0],
        "missing_pdf_assets": store.connection.execute(
            "SELECT COUNT(*) FROM programs WHERE pdf_status='missing_asset'"
        ).fetchone()[0],
        "pdf_assets_using_html_fallback": store.connection.execute(
            """SELECT COUNT(*) FROM programs
               WHERE pdf_status='missing_asset_with_html_fallback'"""
        ).fetchone()[0],
        "financial_index_pages_materialized_as_policies": store.connection.execute(
            """SELECT COUNT(*) FROM financial_policies p
               JOIN source_candidates c ON c.source_url=p.source_url
               WHERE c.status='index_only'"""
        ).fetchone()[0],
    }
    checks.update(duplicate_counts(store))
    return checks
