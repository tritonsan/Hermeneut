from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.services.normalization import normalize_arabic


class ElasticBackupPreview:
    """Read-only catalog preview shaped like the live Elastic library response."""

    def __init__(self) -> None:
        self.path = Path(__file__).resolve().parents[1] / "data" / "elastic_preview.json"
        self._payload: dict | None = None

    @property
    def available(self) -> bool:
        return self.path.exists()

    def catalog(self, query: str = "") -> dict:
        payload = deepcopy(self._load())
        if not payload:
            return {}
        if query.strip():
            normalized = normalize_arabic(query)
            for section in ("authors", "works", "sources", "edges", "passages"):
                payload[section] = [
                    row
                    for row in payload.get(section, [])
                    if normalized in normalize_arabic(" ".join(str(value) for value in row.values()))
                    or any(
                        token in normalize_arabic(" ".join(str(value) for value in row.values()))
                        for token in normalized.split()
                    )
                ]
        payload["passages"] = [self._enrich_passage(row, payload) for row in payload.get("passages", [])]
        payload["meta"] = {
            **payload.get("meta", {}),
            "backend": "elastic_backup_preview",
            "read_only": True,
            "query": query,
            "counts": self.counts(),
        }
        return payload

    def relationships(self, library_id: str) -> list[dict]:
        return [
            edge
            for edge in self._load().get("edges", [])
            if edge.get("library_id") == library_id
        ]

    def source(self, source_id: str) -> dict | None:
        return next(
            (source for source in self._load().get("sources", []) if source.get("source_id") == source_id),
            None,
        )

    def sources(self, library_id: str) -> list[dict]:
        return [
            source
            for source in self._load().get("sources", [])
            if source.get("library_id") == library_id
        ]

    def passage(self, passage_id: str) -> dict | None:
        payload = self._load()
        passage = next((row for row in payload.get("passages", []) if row.get("passage_id") == passage_id), None)
        return self._enrich_passage(deepcopy(passage), payload) if passage else None

    def passages_for_source(self, source_id: str, library_id: str | None = None) -> list[dict]:
        payload = self._load()
        rows = [
            self._enrich_passage(deepcopy(row), payload)
            for row in payload.get("passages", [])
            if row.get("source_id") == source_id and (not library_id or row.get("library_id") == library_id)
        ]
        return sorted(rows, key=lambda row: (int(row.get("passage_order") or 10**9), str(row.get("source_page") or ""), str(row.get("passage_id") or "")))

    def counts(self) -> dict[str, int]:
        payload = self._load()
        return {
            "authors": len(payload.get("authors", [])),
            "works": len(payload.get("works", [])),
            "sources": len(payload.get("sources", [])),
            "passages": sum(int(source.get("indexed_passage_count") or 0) for source in payload.get("sources", [])),
            "edges": len(payload.get("edges", [])),
            "evidence": 0,
        }

    def _load(self) -> dict:
        if self._payload is None:
            if not self.available:
                self._payload = {}
            else:
                self._payload = json.loads(self.path.read_text(encoding="utf-8"))
        return self._payload

    def _enrich_passage(self, passage: dict | None, payload: dict) -> dict:
        if not passage:
            return {}
        source = next((row for row in payload.get("sources", []) if row.get("source_id") == passage.get("source_id")), {})
        work = next((row for row in payload.get("works", []) if row.get("work_id") == (passage.get("work_id") or source.get("work_id"))), {})
        author = next((row for row in payload.get("authors", []) if row.get("author_id") == (work.get("author_id") or source.get("author_id"))), {})
        source_title = source.get("title") or passage.get("source_title") or passage.get("source_id")
        work_title = work.get("title") or source.get("work_title") or passage.get("work_title") or passage.get("work_id")
        author_name = author.get("name") or work.get("author_name") or source.get("author_name") or passage.get("author_name") or "metadata unresolved"
        page_ref = passage.get("page_ref") or passage.get("source_page") or passage.get("section_ref")
        location = " · ".join(str(part) for part in [work_title, source_title, page_ref] if part)
        citation = f"{author_name}, {work_title}, {source_title}"
        if page_ref:
            citation = f"{citation}, {page_ref}"
        return {
            **passage,
            "library_id": passage.get("library_id") or source.get("library_id") or work.get("library_id"),
            "source_title": source_title,
            "source_url": source.get("url"),
            "source_page_url": source.get("source_page_url") or source.get("url"),
            "work_id": passage.get("work_id") or source.get("work_id") or work.get("work_id"),
            "work_title": work_title,
            "work_title_ar": work.get("title_ar"),
            "author_id": work.get("author_id") or source.get("author_id") or passage.get("author_id"),
            "author_name": author_name,
            "page_ref": page_ref,
            "location_label": location,
            "citation_hint": citation,
        }
