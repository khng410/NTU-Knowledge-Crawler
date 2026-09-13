"""SQLite schema and persistence helpers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    source_type TEXT NOT NULL,
    category TEXT NOT NULL,
    content_hash TEXT,
    raw_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    retrieved_at TEXT
);

CREATE TABLE IF NOT EXISTS crawl_queue (
    url TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    last_attempt TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'running', 'success', 'partial', 'failed')),
    stats_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS crawl_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
    task TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'success', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stats_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS crawl_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES crawl_runs(id),
    url TEXT NOT NULL,
    http_status INTEGER,
    error TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS majors (
    major_id INTEGER PRIMARY KEY,
    major_code TEXT NOT NULL,
    major_name TEXT NOT NULL,
    faculty TEXT,
    source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS programs (
    program_id TEXT PRIMARY KEY,
    external_id INTEGER UNIQUE,
    major_code TEXT NOT NULL,
    major_name TEXT,
    program_name TEXT,
    cohort TEXT,
    faculty TEXT,
    degree_level TEXT,
    training_mode TEXT,
    duration TEXT,
    total_credits REAL,
    language TEXT,
    degree TEXT,
    decision_number TEXT,
    decision_date TEXT,
    updated_date TEXT,
    plos_json TEXT,
    source_url TEXT NOT NULL,
    pdf_url TEXT,
    crawl_status TEXT NOT NULL,
    content_status TEXT,
    publication_status TEXT,
    published INTEGER,
    declared_course_count INTEGER,
    credit_status TEXT,
    credit_method TEXT,
    credit_citation TEXT,
    pdf_status TEXT,
    fallback_url TEXT,
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS program_gaps (
    gap_id TEXT PRIMARY KEY,
    major_code TEXT NOT NULL,
    major_name TEXT,
    cohort TEXT NOT NULL,
    gap_status TEXT NOT NULL,
    expected_reason TEXT,
    evidence_url TEXT NOT NULL,
    citation TEXT,
    replacement_major_code TEXT,
    review_note TEXT,
    reviewed_at TEXT,
    retrieved_at TEXT NOT NULL,
    UNIQUE(major_code, cohort)
);

CREATE TABLE IF NOT EXISTS courses (
    course_id TEXT PRIMARY KEY,
    course_code TEXT,
    course_name TEXT NOT NULL,
    credits REAL,
    theory_hours_or_credits REAL,
    practice_hours_or_credits REAL,
    workload_unit TEXT,
    semester INTEGER,
    course_type TEXT,
    prerequisite_json TEXT,
    program_id TEXT NOT NULL REFERENCES programs(program_id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    citation TEXT,
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    logical_key TEXT,
    entity TEXT NOT NULL,
    type TEXT NOT NULL,
    code TEXT,
    content TEXT NOT NULL,
    relation TEXT,
    target TEXT,
    source_url TEXT NOT NULL,
    source_domain TEXT,
    source_type TEXT,
    citation TEXT,
    document_number TEXT,
    document_date TEXT,
    cohort TEXT,
    academic_year TEXT,
    degree_level TEXT,
    program_version TEXT,
    valid_from TEXT,
    valid_to TEXT,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    amended_by TEXT,
    superseded_by TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS source_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    first_retrieved_at TEXT NOT NULL,
    last_retrieved_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'current',
    UNIQUE(url, content_hash)
);

CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id TEXT PRIMARY KEY,
    logical_key TEXT NOT NULL,
    fact_a_id TEXT NOT NULL,
    fact_b_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unresolved',
    detected_at TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS graduate_programs (
    program_id TEXT PRIMARY KEY,
    external_id INTEGER UNIQUE,
    degree_level TEXT NOT NULL,
    major_code TEXT NOT NULL,
    major_name TEXT NOT NULL,
    program_name TEXT NOT NULL,
    orientation TEXT,
    credits REAL,
    duration TEXT,
    entry_conditions TEXT,
    language_entry_requirement TEXT,
    language_exit_requirement TEXT,
    graduation_conditions TEXT,
    cohort TEXT,
    source_url TEXT NOT NULL,
    citation TEXT,
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graduate_admissions (
    admission_id TEXT PRIMARY KEY,
    degree_level TEXT NOT NULL,
    admission_year INTEGER,
    title TEXT NOT NULL,
    entry_conditions TEXT,
    language_entry_requirement TEXT,
    language_exit_requirement TEXT,
    important_dates TEXT,
    forms_json TEXT,
    decisions_json TEXT,
    documents_json TEXT,
    source_url TEXT NOT NULL,
    citation TEXT,
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procedure_candidates (
    candidate_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    matched_keywords_json TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate_procedure',
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procedures (
    procedure_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applicable_to TEXT,
    conditions_json TEXT,
    required_documents_json TEXT,
    submission_unit TEXT,
    processing_unit TEXT,
    deadline TEXT,
    processing_time TEXT,
    result TEXT,
    fee TEXT,
    form TEXT,
    legal_basis TEXT,
    source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate'
    , procedure_kind TEXT
    , verification_note TEXT
);

CREATE TABLE IF NOT EXISTS admissions (
    admission_id TEXT PRIMARY KEY,
    year INTEGER NOT NULL,
    admission_code TEXT NOT NULL,
    major_code TEXT,
    major_name TEXT NOT NULL,
    specialization TEXT,
    admission_mode TEXT,
    admission_category TEXT NOT NULL DEFAULT 'regular_undergraduate',
    program_type TEXT,
    duration TEXT,
    admission_conditions TEXT,
    important_dates TEXT,
    source_url TEXT NOT NULL,
    year_source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE(year, admission_code, major_name, admission_category)
);

CREATE TABLE IF NOT EXISTS source_candidates (
    candidate_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    matched_keywords_json TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unresolved',
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS financial_policies (
    policy_id TEXT PRIMARY KEY,
    title TEXT,
    policy_type TEXT NOT NULL,
    policy_scope TEXT,
    beneficiary TEXT,
    amount REAL,
    percentage REAL,
    academic_year TEXT,
    cohort TEXT,
    conditions_json TEXT,
    deadline TEXT,
    legal_basis TEXT,
    source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unresolved'
    , verification_note TEXT
);

CREATE TABLE IF NOT EXISTS units (
    unit_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    email TEXT,
    working_hours TEXT,
    services_json TEXT,
    source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS student_services (
    service_id TEXT PRIMARY KEY,
    unit_id TEXT NOT NULL REFERENCES units(unit_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    eligibility TEXT,
    fee TEXT,
    working_hours TEXT,
    source_url TEXT NOT NULL,
    citation TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'verified'
);

CREATE TABLE IF NOT EXISTS discipline_rules (
    rule_id TEXT PRIMARY KEY,
    behavior TEXT NOT NULL,
    first_violation TEXT,
    second_violation TEXT,
    third_violation TEXT,
    fourth_violation TEXT,
    fifth_violation TEXT,
    sanction TEXT,
    citation TEXT,
    source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unresolved'
);
"""


class KnowledgeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_admissions_schema()
        self._ensure_column("graduate_admissions", "documents_json", "TEXT")
        self._ensure_column("procedures", "status", "TEXT NOT NULL DEFAULT 'candidate'")
        self._ensure_column("procedures", "procedure_kind", "TEXT")
        self._ensure_column("procedures", "verification_note", "TEXT")
        self._ensure_column("financial_policies", "title", "TEXT")
        self._ensure_column("financial_policies", "policy_scope", "TEXT")
        self._ensure_column("financial_policies", "verification_note", "TEXT")
        self._ensure_column("discipline_rules", "fourth_violation", "TEXT")
        self._ensure_column("discipline_rules", "fifth_violation", "TEXT")
        program_columns = {
            "content_status": "TEXT",
            "publication_status": "TEXT",
            "published": "INTEGER",
            "declared_course_count": "INTEGER",
            "credit_status": "TEXT",
            "credit_method": "TEXT",
            "credit_citation": "TEXT",
            "pdf_status": "TEXT",
            "fallback_url": "TEXT",
        }
        for column, declaration in program_columns.items():
            self._ensure_column("programs", column, declaration)
        fact_columns = {
            "logical_key": "TEXT",
            "code": "TEXT",
            "relation": "TEXT",
            "target": "TEXT",
            "source_domain": "TEXT",
            "source_type": "TEXT",
            "document_number": "TEXT",
            "document_date": "TEXT",
            "degree_level": "TEXT",
            "program_version": "TEXT",
            "amended_by": "TEXT",
            "superseded_by": "TEXT",
            "note": "TEXT",
        }
        for column, declaration in fact_columns.items():
            self._ensure_column("facts", column, declaration)
        self.connection.execute(
            """INSERT OR IGNORE INTO source_versions
               (url, content_hash, raw_path, first_retrieved_at, last_retrieved_at, status)
               SELECT url, content_hash, raw_path, retrieved_at, retrieved_at, 'current'
               FROM sources
               WHERE content_hash IS NOT NULL AND raw_path IS NOT NULL AND retrieved_at IS NOT NULL"""
        )
        self.connection.execute(
            """UPDATE programs SET pdf_status='missing_asset'
               WHERE pdf_status IS NOT 'missing_asset_with_html_fallback'
                 AND pdf_url IN (
                   SELECT url FROM crawl_queue
                   WHERE category='ctdt_pdf' AND status='failed'
                     AND retry_count >= 3 AND error LIKE '%404%'
               )"""
        )
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            self.connection.commit()

    def _migrate_admissions_schema(self) -> None:
        table_sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='admissions'"
        ).fetchone()[0]
        if "admission_category" in table_sql and "major_name, admission_category" in table_sql:
            return
        self.connection.executescript(
            """ALTER TABLE admissions RENAME TO admissions_legacy;
            CREATE TABLE admissions (
                admission_id TEXT PRIMARY KEY,
                year INTEGER NOT NULL,
                admission_code TEXT NOT NULL,
                major_code TEXT,
                major_name TEXT NOT NULL,
                specialization TEXT,
                admission_mode TEXT,
                admission_category TEXT NOT NULL DEFAULT 'regular_undergraduate',
                program_type TEXT,
                duration TEXT,
                admission_conditions TEXT,
                important_dates TEXT,
                source_url TEXT NOT NULL,
                year_source_url TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,
                UNIQUE(year, admission_code, major_name, admission_category)
            );
            INSERT INTO admissions (
                admission_id, year, admission_code, major_code, major_name,
                specialization, admission_mode, admission_category, program_type,
                duration, admission_conditions, important_dates, source_url,
                year_source_url, retrieved_at
            )
            SELECT admission_id, year, admission_code, major_code, major_name,
                   specialization, admission_mode, 'regular_undergraduate', program_type,
                   duration, admission_conditions, important_dates, source_url,
                   year_source_url, retrieved_at
            FROM admissions_legacy;
            DROP TABLE admissions_legacy;"""
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "KnowledgeStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start_run(self, started_at: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO crawl_runs(started_at, status) VALUES (?, 'running')",
            (started_at,),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self, run_id: int, finished_at: str, status: str, stats: Mapping[str, Any]
    ) -> None:
        self.connection.execute(
            "UPDATE crawl_runs SET finished_at=?, status=?, stats_json=? WHERE id=?",
            (finished_at, status, json.dumps(dict(stats), ensure_ascii=False), run_id),
        )
        self.connection.commit()

    def start_task(self, run_id: int, task: str, started_at: str) -> int:
        cursor = self.connection.execute(
            """INSERT INTO crawl_tasks(run_id, task, status, started_at)
               VALUES (?, ?, 'running', ?)""",
            (run_id, task, started_at),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_task(
        self,
        task_id: int,
        status: str,
        finished_at: str,
        stats: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """UPDATE crawl_tasks
               SET status=?, finished_at=?, stats_json=?, error=? WHERE id=?""",
            (status, finished_at, json.dumps(dict(stats or {}), ensure_ascii=False), error, task_id),
        )
        self.connection.commit()

    def enqueue(self, url: str, category: str, *, refresh: bool = False) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO crawl_queue(url, category) VALUES (?, ?)",
            (url, category),
        )
        if refresh:
            self.connection.execute(
                """UPDATE crawl_queue
                   SET status='pending', retry_count=0, error=NULL
                   WHERE url=? AND status='done'""",
                (url,),
            )
        self.connection.commit()

    def queued(self, categories: Iterable[str], limit: int | None = None) -> list[sqlite3.Row]:
        category_list = list(categories)
        placeholders = ",".join("?" for _ in category_list)
        sql = f"""SELECT * FROM crawl_queue
                  WHERE status IN ('pending', 'failed') AND retry_count < 3
                    AND category IN ({placeholders})
                  ORDER BY retry_count, url"""
        params: list[Any] = category_list
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return list(self.connection.execute(sql, params))

    def mark_queue(self, url: str, status: str, attempted_at: str, error: str | None = None) -> None:
        retry_increment = 1 if status == "failed" else 0
        self.connection.execute(
            """UPDATE crawl_queue
               SET status=?, last_attempt=?, retry_count=retry_count+?, error=?
               WHERE url=?""",
            (status, attempted_at, retry_increment, error, url),
        )
        self.connection.commit()

    def save_source(self, record: Mapping[str, Any]) -> bool:
        previous = self.connection.execute(
            "SELECT content_hash FROM sources WHERE url=?", (record["url"],)
        ).fetchone()
        changed = previous is None or previous["content_hash"] != record["content_hash"]
        if previous is not None and changed:
            self.connection.execute(
                "UPDATE source_versions SET status='historical' WHERE url=? AND status='current'",
                (record["url"],),
            )
        self.connection.execute(
            """INSERT INTO source_versions
               (url, content_hash, raw_path, first_retrieved_at, last_retrieved_at, status)
               VALUES (:url, :content_hash, :raw_path, :retrieved_at, :retrieved_at, 'current')
               ON CONFLICT(url, content_hash) DO UPDATE SET
                 last_retrieved_at=excluded.last_retrieved_at,
                 status='current'""",
            record,
        )
        self.connection.execute(
            """INSERT INTO sources
               (url, domain, source_type, category, content_hash, raw_path, status, retrieved_at)
               VALUES (:url, :domain, :source_type, :category, :content_hash,
                       :raw_path, :status, :retrieved_at)
               ON CONFLICT(url) DO UPDATE SET
                 content_hash=excluded.content_hash,
                 raw_path=excluded.raw_path,
                 status=excluded.status,
                 retrieved_at=excluded.retrieved_at""",
            record,
        )
        self.connection.commit()
        return changed

    def save_major(self, record: Mapping[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO majors
               (major_id, major_code, major_name, faculty, source_url, retrieved_at)
               VALUES (:major_id, :major_code, :major_name, :faculty, :source_url, :retrieved_at)
               ON CONFLICT(major_id) DO UPDATE SET
                 major_code=excluded.major_code, major_name=excluded.major_name,
                 faculty=excluded.faculty, source_url=excluded.source_url,
                 retrieved_at=excluded.retrieved_at""",
            record,
        )
        self.connection.commit()

    def save_program(self, record: Mapping[str, Any]) -> None:
        columns = tuple(record)
        updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "program_id")
        sql = f"""INSERT INTO programs ({', '.join(columns)})
                  VALUES ({', '.join('?' for _ in columns)})
                  ON CONFLICT(program_id) DO UPDATE SET {updates}"""
        self.connection.execute(sql, tuple(record[column] for column in columns))
        self.connection.commit()

    def replace_courses(self, program_id: str, records: Iterable[Mapping[str, Any]]) -> int:
        self.connection.execute("DELETE FROM courses WHERE program_id=?", (program_id,))
        count = 0
        for record in records:
            columns = tuple(record)
            self.connection.execute(
                f"INSERT INTO courses ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(record[column] for column in columns),
            )
            count += 1
        self.connection.commit()
        return count

    def error(self, run_id: int, url: str, message: str, created_at: str, http_status: int | None = None) -> None:
        self.connection.execute(
            """INSERT INTO crawl_errors(run_id, url, http_status, error, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, url, http_status, message, created_at),
        )
        self.connection.commit()

    def rows(self, table: str) -> list[dict[str, Any]]:
        allowed = {"majors", "programs", "courses", "crawl_errors", "crawl_queue", "sources"}
        if table not in allowed:
            raise ValueError(f"Unsupported table: {table}")
        return [dict(row) for row in self.connection.execute(f"SELECT * FROM {table}")]
