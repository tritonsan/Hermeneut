from __future__ import annotations

import re
from typing import Any


FILE_TITLE = re.compile(r"\.(pdf|docx?|txt)$|^(juz|part|volume|vol)[-_ ]?\d+", re.IGNORECASE)
UNRESOLVED = {"", "unknown", "metadata unresolved", "unresolved", "n/a", "none"}


def enrich_catalog_quality(result: dict[str, Any]) -> dict[str, Any]:
    for section in ("works", "sources"):
        result[section] = [classify_catalog_record(dict(row), section[:-1]) for row in result.get(section, [])]
    return result


def classify_catalog_record(record: dict[str, Any], record_type: str) -> dict[str, Any]:
    title = str(record.get("title") or record.get("work_title") or "").strip()
    author = str(record.get("author_name") or "").strip()
    reasons: list[str] = []
    if not title:
        reasons.append("missing_title")
    elif FILE_TITLE.search(title):
        reasons.append("filename_like_title")
    elif len(title) > 180 or (len(title.split()) > 22 and title.count(",") >= 3):
        reasons.append("overlong_keyword_title")
    if author.lower() in UNRESOLVED and not record.get("author_id"):
        reasons.append("unresolved_author")
    if record.get("metadata_conflict") or record.get("catalog_conflict"):
        reasons.append("metadata_text_conflict")
    if str(record.get("catalog_review_status") or "").lower() == "needs_review":
        reasons.extend(str(reason) for reason in record.get("catalog_review_reasons", []) if reason)
    if record.get("curator_flagged"):
        reasons.append("curator_flagged")
    reasons = list(dict.fromkeys(reasons))
    penalty = sum(30 if reason in {"missing_title", "filename_like_title"} else 20 for reason in reasons)
    if record_type == "source" and not author and record.get("work_id"):
        penalty = max(0, penalty - 10)
    record["catalog_review_reasons"] = reasons
    record["metadata_quality_score"] = max(0, min(100, 100 - penalty))
    record["catalog_review_status"] = "needs_review" if reasons else "verified"
    return record
