"""Command-line entry point for the NTU knowledge crawler."""

from __future__ import annotations

import argparse
import traceback
from collections import Counter
from pathlib import Path

from crawlers.ctdt import CTDTCrawler, now_iso
from crawlers.postgraduate import PostgraduateCrawler
from crawlers.procedures import ProcedureCrawler
from crawlers.admissions import AdmissionsCrawler
from crawlers.discipline import build_discipline_crawler
from crawlers.finance import build_finance_crawler
from crawlers.services import build_services_crawler
from database import KnowledgeStore
from extractors.fact_extractor import generate_facts
from pipeline.export import (
    export_academic_master_markdown,
    export_facts_jsonl,
    export_json,
    export_master_markdown,
    export_ontology,
    generate_audit,
    generate_quality_report,
)
from pipeline.student_fact_export import (
    crawl_curated_student_sources,
    export_student_fact_markdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl the official NTU knowledge sources")
    parser.add_argument("--database", default="database/ntu_kb.db")
    parser.add_argument("--raw-directory", default="data/raw/ctdt")
    parser.add_argument("--output-directory", default="output")
    parser.add_argument("--limit", type=int, help="Limit program/PDF items for a sample run")
    parser.add_argument("--skip-pdfs", action="store_true", help="Queue PDFs but do not download them")
    parser.add_argument("--skip-postgraduate", action="store_true", help="Skip graduate programs and admissions")
    parser.add_argument("--skip-procedures", action="store_true", help="Skip student-procedure discovery")
    parser.add_argument("--skip-milestone5", action="store_true", help="Skip finance, services, discipline, and admissions")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    totals: Counter[str] = Counter()
    failed_steps: list[str] = []

    with KnowledgeStore(args.database) as store:
        run_started_at = now_iso()
        run_id = store.start_run(run_started_at)
        crawler = CTDTCrawler(store, args.raw_directory)
        postgraduate = PostgraduateCrawler(store, str(Path(args.raw_directory).parent))
        procedures = ProcedureCrawler(store, str(Path(args.raw_directory).parent))
        shared_raw = str(Path(args.raw_directory).parent)
        admissions = AdmissionsCrawler(store, shared_raw)
        finance = build_finance_crawler(store, shared_raw)
        services = build_services_crawler(store, shared_raw)
        discipline = build_discipline_crawler(store, shared_raw)
        stages = [
            (
                "crawl_curated_student_sources",
                lambda: crawl_curated_student_sources(store, shared_raw),
            ),
            ("discover_ctdt", crawler.discover),
            ("crawl_ctdt_and_courses", lambda: crawler.crawl_programs(limit=args.limit)),
        ]
        if not args.skip_pdfs:
            stages.append(("download_ctdt_pdfs", lambda: crawler.crawl_pdfs(limit=args.limit)))
        if not args.skip_postgraduate:
            stages.extend(
                [
                    ("discover_postgraduate", postgraduate.discover),
                    ("crawl_postgraduate_programs", lambda: postgraduate.crawl_programs(args.limit)),
                    ("crawl_postgraduate_admissions", lambda: postgraduate.crawl_admissions(args.limit)),
                ]
            )
        if not args.skip_procedures:
            stages.extend(
                [
                    ("discover_procedures", procedures.discover),
                    ("crawl_procedure_candidates", lambda: procedures.crawl_candidates(args.limit)),
                ]
            )
        if not args.skip_milestone5:
            stages.extend(
                [
                    ("crawl_admissions", admissions.crawl),
                    ("discover_finance", finance.discover),
                    ("crawl_finance", lambda: finance.crawl(args.limit)),
                    ("discover_services", services.discover),
                    ("crawl_services", lambda: services.crawl(args.limit)),
                    ("discover_discipline", discipline.discover),
                    ("crawl_discipline", lambda: discipline.crawl(args.limit)),
                ]
            )

        for name, stage in stages:
            task_id = store.start_task(run_id, name, now_iso())
            try:
                result = stage()
                store.finish_task(task_id, "success", now_iso(), result or {})
            except Exception as error:
                failed_steps.append(name)
                store.finish_task(task_id, "failed", now_iso(), error=str(error))
                traceback.print_exc()

        output_directory = Path(args.output_directory)
        fact_task_id = store.start_task(run_id, "generate_facts", now_iso())
        try:
            fact_stats = generate_facts(store)
            totals.update(fact_stats)
            store.finish_task(fact_task_id, "success", now_iso(), fact_stats)
        except Exception as error:
            failed_steps.append("generate_facts")
            store.finish_task(fact_task_id, "failed", now_iso(), error=str(error))
            traceback.print_exc()
        export_task_id = store.start_task(run_id, "export_and_audit", now_iso())
        try:
            totals.update(crawler.stats)
            totals.update(postgraduate.stats)
            totals.update(procedures.stats)
            totals.update(admissions.stats)
            totals.update(finance.stats)
            totals.update(services.stats)
            totals.update(discipline.stats)
            totals.update(export_json(store, output_directory))
            totals["facts"] = export_facts_jsonl(store, output_directory / "facts.jsonl")
            export_master_markdown(store, output_directory / "MASTER.md")
            export_academic_master_markdown(store, output_directory / "NTU_HOC_VU_MASTER.md")
            totals["student_fact_rows"] = export_student_fact_markdown(
                store, output_directory / "NTU_STUDENT_FACTS.md"
            )
            export_ontology(store, output_directory / "ontology.owl")
            quality = generate_quality_report(store, output_directory / "DATA_QUALITY_REPORT.md")
            critical_checks = (
                "foreign_key_violations",
                "malformed_or_unapproved_urls",
                "facts_missing_provenance",
                "facts_missing_retrieved_at",
                "admissions_missing_year",
                "admissions_missing_category",
                "student_services_missing_evidence",
                "discipline_rules_missing_evidence",
                "partial_discipline_rules",
                "multiple_current_source_versions",
                "duplicate_current_logical_keys",
                "current_facts_missing_citation",
                "complete_programs_without_courses",
                "program_gaps_without_classification",
                "program_gap_conclusions_without_evidence",
                "credit_values_missing_evidence",
                "financial_index_pages_materialized_as_policies",
                "potential_personal_data_sources",
                "excluded_decisions_used",
                "duplicate_facts",
                "duplicate_course_relationships",
            )
            if any(quality[name] != 0 for name in critical_checks):
                failed_steps.append("data_quality")
            totals["urls_discovered"] = store.connection.execute(
                "SELECT COUNT(*) FROM crawl_queue"
            ).fetchone()[0]
            totals["urls_crawled"] = store.connection.execute(
                "SELECT COUNT(*) FROM crawl_queue WHERE status='done'"
            ).fetchone()[0]
            totals["failed_urls"] = store.connection.execute(
                "SELECT COUNT(*) FROM crawl_queue WHERE status='failed'"
            ).fetchone()[0]
            totals["current_documents"] = store.connection.execute(
                "SELECT COUNT(*) FROM source_versions WHERE status='current'"
            ).fetchone()[0]
            totals["historical_documents"] = store.connection.execute(
                "SELECT COUNT(*) FROM source_versions WHERE status='historical'"
            ).fetchone()[0]
            totals["documents_new_or_changed"] = store.connection.execute(
                "SELECT COUNT(*) FROM source_versions WHERE first_retrieved_at >= ?",
                (run_started_at,),
            ).fetchone()[0]
            totals["historical_facts"] = store.connection.execute(
                "SELECT COUNT(*) FROM facts WHERE status='historical'"
            ).fetchone()[0]
            totals["conflicts"] = store.connection.execute(
                "SELECT COUNT(*) FROM conflicts WHERE status='unresolved'"
            ).fetchone()[0]
            for key in ("facts_new", "facts_updated", "facts_duplicate"):
                totals.setdefault(key, 0)
            store.finish_task(export_task_id, "success", now_iso(), dict(totals))
            generate_audit(store, output_directory / "CRAWL_AUDIT.md")
        except Exception as error:
            failed_steps.append("export_and_audit")
            store.finish_task(export_task_id, "failed", now_iso(), error=str(error))
            traceback.print_exc()

        status = "partial" if failed_steps or totals.get("errors") else "success"
        store.finish_run(run_id, now_iso(), status, dict(totals))

    print("Crawl hoàn tất" if not failed_steps else "Crawl hoàn tất một phần")
    for key, value in sorted(totals.items()):
        print(f"{key}: {value}")
    if failed_steps:
        print("Task lỗi: " + ", ".join(failed_steps))
    return 1 if failed_steps or totals.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
