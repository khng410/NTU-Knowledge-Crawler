"""Duplicate diagnostics for normalized knowledge records."""

from database import KnowledgeStore


def duplicate_counts(store: KnowledgeStore) -> dict[str, int]:
    exact_facts = store.connection.execute(
        """SELECT COUNT(*) FROM (
           SELECT entity, type, content, source_url, citation, COUNT(*) amount
           FROM facts GROUP BY entity, type, content, source_url, citation
           HAVING amount > 1)"""
    ).fetchone()[0]
    course_relations = store.connection.execute(
        """SELECT COUNT(*) FROM (
           SELECT program_id, course_code, course_name, semester, course_type, COUNT(*) amount
           FROM courses GROUP BY program_id, course_code, course_name, semester, course_type
           HAVING amount > 1)"""
    ).fetchone()[0]
    return {"duplicate_facts": exact_facts, "duplicate_course_relationships": course_relations}
