"""Fact hashing, source-quality confidence, and non-destructive versioning."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date
from typing import Any

from crawlers.ctdt import now_iso
from database import KnowledgeStore


HIGH_CONFIDENCE = {
    "signed_official_document",
    "official_program_pdf",
    "official_document",
    "official_api",
    "official_unit_page",
}
MEDIUM_CONFIDENCE = {"official_news", "historical_notice"}


def confidence_for_source(source_type: str) -> str:
    if source_type in HIGH_CONFIDENCE:
        return "Cao"
    if source_type in MEDIUM_CONFIDENCE:
        return "Trung bình"
    return "Thấp"


def fact_hash(fact: Mapping[str, Any]) -> str:
    raw = "|".join(
        str(fact.get(field) or "")
        for field in ("entity", "type", "content", "source_url", "citation")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def logical_key(fact: Mapping[str, Any]) -> str:
    raw = "|".join(
        str(fact.get(field) or "")
        for field in ("entity", "type", "relation", "target", "cohort", "academic_year")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upsert_fact(store: KnowledgeStore, fact: dict[str, Any]) -> str:
    fact_id = fact_hash(fact)
    key = logical_key(fact)
    previous = list(
        store.connection.execute(
            """SELECT id, content, source_url, citation, note FROM facts
               WHERE logical_key=? AND status='current'""",
            (key,),
        )
    )
    same = [
        old for old in previous
        if old["content"] == fact["content"] and old["source_url"] == fact["source_url"]
    ]
    if same:
        # A citation/note enrichment is metadata maintenance, not a new fact
        # version. Prefer the exact new hash, then the richest citation.
        keep = next((old for old in same if old["id"] == fact_id), None)
        if keep is None:
            keep = max(same, key=lambda old: len(old["citation"] or ""))
        for old in same:
            if old["id"] != keep["id"]:
                store.connection.execute(
                    """UPDATE facts SET status='historical', valid_to=?, superseded_by=?
                       WHERE id=?""",
                    (date.today().isoformat(), keep["id"], old["id"]),
                )
        store.connection.execute(
            """UPDATE facts SET citation=?, note=?, source_type=?, confidence=?,
                      retrieved_at=? WHERE id=?""",
            (
                fact.get("citation"), fact.get("note"), fact.get("source_type"),
                confidence_for_source(fact.get("source_type") or ""),
                fact.get("retrieved_at") or now_iso(), keep["id"],
            ),
        )
        store.connection.commit()
        return "duplicate" if len(same) == 1 else "updated"

    if store.connection.execute("SELECT 1 FROM facts WHERE id=?", (fact_id,)).fetchone():
        return "duplicate"

    for old in previous:
        if old["source_url"] == fact["source_url"]:
            store.connection.execute(
                """UPDATE facts SET status='historical', valid_to=?, superseded_by=?
                   WHERE id=?""",
                (date.today().isoformat(), fact_id, old["id"]),
            )
        else:
            pair = "|".join(sorted((old["id"], fact_id)))
            conflict_id = hashlib.sha256(f"{key}|{pair}".encode("utf-8")).hexdigest()
            store.connection.execute(
                """INSERT OR IGNORE INTO conflicts
                   (conflict_id, logical_key, fact_a_id, fact_b_id, detected_at, note)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    conflict_id,
                    key,
                    old["id"],
                    fact_id,
                    now_iso(),
                    "Conflicting current facts from different official sources",
                ),
            )

    record = {
        "id": fact_id,
        "logical_key": key,
        "status": "current",
        "confidence": confidence_for_source(fact.get("source_type") or ""),
        "retrieved_at": fact.get("retrieved_at") or now_iso(),
        **fact,
    }
    columns = tuple(record)
    store.connection.execute(
        f"INSERT INTO facts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(record[column] for column in columns),
    )
    store.connection.commit()
    return "new" if not previous else "updated"
