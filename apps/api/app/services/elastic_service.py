import json
import re
from collections import Counter
from datetime import datetime, timezone
from math import sqrt

try:
    from elasticsearch import Elasticsearch
except ImportError:  # pragma: no cover - exercised only when optional dependency is absent
    Elasticsearch = None

from app.data.seed import AUTHORS, EDGES, PASSAGES, SOURCES, WORKS
from app.models import (
    ElasticBootstrapResult,
    EvidenceItem,
    EvidenceMemoryRecord,
    SearchPlanItem,
    SearchType,
)
from app.services.normalization import normalize_arabic, tokenize
from app.services.scoring import confidence_score
from app.services.elastic_preview import ElasticBackupPreview
from app.services.catalog_quality import enrich_catalog_quality
from app.settings import Settings

ELASTIC_SCHEMA_VERSION = "2026-06-catalog-curator-v1"
INDEX_ALIASES = {
    "runs": "hermeneut_runs_current",
    "passages": "hermeneut_passages_current",
    "sources": "hermeneut_sources_current",
    "catalog_proposals": "hermeneut_catalog_proposals_current",
    "catalog_analysis_jobs": "hermeneut_catalog_analysis_jobs_current",
}
VERSIONED_INDICES = {
    "runs": "hermeneut_runs_v2",
    "catalog_proposals": "hermeneut_catalog_proposals_v1",
    "catalog_analysis_jobs": "hermeneut_catalog_analysis_jobs_v1",
}

INDEX_MAPPINGS = {
    "hermeneut_authors": {
        "mappings": {
            "properties": {
                "author_id": {"type": "keyword"},
                "name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "name_ar": {"type": "text"},
                "aliases": {"type": "text"},
                "death_year": {"type": "integer"},
                "period": {"type": "keyword"},
                "tradition": {"type": "keyword"},
            }
        }
    },
    "hermeneut_works": {
        "mappings": {
            "properties": {
                "work_id": {"type": "keyword"},
                "title": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "title_ar": {"type": "text"},
                "author_id": {"type": "keyword"},
                "domain": {"type": "keyword"},
                "language": {"type": "keyword"},
                "source_status": {"type": "keyword"},
                "library_id": {"type": "keyword"},
                "visibility": {"type": "keyword"},
                "license_status": {"type": "keyword"},
                "institution_owned": {"type": "boolean"},
                "ingestion_status": {"type": "keyword"},
            }
        }
    },
    "hermeneut_sources": {
        "mappings": {
            "properties": {
                "source_id": {"type": "keyword"},
                "work_id": {"type": "keyword"},
                "provider": {"type": "keyword"},
                "url": {"type": "keyword"},
                "source_page_url": {"type": "keyword"},
                "file_type": {"type": "keyword"},
                "license_note": {"type": "text"},
                "quality": {"type": "float"},
                "title": {"type": "text"},
                "host": {"type": "keyword"},
                "library_id": {"type": "keyword"},
                "visibility": {"type": "keyword"},
                "license_status": {"type": "keyword"},
                "institution_owned": {"type": "boolean"},
                "ingestion_status": {"type": "keyword"},
                "lifecycle_status": {"type": "keyword"},
                "download_url": {"type": "keyword"},
                "grounding_urls": {"type": "keyword"},
                "grounding_metadata": {"type": "object", "enabled": False},
                "ocr_status": {"type": "keyword"},
                "verification_status": {"type": "keyword"},
                "gcs_raw_path": {"type": "keyword"},
                "gcs_ocr_path": {"type": "keyword"},
                "gcs_normalized_path": {"type": "keyword"},
            }
        }
    },
    "hermeneut_passages": {
        "mappings": {
            "properties": {
                "passage_id": {"type": "keyword"},
                "text_raw": {"type": "text"},
                "text_normalized": {"type": "text"},
                "translation_hint": {"type": "text"},
                "work_id": {"type": "keyword"},
                "work_title": {"type": "text"},
                "work_title_ar": {"type": "text"},
                "author_id": {"type": "keyword"},
                "author_name": {"type": "text"},
                "domain": {"type": "keyword"},
                "source_id": {"type": "keyword"},
                "library_id": {"type": "keyword"},
                "concepts": {"type": "keyword"},
                "page_ref": {"type": "keyword"},
                "section_ref": {"type": "keyword"},
                "semantic_vector": {"type": "dense_vector", "dims": 8, "index": True, "similarity": "cosine"},
                "semantic_model": {"type": "keyword"},
                "source_page": {"type": "keyword"},
                "passage_order": {"type": "integer"},
                "chunk_index": {"type": "integer"},
                "ocr_confidence": {"type": "float"},
                "extraction_method": {"type": "keyword"},
                "gcs_raw_path": {"type": "keyword"},
                "gcs_ocr_path": {"type": "keyword"},
                "page_image_url": {"type": "keyword"},
            }
        }
    },
    "hermeneut_edges": {
        "mappings": {
            "properties": {
                "from": {"type": "keyword"},
                "to": {"type": "keyword"},
                "type": {"type": "keyword"},
                "edge_id": {"type": "keyword"},
                "from_type": {"type": "keyword"},
                "from_id": {"type": "keyword"},
                "to_type": {"type": "keyword"},
                "to_id": {"type": "keyword"},
                "relation": {"type": "keyword"},
                "source_url": {"type": "keyword"},
                "provenance": {"type": "keyword"},
                "confidence": {"type": "float"},
                "verification_status": {"type": "keyword"},
                "library_id": {"type": "keyword"},
                "direction": {"type": "keyword"},
                "evidence_snippet": {"type": "text"},
                "reasoning_summary": {"type": "text"},
                "chronology_status": {"type": "keyword"},
                "model_trace": {"type": "object", "enabled": False},
            }
        }
    },
    "hermeneut_evidence": {
        "mappings": {
            "properties": {
                "run_id": {"type": "keyword"},
                "query": {"type": "text"},
                "tool_used": {"type": "keyword"},
                "passage_id": {"type": "keyword"},
                "candidate_work": {"type": "keyword"},
                "confidence": {"type": "float"},
                "verification_note": {"type": "text"},
                "retrieval_mode": {"type": "keyword"},
                "relationship_fit_score": {"type": "float"},
                "model_trace": {"type": "object", "enabled": False},
            }
        }
    },
    "hermeneut_runs": {
        "mappings": {
            "properties": {
                "run_id": {"type": "keyword"},
                "status": {"type": "keyword"},
                "current_step": {"type": "keyword"},
                "progress_percent": {"type": "integer"},
                "estimated_remaining_seconds": {"type": "integer"},
                "input_passage": {"type": "text"},
                "timeline": {"type": "object", "enabled": False},
                "source_lifecycle_records": {"type": "object", "enabled": False},
                "trace_events": {"type": "object", "enabled": False},
                "mode": {"type": "keyword"},
                "current_phase": {"type": "keyword"},
                "blocked_reason": {"type": "text"},
                "grounded_search_queries": {"type": "text"},
                "run_doc": {"type": "object", "enabled": False},
                "run_doc_json": {"type": "text", "index": False},
                "payload_json": {"type": "text", "index": False},
                "attempt": {"type": "integer"},
                "locked_until": {"type": "date"},
                "last_error": {"type": "text"},
                "job_operation_name": {"type": "keyword"},
                "queued_at": {"type": "date"},
                "started_at": {"type": "date"},
                "completed_at": {"type": "date"},
                "worker_status": {"type": "keyword"},
                "schema_version": {"type": "keyword"},
                "updated_at": {"type": "date"},
            }
        }
    },
    "hermeneut_catalog_records": {
        "mappings": {
            "properties": {
                "catalog_id": {"type": "keyword"},
                "title": {"type": "text"},
                "variant_titles": {"type": "text"},
                "author": {"type": "text"},
                "dates": {"type": "keyword"},
                "subjects": {"type": "text"},
                "holding_institution": {"type": "text"},
                "shelfmark": {"type": "keyword"},
                "archive_number": {"type": "keyword"},
                "physical_description": {"type": "text"},
                "record_url": {"type": "keyword"},
                "protocol": {"type": "keyword"},
                "evidence_status": {"type": "keyword"},
                "query": {"type": "text"},
                "raw_payload": {"type": "object", "enabled": False},
                "created_at": {"type": "date"},
            }
        }
    },
    "hermeneut_ocr_corrections": {
        "mappings": {
            "properties": {
                "correction_id": {"type": "keyword"},
                "source_id": {"type": "keyword"},
                "library_id": {"type": "keyword"},
                "page_number": {"type": "integer"},
                "before_text": {"type": "text"},
                "after_text": {"type": "text"},
                "normalized_after": {"type": "text"},
                "editor_id": {"type": "keyword"},
                "correction_reason": {"type": "text"},
                "training_status": {"type": "keyword"},
                "ground_truth_path": {"type": "keyword"},
                "model_trace": {"type": "object", "enabled": False},
                "created_at": {"type": "date"},
            }
        }
    },
    "hermeneut_catalog_proposals": {
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "proposal_id": {"type": "keyword"},
                "analysis_job_id": {"type": "keyword"},
                "library_id": {"type": "keyword"},
                "source_id": {"type": "keyword"},
                "work_id": {"type": "keyword"},
                "proposal_type": {"type": "keyword"},
                "status": {"type": "keyword"},
                "risk_level": {"type": "keyword"},
                "confidence": {"type": "float"},
                "current_value": {"type": "object", "enabled": False},
                "proposed_value": {"type": "object", "enabled": False},
                "reasoning": {"type": "text"},
                "evidence": {"type": "object", "enabled": False},
                "affected_records": {"type": "keyword"},
                "model_used": {"type": "keyword"},
                "model_route": {"type": "keyword"},
                "prompt_profile": {"type": "keyword"},
                "analysis_version": {"type": "keyword"},
                "suppression_key": {"type": "keyword"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                "decision_audit": {"type": "object", "enabled": False},
            },
        }
    },
    "hermeneut_catalog_analysis_jobs": {
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "analysis_job_id": {"type": "keyword"},
                "job_kind": {"type": "keyword"},
                "library_id": {"type": "keyword"},
                "source_id": {"type": "keyword"},
                "status": {"type": "keyword"},
                "flash_model": {"type": "keyword"},
                "pro_model": {"type": "keyword"},
                "proposal_count": {"type": "integer"},
                "error": {"type": "text"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
            },
        }
    },
}


class ElasticService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        self.preview = ElasticBackupPreview()
        if Elasticsearch and settings.elasticsearch_url and settings.elasticsearch_api_key:
            self.client = Elasticsearch(
                settings.elasticsearch_url,
                api_key=settings.elasticsearch_api_key,
                request_timeout=10,
            )

    def mode(self) -> str:
        if self.client and self.health() == "connected":
            return "elasticsearch"
        return "elastic_backup_preview" if self.preview.available else "seed-memory"

    def health(self) -> str:
        if not self.client:
            return "seed-memory"
        try:
            return "connected" if self.client.ping() else "unreachable"
        except Exception:
            return "unreachable"

    def schema_status(self) -> dict:
        if not self.client or self.health() != "connected":
            return {
                "version": ELASTIC_SCHEMA_VERSION,
                "aliases": {},
                "run_snapshot_store": "seed-memory",
                "migration": "not_configured",
            }
        aliases: dict[str, str] = {}
        for key, alias in INDEX_ALIASES.items():
            aliases[key] = self._alias_target(alias) or "missing"
        run_target = aliases.get("runs")
        return {
            "version": ELASTIC_SCHEMA_VERSION,
            "aliases": aliases,
            "run_snapshot_store": "connected" if run_target and run_target != "missing" else "legacy_or_missing",
            "migration": "ready",
        }

    def ensure_run_schema(self, dry_run: bool = False) -> dict:
        operations: list[str] = []
        if not self.client or self.health() != "connected":
            return {
                "schema_version": ELASTIC_SCHEMA_VERSION,
                "dry_run": dry_run,
                "operations": ["skip: elasticsearch is not connected"],
                "aliases": {},
            }

        versioned_index = VERSIONED_INDICES["runs"]
        alias = INDEX_ALIASES["runs"]
        legacy_index = "hermeneut_runs"
        if not self.client.indices.exists(index=versioned_index):
            operations.append(f"create index {versioned_index}")
            if not dry_run:
                self.client.indices.create(index=versioned_index, **INDEX_MAPPINGS["hermeneut_runs"])
        else:
            operations.append(f"update mapping {versioned_index}")
            if not dry_run:
                self._update_run_mapping(versioned_index)

        if self.client.indices.exists(index=legacy_index):
            try:
                target_count = int(self.client.count(index=versioned_index)["count"]) if self.client.indices.exists(index=versioned_index) else 0
            except Exception:
                target_count = 0
            if target_count == 0:
                operations.append(f"reindex {legacy_index} -> {versioned_index}")
                if not dry_run:
                    self.client.reindex(
                        body={
                            "source": {"index": legacy_index},
                            "dest": {"index": versioned_index},
                            "script": {
                                "lang": "painless",
                                "source": "ctx._source.schema_version = params.version",
                                "params": {"version": ELASTIC_SCHEMA_VERSION},
                            },
                        },
                        wait_for_completion=True,
                        refresh=True,
                    )

        current_target = self._alias_target(alias)
        if current_target != versioned_index:
            operations.append(f"point alias {alias} -> {versioned_index}")
            if not dry_run:
                actions = []
                if current_target:
                    actions.append({"remove": {"index": current_target, "alias": alias}})
                actions.append({"add": {"index": versioned_index, "alias": alias}})
                self.client.indices.update_aliases(body={"actions": actions})

        for key, index_name in {"passages": "hermeneut_passages", "sources": "hermeneut_sources"}.items():
            alias_name = INDEX_ALIASES[key]
            if self.client.indices.exists(index=index_name) and self._alias_target(alias_name) != index_name:
                operations.append(f"point alias {alias_name} -> {index_name}")
                if not dry_run:
                    current = self._alias_target(alias_name)
                    actions = []
                    if current:
                        actions.append({"remove": {"index": current, "alias": alias_name}})
                    actions.append({"add": {"index": index_name, "alias": alias_name}})
                self.client.indices.update_aliases(body={"actions": actions})

        for key in ("catalog_proposals", "catalog_analysis_jobs"):
            index_name = VERSIONED_INDICES[key]
            alias_name = INDEX_ALIASES[key]
            mapping_name = "hermeneut_catalog_proposals" if key == "catalog_proposals" else "hermeneut_catalog_analysis_jobs"
            if not self.client.indices.exists(index=index_name):
                operations.append(f"create index {index_name}")
                if not dry_run:
                    self.client.indices.create(index=index_name, **INDEX_MAPPINGS[mapping_name])
            current = self._alias_target(alias_name)
            if current != index_name:
                operations.append(f"point alias {alias_name} -> {index_name}")
                if not dry_run:
                    actions = []
                    if current:
                        actions.append({"remove": {"index": current, "alias": alias_name}})
                    actions.append({"add": {"index": index_name, "alias": alias_name}})
                    self.client.indices.update_aliases(body={"actions": actions})

        return {
            "schema_version": ELASTIC_SCHEMA_VERSION,
            "dry_run": dry_run,
            "operations": operations or ["no-op"],
            "aliases": self.schema_status().get("aliases", {}),
        }

    def _alias_target(self, alias: str) -> str | None:
        if not self.client:
            return None
        try:
            if not self.client.indices.exists_alias(name=alias):
                return None
            response = self.client.indices.get_alias(name=alias)
            targets = list(response.keys())
            return targets[0] if targets else None
        except Exception:
            return None

    def _run_write_index(self) -> str:
        return self._alias_target(INDEX_ALIASES["runs"]) or (
            VERSIONED_INDICES["runs"]
            if self.client and self.client.indices.exists(index=VERSIONED_INDICES["runs"])
            else "hermeneut_runs"
        )

    def _run_read_indices(self) -> list[str]:
        indices: list[str] = []
        alias_target = self._alias_target(INDEX_ALIASES["runs"])
        if alias_target:
            indices.append(INDEX_ALIASES["runs"])
        for index_name in (VERSIONED_INDICES["runs"], "hermeneut_runs"):
            try:
                if self.client and self.client.indices.exists(index=index_name) and index_name not in indices:
                    indices.append(index_name)
            except Exception:
                pass
        return indices

    def _update_run_mapping(self, index_name: str) -> None:
        try:
            self.client.indices.put_settings(
                index=index_name,
                settings={"index.mapping.total_fields.limit": 5000},
            )
        except Exception:
            pass
        try:
            self.client.indices.put_mapping(
                index=index_name,
                properties=INDEX_MAPPINGS["hermeneut_runs"]["mappings"]["properties"],
            )
        except Exception:
            pass

    def bootstrap_seed_corpus(self) -> ElasticBootstrapResult:
        if not self.client or self.health() != "connected":
            return ElasticBootstrapResult(
                mode=self.mode(),
                indexed=False,
                indices={},
                note="Elasticsearch is not configured or not reachable; seed-memory fallback is active.",
            )

        for index_name, mapping in INDEX_MAPPINGS.items():
            if not self.client.indices.exists(index=index_name):
                self.client.indices.create(index=index_name, **mapping)
            else:
                self.client.indices.put_mapping(index=index_name, **mapping["mappings"])

        self._index_seed_docs()
        self.client.indices.refresh(index="hermeneut_*")
        counts = self.index_counts()
        return ElasticBootstrapResult(
            mode="elasticsearch",
            indexed=True,
            indices=counts,
            note="Seed author, work, source, edge, and passage documents are indexed in Elasticsearch.",
        )

    def index_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        if not self.client or self.health() != "connected":
            return counts
        for index_name in INDEX_MAPPINGS:
            try:
                counts[index_name] = int(self.client.count(index=index_name)["count"])
            except Exception:
                counts[index_name] = 0
        return counts

    def search_library(self, query: str) -> dict:
        if self.client and self.health() == "connected":
            self._ensure_seed_indexed()
            if query.strip():
                return enrich_catalog_quality(self._search_library_elastic(query))
            return enrich_catalog_quality(self.library_catalog_summary())

        if self.preview.available:
            return enrich_catalog_quality(self.preview.catalog(query))

        query_norm = normalize_arabic(query)

        def matches(item: dict) -> bool:
            haystack = normalize_arabic(" ".join(str(v) for v in item.values()))
            return query_norm in haystack or any(token in haystack for token in query_norm.split())

        result = {
            "authors": [author for author in AUTHORS if matches(author)],
            "works": [work for work in WORKS if matches(work)],
            "sources": [source for source in SOURCES if matches(source)],
            "edges": [edge for edge in EDGES if query_norm in normalize_arabic(" ".join(edge.values()))],
        }
        result["meta"] = {
            "backend": "seed_fallback",
            "counts": {
                "authors": len(AUTHORS),
                "works": len(WORKS),
                "sources": len(SOURCES),
                "edges": len(EDGES),
                "passages": len(PASSAGES),
            },
        }
        return enrich_catalog_quality(result)

    def library_catalog_summary(self) -> dict:
        counts = self.index_counts()
        sources = self._recent_sources(limit=18)
        works = self._top_documents("hermeneut_works", "work_id", limit=18)
        authors = self._top_documents("hermeneut_authors", "author_id", limit=12)
        edges = self._top_documents("hermeneut_edges", "edge_id", limit=18, sort_field="confidence")
        libraries = self._library_buckets()
        return {
            "meta": {
                "backend": "elasticsearch",
                "health": self.health(),
                "counts": {
                    "authors": counts.get("hermeneut_authors", 0),
                    "works": counts.get("hermeneut_works", 0),
                    "sources": counts.get("hermeneut_sources", 0),
                    "passages": counts.get("hermeneut_passages", 0),
                    "edges": counts.get("hermeneut_edges", 0),
                    "evidence": counts.get("hermeneut_evidence", 0),
                    "runs": counts.get("hermeneut_runs", 0),
                },
            },
            "libraries": libraries,
            "sources": sources,
            "works": works,
            "authors": authors,
            "edges": edges,
            "passages": [],
        }

    def library_scope(self, library_id: str) -> dict:
        if self.client and self.health() == "connected":
            self._ensure_seed_indexed()
            try:
                result = self.client.search(
                    index="hermeneut_passages",
                    size=0,
                    query={"term": {"library_id": library_id}},
                    aggs={
                        "works": {"cardinality": {"field": "work_id"}},
                        "sources": {"cardinality": {"field": "source_id"}},
                    },
                )
                return {
                    "library_id": library_id,
                    "passage_count": int(result["hits"]["total"]["value"]),
                    "work_count": int(result["aggregations"]["works"]["value"]),
                    "source_count": int(result["aggregations"]["sources"]["value"]),
                    "backend": "elasticsearch",
                }
            except Exception as exc:
                return {"library_id": library_id, "backend": "elasticsearch", "error": str(exc)}

        scoped = [
            passage
            for passage in PASSAGES
            if (self.work_by_id(passage["work_id"]) or {}).get("library_id", "demo_kalam") == library_id
        ]
        return {
            "library_id": library_id,
            "passage_count": len(scoped),
            "work_count": len({passage["work_id"] for passage in scoped}),
            "source_count": len({passage["source_id"] for passage in scoped}),
            "backend": "seed-memory",
        }

    def lookup_sources(self, query: str, library_id: str | None = None) -> list[dict]:
        if self.client and self.health() == "connected":
            self._ensure_seed_indexed()
            filters = []
            if library_id:
                filters.append({"term": {"library_id": library_id}})
            result = self.client.search(
                index="hermeneut_sources",
                size=10,
                query={
                    "bool": {
                        "must": [
                            {
                                "multi_match": {
                                    "query": query,
                                    "fields": ["source_id^3", "work_id^3", "provider", "license_note"],
                                    "lenient": True,
                                }
                            }
                        ],
                        "filter": filters,
                    }
                },
            )
            hits = [hit["_source"] | {"elastic_score": hit.get("_score")} for hit in result["hits"]["hits"]]
            return hits or self._seed_source_lookup(query, library_id)

        return self._seed_source_lookup(query, library_id)

    def lookup_research_graph(self, query: str) -> list[dict]:
        if self.client and self.health() == "connected":
            self._ensure_seed_indexed()
            result = self.client.search(
                index="hermeneut_edges",
                size=20,
                query={
                    "multi_match": {
                        "query": query,
                        "fields": ["from^3", "to^3", "type^2", "from_id^3", "to_id^3", "relation^2"],
                        "lenient": True,
                    }
                },
            )
            hits = [hit["_source"] | {"elastic_score": hit.get("_score")} for hit in result["hits"]["hits"]]
            return hits or self._seed_graph_lookup(query)

        return self._seed_graph_lookup(query)

    def library_relationship_graph(self, library_id: str) -> list[dict]:
        if self.client and self.health() == "connected":
            self._ensure_seed_indexed()
            result = self.client.search(
                index="hermeneut_edges",
                size=100,
                query={"term": {"library_id": library_id}},
                sort=[{"confidence": {"order": "desc"}}],
            )
            return [hit["_source"] | {"elastic_score": hit.get("_score")} for hit in result["hits"]["hits"]]
        return self.preview.relationships(library_id) if self.preview.available else []

    def library_sources(self, library_id: str, limit: int = 50) -> list[dict]:
        if self.client and self.health() == "connected":
            self._ensure_seed_indexed()
            result = self.client.search(
                index="hermeneut_sources",
                size=limit,
                query={"term": {"library_id": library_id}},
                sort=[{"quality": {"order": "desc", "unmapped_type": "float"}}],
            )
            return [hit["_source"] | {"elastic_score": hit.get("_score")} for hit in result["hits"]["hits"]]
        return self.preview.sources(library_id)[:limit] if self.preview.available else []

    def _recent_sources(self, limit: int = 18) -> list[dict]:
        if not self.client or self.health() != "connected":
            return []
        result = self.client.search(
            index="hermeneut_sources",
            size=limit,
            query={"match_all": {}},
            sort=[
                {"indexed_passage_count": {"order": "desc", "unmapped_type": "integer"}},
                {"quality": {"order": "desc", "unmapped_type": "float"}},
            ],
        )
        return [hit["_source"] | {"elastic_score": hit.get("_score")} for hit in result["hits"]["hits"]]

    def _top_documents(self, index_name: str, id_field: str, limit: int = 12, sort_field: str | None = None) -> list[dict]:
        if not self.client or self.health() != "connected":
            return []
        sort = [{sort_field: {"order": "desc", "unmapped_type": "float"}}] if sort_field else None
        kwargs = {"index": index_name, "size": limit, "query": {"match_all": {}}}
        if sort:
            kwargs["sort"] = sort
        result = self.client.search(**kwargs)
        rows = []
        for hit in result["hits"]["hits"]:
            source = hit["_source"]
            if source.get(id_field):
                rows.append(source | {"elastic_score": hit.get("_score")})
        return rows

    def _library_buckets(self) -> list[dict]:
        if not self.client or self.health() != "connected":
            return []
        result = self.client.search(
            index="hermeneut_passages",
            size=0,
            query={"match_all": {}},
            aggs={
                "libraries": {
                    "terms": {"field": "library_id", "size": 20},
                    "aggs": {
                        "works": {"cardinality": {"field": "work_id"}},
                        "sources": {"cardinality": {"field": "source_id"}},
                    },
                }
            },
        )
        return [
            {
                "library_id": bucket["key"],
                "passage_count": int(bucket["doc_count"]),
                "work_count": int(bucket["works"]["value"]),
                "source_count": int(bucket["sources"]["value"]),
            }
            for bucket in result.get("aggregations", {}).get("libraries", {}).get("buckets", [])
        ]

    def library_passage_samples(self, library_id: str, per_source: int = 4, limit: int = 80) -> list[dict]:
        if not self.client or self.health() != "connected":
            return []
        self._ensure_seed_indexed()
        result = self.client.search(
            index="hermeneut_passages",
            size=limit,
            query={"term": {"library_id": library_id}},
            collapse={"field": "passage_id"},
            sort=[{"ocr_confidence": {"order": "desc", "unmapped_type": "float"}}],
        )
        samples: list[dict] = []
        counts: Counter[str] = Counter()
        for hit in result["hits"]["hits"]:
            source = hit["_source"]
            source_id = str(source.get("source_id", "unknown"))
            if counts[source_id] >= per_source:
                continue
            samples.append(source | {"elastic_score": hit.get("_score")})
            counts[source_id] += 1
        return samples

    def index_library_relationship_edges(self, library_id: str, edges: list[dict]) -> int:
        if not self.client or self.health() != "connected":
            return 0
        if not self.client.indices.exists(index="hermeneut_edges"):
            self.client.indices.create(index="hermeneut_edges", **INDEX_MAPPINGS["hermeneut_edges"])
        try:
            self.client.delete_by_query(
                index="hermeneut_edges",
                query={
                    "bool": {
                        "must": [
                            {"term": {"library_id": library_id}},
                            {"term": {"provenance": "structured_docx_layer_analysis"}},
                        ]
                    }
                },
                conflicts="proceed",
                refresh=True,
            )
        except Exception:
            pass
        for edge in edges:
            edge_id = edge.get("edge_id") or f"{library_id}-{edge.get('from_id')}-{edge.get('relation')}-{edge.get('to_id')}"
            self.client.index(index="hermeneut_edges", id=edge_id, document={**edge, "library_id": library_id})
        self.client.indices.refresh(index="hermeneut_edges")
        return len(edges)

    def index_gemini_library_relationship_edges(self, library_id: str, edges: list[dict]) -> int:
        if not self.client or self.health() != "connected":
            return 0
        if not self.client.indices.exists(index="hermeneut_edges"):
            self.client.indices.create(index="hermeneut_edges", **INDEX_MAPPINGS["hermeneut_edges"])
        try:
            self.client.delete_by_query(
                index="hermeneut_edges",
                query={
                    "bool": {
                        "must": [
                            {"term": {"library_id": library_id}},
                            {"term": {"provenance": "gemini_library_relationship_analyst"}},
                        ]
                    }
                },
                conflicts="proceed",
                refresh=True,
            )
        except Exception:
            pass
        for edge in edges:
            edge_id = edge.get("edge_id") or f"{library_id}-gemini-{edge.get('from_id')}-{edge.get('relation')}-{edge.get('to_id')}"
            self.client.index(index="hermeneut_edges", id=edge_id, document={**edge, "library_id": library_id})
        self.client.indices.refresh(index="hermeneut_edges")
        return len(edges)

    def index_approved_relationship_edge(self, library_id: str, edge: dict) -> bool:
        if not self.client or self.health() != "connected":
            return False
        if not self.client.indices.exists(index="hermeneut_edges"):
            self.client.indices.create(index="hermeneut_edges", **INDEX_MAPPINGS["hermeneut_edges"])
        edge_id = edge.get("edge_id") or f"{library_id}-approved-{edge.get('from_id')}-{edge.get('relation')}-{edge.get('to_id')}"
        self.client.index(index="hermeneut_edges", id=edge_id, document={**edge, "edge_id": edge_id, "library_id": library_id}, refresh=True)
        return True

    def _seed_source_lookup(self, query: str, library_id: str | None = None) -> list[dict]:
        query_norm = normalize_arabic(query)
        query_tokens = set(tokenize(query))

        def query_matches(values: dict) -> bool:
            haystack = normalize_arabic(" ".join(str(value) for value in values.values()))
            return query_norm in haystack or haystack in query_norm or bool(query_tokens & set(tokenize(haystack)))

        matching_work_ids = {
            work["work_id"]
            for work in WORKS
            if query_matches(work)
        }
        return [
            {
                **source,
                "resolution_backend": "seed-resolver",
                "resolution_reason": "Matched source metadata or inferred source from matching work metadata.",
            }
            for source in SOURCES
            if (not library_id or source.get("library_id") == library_id)
            and (query_matches(source) or source["work_id"] in matching_work_ids)
        ][:10]

    def _seed_graph_lookup(self, query: str) -> list[dict]:
        query_norm = normalize_arabic(query)
        query_tokens = set(tokenize(query))

        def query_matches(values: dict) -> bool:
            haystack = normalize_arabic(" ".join(str(value) for value in values.values()))
            return query_norm in haystack or haystack in query_norm or bool(query_tokens & set(tokenize(haystack)))

        matching_ids = {
            item["author_id"]
            for item in AUTHORS
            if query_matches(item)
        } | {
            item["work_id"]
            for item in WORKS
            if query_matches(item)
        }
        return [
            self._enrich_edge(edge, index)
            for index, edge in enumerate(EDGES)
            if query_matches(edge)
            or edge.get("from") in matching_ids
            or edge.get("to") in matching_ids
        ][:20]

    def lookup_evidence_memory(self, query: str) -> list[dict]:
        if self.client and self.health() == "connected":
            if not self.client.indices.exists(index="hermeneut_evidence"):
                return []
            result = self.client.search(
                index="hermeneut_evidence",
                size=10,
                query={
                    "multi_match": {
                        "query": query,
                        "fields": ["query^3", "passage_id^2", "candidate_work^2", "verification_note"],
                        "lenient": True,
                    }
                },
                sort=[{"confidence": {"order": "desc", "unmapped_type": "float"}}],
            )
            return [hit["_source"] | {"elastic_score": hit.get("_score")} for hit in result["hits"]["hits"]]

        return []

    def search_passages(
        self,
        passage: str,
        plan: list[SearchPlanItem],
        web_research: dict | None = None,
        library_id: str | None = None,
    ) -> list[EvidenceItem]:
        web_research = web_research or {}
        if self.client and self.health() == "connected":
            self._ensure_seed_indexed()
            return self._search_passages_elastic(passage, plan, web_research, library_id)

        evidence_by_passage: dict[str, EvidenceItem] = {}
        for plan_item in plan:
            for passage_doc in PASSAGES:
                if library_id and passage_doc.get("library_id", "demo_kalam") != library_id:
                    work = self.work_by_id(passage_doc["work_id"]) or {}
                    if work.get("library_id", "demo_kalam") != library_id:
                        continue
                lexical = self._lexical_similarity(plan_item.query, passage_doc["text_raw"])
                semantic = self._semantic_similarity(plan_item.query, passage_doc)
                metadata = self._metadata_fit(plan_item.query, passage_doc)
                context = self._citation_context_fit(passage, passage_doc)
                source_quality = self._source_quality(passage_doc["source_id"])
                relationship = self._relationship_fit(passage_doc["work_id"], web_research)

                if plan_item.type == SearchType.lexical:
                    match_strength = lexical
                elif plan_item.type == SearchType.semantic:
                    match_strength = semantic
                elif plan_item.type == SearchType.metadata:
                    match_strength = metadata
                else:
                    match_strength = max(lexical, semantic) * 0.7 + metadata * 0.3

                if match_strength < 0.12:
                    continue

                confidence = confidence_score(lexical, semantic, metadata, context, source_quality, relationship)
                existing = evidence_by_passage.get(passage_doc["passage_id"])
                if existing and existing.confidence >= confidence:
                    continue

                evidence_by_passage[passage_doc["passage_id"]] = EvidenceItem(
                    evidence_id=f"ev-{passage_doc['passage_id']}",
                    passage_id=passage_doc["passage_id"],
                    work_id=passage_doc["work_id"],
                    work_title=passage_doc.get("work_title"),
                    author_name=passage_doc.get("author_name"),
                    **self._evidence_location_fields(passage_doc),
                    **self._evidence_anchor_fields(passage_doc, passage_doc["text_raw"]),
                    match_type=plan_item.type.value,
                    quote=passage_doc["text_raw"],
                    translation_hint=passage_doc.get("translation_hint"),
                    lexical_score=round(lexical, 3),
                    semantic_score=round(semantic, 3),
                    metadata_score=round(metadata, 3),
                    citation_context_score=round(context, 3),
                    source_quality_score=round(source_quality, 3),
                    relationship_fit_score=round(relationship, 3),
                    retrieval_mode="hybrid",
                    confidence=confidence,
                    explanation=self._explain_match(plan_item, passage_doc, confidence),
                    retrieval_backend="seed-memory",
                    elastic_index=None,
                    elastic_score=None,
                    tool_trace={
                        "backend": "seed-memory",
                        "query_type": plan_item.type.value,
                        "query": plan_item.query,
                        "purpose": plan_item.purpose,
                        "relationship_fit": relationship,
                        "note": "Local deterministic fallback; no external Elastic call was made.",
                    },
                    model_trace={
                        "research_model": self.settings.gemini_research_model,
                        "report_model": self.settings.gemini_report_model,
                        "embedding_model": self.settings.gemini_embedding_model,
                    },
                )

        return sorted(evidence_by_passage.values(), key=lambda item: item.confidence, reverse=True)

    def semantic_passage_lookup(
        self,
        query: str,
        web_research: dict | None = None,
        limit: int = 5,
        library_id: str | None = None,
    ) -> list[EvidenceItem]:
        web_research = web_research or {}
        query_vector = self._semantic_vector(query)
        plan_item = SearchPlanItem(
            query=query,
            type=SearchType.semantic,
            purpose="Elastic dense-vector semantic retrieval over passage embeddings.",
        )

        if self.client and self.health() == "connected":
            self._ensure_seed_indexed()
            try:
                result = self.client.search(
                    index="hermeneut_passages",
                    size=limit,
                    knn={
                        "field": "semantic_vector",
                        "query_vector": query_vector,
                        "k": limit,
                        "num_candidates": max(20, limit * 4),
                        **({"filter": {"term": {"library_id": library_id}}} if library_id else {}),
                    },
                )
                return self._semantic_hits_to_evidence(result["hits"]["hits"], query, plan_item, web_research)
            except Exception:
                # Some serverless projects need a fresh mapping refresh before kNN is available; keep demo resilient.
                pass

        scored = []
        for passage_doc in PASSAGES:
            if library_id and passage_doc.get("library_id", "demo_kalam") != library_id:
                work = self.work_by_id(passage_doc["work_id"]) or {}
                if work.get("library_id", "demo_kalam") != library_id:
                    continue
            passage_vector = self._semantic_vector(
                " ".join(
                    [
                        passage_doc["text_raw"],
                        passage_doc.get("translation_hint", ""),
                        " ".join(passage_doc.get("concepts", [])),
                    ]
                )
            )
            score = self._dense_cosine(query_vector, passage_vector)
            scored.append((score, passage_doc))

        hits = [
            {"_source": passage_doc, "_index": None, "_id": passage_doc["passage_id"], "_score": score}
            for score, passage_doc in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]
        ]
        return self._semantic_hits_to_evidence(hits, query, plan_item, web_research, fallback=True)

    def write_evidence_memory(self, records: list[EvidenceMemoryRecord]) -> int:
        if not records:
            return 0
        if not self.client or self.health() != "connected":
            return 0

        if not self.client.indices.exists(index="hermeneut_evidence"):
            self.client.indices.create(index="hermeneut_evidence", **INDEX_MAPPINGS["hermeneut_evidence"])

        for record in records:
            doc_id = f"{record.run_id}-{record.passage_id}"
            document = {
                **record.model_dump(),
                "retrieval_mode": "hybrid",
                "relationship_fit_score": 0.0,
                "model_trace": {
                    "research_model": self.settings.gemini_research_model,
                    "report_model": self.settings.gemini_report_model,
                    "embedding_model": self.settings.gemini_embedding_model,
                },
            }
            self.client.index(index="hermeneut_evidence", id=doc_id, document=document)
        self.client.indices.refresh(index="hermeneut_evidence")
        return len(records)

    def index_source_metadata(self, source_doc: dict) -> bool:
        if not self.client or self.health() != "connected":
            return False
        if not self.client.indices.exists(index="hermeneut_sources"):
            self.client.indices.create(index="hermeneut_sources", **INDEX_MAPPINGS["hermeneut_sources"])
        self.client.index(index="hermeneut_sources", id=source_doc["source_id"], document=source_doc)
        self.client.indices.refresh(index="hermeneut_sources")
        return True

    def index_extracted_passages(self, source_doc: dict, passages: list[dict], replace_source: bool = True) -> int:
        if not self.client or self.health() != "connected":
            return 0
        if not self.client.indices.exists(index="hermeneut_passages"):
            self.client.indices.create(index="hermeneut_passages", **INDEX_MAPPINGS["hermeneut_passages"])
        if replace_source:
            try:
                self.client.delete_by_query(
                    index="hermeneut_passages",
                    query={"term": {"source_id": source_doc["source_id"]}},
                    conflicts="proceed",
                    refresh=True,
                )
            except Exception:
                pass
        count = 0
        for index, passage_doc in enumerate(passages, start=1):
            text_raw = passage_doc["text_raw"]
            document = {
                **passage_doc,
                "text_normalized": normalize_arabic(text_raw),
                "source_id": source_doc["source_id"],
                "work_id": source_doc.get("work_id", source_doc["source_id"]),
                "work_title": passage_doc.get("work_title") or source_doc.get("work_title") or source_doc.get("title") or source_doc.get("work_id"),
                "author_id": passage_doc.get("author_id") or source_doc.get("author_id"),
                "author_name": passage_doc.get("author_name") or source_doc.get("author_name") or "Unknown",
                "domain": passage_doc.get("domain", "classical texts"),
                "library_id": source_doc.get("library_id", "demo_kalam"),
                "page_ref": passage_doc.get("page_ref", f"ocr:{index}"),
                "source_page": passage_doc.get("source_page", str(index)),
                "passage_order": int(passage_doc.get("passage_order") or index),
                "chunk_index": int(passage_doc.get("chunk_index") or index),
                "ocr_confidence": passage_doc.get("ocr_confidence", 0.72),
                "ocr_quality_status": passage_doc.get("ocr_quality_status") or source_doc.get("ocr_quality_status"),
                "source_role": passage_doc.get("source_role") or source_doc.get("source_role"),
                "source_role_group": passage_doc.get("source_role_group") or source_doc.get("source_role_group"),
                "source_resolution_query": passage_doc.get("source_resolution_query") or source_doc.get("source_resolution_query"),
                "source_candidate_rank": passage_doc.get("source_candidate_rank") or source_doc.get("source_candidate_rank"),
                "source_title": source_doc.get("title"),
                "source_url": source_doc.get("url"),
                "source_page_url": source_doc.get("source_page_url") or source_doc.get("url"),
                "extraction_method": passage_doc.get("extraction_method", "text_layer_or_ocr"),
                "gcs_raw_path": source_doc.get("gcs_raw_path"),
                "gcs_ocr_path": source_doc.get("gcs_ocr_path"),
                "source_quality": source_doc.get("quality", 0.72),
                "semantic_vector": self._semantic_vector(
                    " ".join([text_raw, passage_doc.get("translation_hint", ""), " ".join(passage_doc.get("concepts", []))])
                ),
                "semantic_model": self.settings.gemini_embedding_model,
            }
            passage_id = passage_doc.get("passage_id") or f"{source_doc['source_id']}-ocr-{index}"
            document["passage_id"] = passage_id
            self.client.index(index="hermeneut_passages", id=passage_id, document=document)
            count += 1
        self.client.indices.refresh(index="hermeneut_passages")
        return count

    def delete_source_page_passages(self, source_id: str, page_number: int) -> int:
        if not self.client or self.health() != "connected":
            return 0
        if not self.client.indices.exists(index="hermeneut_passages"):
            return 0
        try:
            result = self.client.delete_by_query(
                index="hermeneut_passages",
                query={
                    "bool": {
                        "must": [
                            {"term": {"source_id": source_id}},
                            {"term": {"source_page": str(page_number)}},
                        ]
                    }
                },
                conflicts="proceed",
                refresh=True,
            )
            return int(result.get("deleted", 0))
        except Exception:
            return 0

    def index_catalog_records(self, records: list[dict]) -> int:
        if not self.client or self.health() != "connected":
            return 0
        if not self.client.indices.exists(index="hermeneut_catalog_records"):
            self.client.indices.create(index="hermeneut_catalog_records", **INDEX_MAPPINGS["hermeneut_catalog_records"])
        for record in records:
            self.client.index(index="hermeneut_catalog_records", id=record["catalog_id"], document=record)
        self.client.indices.refresh(index="hermeneut_catalog_records")
        return len(records)

    def search_catalog_records(self, query: str, limit: int = 20) -> list[dict]:
        if not self.client or self.health() != "connected":
            return []
        if not self.client.indices.exists(index="hermeneut_catalog_records"):
            return []
        result = self.client.search(
            index="hermeneut_catalog_records",
            size=limit,
            query={
                "multi_match": {
                    "query": query,
                    "fields": ["title^3", "variant_titles", "author^2", "subjects", "shelfmark", "holding_institution"],
                    "lenient": True,
                }
            },
        )
        return [hit["_source"] | {"elastic_score": hit.get("_score")} for hit in result["hits"]["hits"]]

    def index_ocr_correction(self, correction: dict) -> bool:
        if not self.client or self.health() != "connected":
            return False
        if not self.client.indices.exists(index="hermeneut_ocr_corrections"):
            self.client.indices.create(index="hermeneut_ocr_corrections", **INDEX_MAPPINGS["hermeneut_ocr_corrections"])
        self.client.index(index="hermeneut_ocr_corrections", id=correction["correction_id"], document=correction)
        self.client.indices.refresh(index="hermeneut_ocr_corrections")
        return True

    def lookup_ocr_corrections(self, source_id: str, page_number: int | None = None) -> list[dict]:
        if not self.client or self.health() != "connected":
            return []
        if not self.client.indices.exists(index="hermeneut_ocr_corrections"):
            return []
        filters = [{"term": {"source_id": source_id}}]
        if page_number is not None:
            filters.append({"term": {"page_number": page_number}})
        result = self.client.search(
            index="hermeneut_ocr_corrections",
            size=50,
            query={"bool": {"filter": filters}},
            sort=[{"created_at": {"order": "desc"}}],
        )
        return [hit["_source"] for hit in result["hits"]["hits"]]

    def write_run_snapshot(self, run, payload=None, metadata: dict | None = None) -> bool:
        if not self.client or self.health() != "connected":
            return False
        index_name = self._run_write_index()
        if not self.client.indices.exists(index=index_name):
            self.client.indices.create(index=index_name, **INDEX_MAPPINGS["hermeneut_runs"])
        else:
            self._update_run_mapping(index_name)
        run_doc = run.model_dump(mode="json")
        now = datetime.now(timezone.utc).isoformat()
        payload_doc = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        existing_payload_json = None if payload_doc is not None else self._existing_run_field(run.run_id, "payload_json")
        metadata = {**self.get_run_metadata(run.run_id), **dict(metadata or {})}
        document = {
            "run_id": run_doc["run_id"],
            "status": run_doc["status"],
            "current_step": run_doc["current_step"],
            "progress_percent": run_doc["progress_percent"],
            "estimated_remaining_seconds": run_doc["estimated_remaining_seconds"],
            "input_passage": run_doc["input_passage"],
            "timeline": run_doc.get("timeline", []),
            "source_lifecycle_records": run_doc.get("source_lifecycle_records", []),
            "trace_events": run_doc.get("trace_events", []),
            "mode": run_doc.get("mode"),
            "current_phase": run_doc.get("current_phase"),
            "blocked_reason": run_doc.get("blocked_reason"),
            "run_doc": run_doc,
            "run_doc_json": json.dumps(run_doc, ensure_ascii=False),
            "payload_json": json.dumps(payload_doc, ensure_ascii=False) if payload_doc is not None else existing_payload_json,
            "schema_version": ELASTIC_SCHEMA_VERSION,
            **metadata,
        }
        document["updated_at"] = now
        if run_doc["status"] == "queued" and not document.get("queued_at"):
            document["queued_at"] = now
        if run_doc["status"] == "running" and not document.get("started_at"):
            document["started_at"] = now
        if run_doc["status"] in {"completed", "failed"} and not document.get("completed_at"):
            document["completed_at"] = now
        document["grounded_search_queries"] = [
            question
            for event in run.timeline
            for question in event.payload.get("research_questions", [])
            if isinstance(question, str)
        ]
        document = {key: value for key, value in document.items() if value is not None}
        try:
            self.client.index(index=index_name, id=run.run_id, document=document)
        except Exception:
            minimal_document = {
                key: value
                for key, value in document.items()
                if key
                in {
                    "run_id",
                    "status",
                    "current_step",
                    "progress_percent",
                    "estimated_remaining_seconds",
                    "input_passage",
                    "mode",
                    "current_phase",
                    "blocked_reason",
                    "grounded_search_queries",
                    "run_doc_json",
                    "payload_json",
                    "attempt",
                    "locked_until",
                    "last_error",
                    "job_operation_name",
                    "queued_at",
                    "started_at",
                    "completed_at",
                    "worker_status",
                    "schema_version",
                    "updated_at",
                }
            }
            try:
                self.client.index(index=index_name, id=run.run_id, document=minimal_document)
            except Exception:
                return False
        try:
            self.client.indices.refresh(index=index_name)
        except Exception:
            pass
        return True

    def get_run_snapshot(self, run_id: str):
        if not self.client or self.health() != "connected":
            return None
        from app.models import AgentRun

        for index_name in self._run_read_indices() or ["hermeneut_runs"]:
            try:
                result = self.client.get(index=index_name, id=run_id)
                if not result.get("found"):
                    continue
                source = result["_source"]
                run_doc = source.get("run_doc")
                if not run_doc and source.get("run_doc_json"):
                    run_doc = json.loads(source["run_doc_json"])
                if run_doc:
                    return AgentRun.model_validate(run_doc)
            except Exception:
                continue
        return None

    def get_run_payload(self, run_id: str):
        if not self.client or self.health() != "connected":
            return None
        from app.models import RunCreate

        for index_name in self._run_read_indices() or ["hermeneut_runs"]:
            try:
                result = self.client.get(index=index_name, id=run_id)
                if not result.get("found"):
                    continue
                payload_json = result["_source"].get("payload_json")
                if payload_json:
                    return RunCreate.model_validate(json.loads(payload_json))
            except Exception:
                continue
        return None

    def get_run_metadata(self, run_id: str) -> dict:
        if not self.client or self.health() != "connected":
            return {}
        metadata_keys = {
            "attempt",
            "locked_until",
            "last_error",
            "job_operation_name",
            "queued_at",
            "started_at",
            "completed_at",
            "worker_status",
            "schema_version",
        }
        for index_name in self._run_read_indices() or ["hermeneut_runs"]:
            try:
                result = self.client.get(index=index_name, id=run_id)
                if result.get("found"):
                    return {key: value for key, value in result["_source"].items() if key in metadata_keys}
            except Exception:
                continue
        return {}

    def _existing_run_field(self, run_id: str, field: str):
        for index_name in self._run_read_indices() or ["hermeneut_runs"]:
            try:
                result = self.client.get(index=index_name, id=run_id)
                if result.get("found"):
                    return result["_source"].get(field)
            except Exception:
                continue
        return None

    def work_by_id(self, work_id: str) -> dict | None:
        if self.client and self.health() == "connected":
            try:
                result = self.client.get(index="hermeneut_works", id=work_id)
                if result.get("found"):
                    return result["_source"]
            except Exception:
                try:
                    result = self.client.search(
                        index="hermeneut_works",
                        size=1,
                        query={"term": {"work_id": work_id}},
                    )
                    hits = result["hits"]["hits"]
                    if hits:
                        return hits[0]["_source"]
                except Exception:
                    pass
        return next((work for work in WORKS if work["work_id"] == work_id), None)

    def author_for_work(self, work_id: str) -> dict | None:
        work = self.work_by_id(work_id)
        if not work:
            return None
        author_id = work.get("author_id")
        if self.client and self.health() == "connected" and author_id:
            try:
                result = self.client.get(index="hermeneut_authors", id=author_id)
                if result.get("found"):
                    return result["_source"]
            except Exception:
                pass
        return next((author for author in AUTHORS if author["author_id"] == author_id), None)

    def _lexical_similarity(self, query: str, text: str) -> float:
        query_tokens = set(tokenize(query))
        text_tokens = set(tokenize(text))
        if not query_tokens or not text_tokens:
            return 0.0
        normalized_query = normalize_arabic(query)
        normalized_text = normalize_arabic(text)
        if normalized_query and normalized_query in normalized_text:
            return 1.0
        overlap = len(query_tokens & text_tokens)
        jaccard = overlap / len(query_tokens | text_tokens)
        coverage = overlap / len(query_tokens)
        return max(jaccard, coverage * 0.92)

    def _semantic_similarity(self, query: str, passage_doc: dict) -> float:
        query_tokens = Counter(tokenize(query))
        concept_tokens = Counter(token for concept in passage_doc["concepts"] for token in concept.split())
        text_tokens = Counter(tokenize(passage_doc["text_raw"]))
        combined = text_tokens + concept_tokens
        return self._cosine(query_tokens, combined)

    def _metadata_fit(self, query: str, passage_doc: dict) -> float:
        work = self.work_by_id(passage_doc["work_id"]) or {}
        author = self.author_for_work(passage_doc["work_id"]) or {}
        haystack = normalize_arabic(
            " ".join(
                [
                    passage_doc.get("work_title", ""),
                    passage_doc.get("author_name", ""),
                    passage_doc.get("domain", ""),
                    work.get("title", ""),
                    work.get("title_ar", ""),
                    work.get("domain", ""),
                    author.get("name", ""),
                    author.get("name_ar", ""),
                    author.get("tradition", ""),
                    " ".join(passage_doc.get("concepts", [])),
                ]
            )
        )
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return 0.0
        return min(1.0, sum(1 for token in query_tokens if token in haystack) / max(3, len(query_tokens)))

    def _enrich_passage_doc(self, passage_doc: dict) -> dict:
        work_id = passage_doc.get("work_id")
        source_id = passage_doc.get("source_id")
        work = self.work_by_id(work_id) if work_id else None
        author = self.author_for_work(work_id) if work_id else None
        source: dict = {}
        if self.client and self.health() == "connected" and source_id:
            try:
                result = self.client.get(index="hermeneut_sources", id=source_id)
                if result.get("found"):
                    source = result["_source"]
            except Exception:
                source = {}
        if not source and source_id:
            source = next((item for item in SOURCES if item["source_id"] == source_id), {})
        title = passage_doc.get("work_title")
        author_name = passage_doc.get("author_name")
        if not title or title == work_id:
            title = (work or {}).get("title") or source.get("title") or title
        if not author_name or author_name == "Unknown":
            author_name = (author or {}).get("name") or source.get("author_name") or author_name
        return {
            **passage_doc,
            "work_title": title,
            "work_title_ar": passage_doc.get("work_title_ar") or (work or {}).get("title_ar"),
            "author_id": passage_doc.get("author_id") or (author or {}).get("author_id") or (work or {}).get("author_id"),
            "author_name": author_name,
            "domain": passage_doc.get("domain") or (work or {}).get("domain") or source.get("domain") or "classical texts",
            "library_id": passage_doc.get("library_id") or (work or {}).get("library_id") or source.get("library_id", "demo_kalam"),
            "source_quality": passage_doc.get("source_quality") or source.get("quality", 0.5),
            "source_title": passage_doc.get("source_title") or source.get("title") or source.get("name") or source_id,
            "source_url": passage_doc.get("source_url") or source.get("url"),
            "source_page_url": passage_doc.get("source_page_url") or source.get("source_page_url") or source.get("url"),
            "page_ref": passage_doc.get("page_ref") or passage_doc.get("section_ref"),
            "source_page": passage_doc.get("source_page") or passage_doc.get("page_number"),
            "passage_order": passage_doc.get("passage_order") or passage_doc.get("chunk_index"),
            "chunk_index": passage_doc.get("chunk_index"),
            "ocr_confidence": passage_doc.get("ocr_confidence") or passage_doc.get("confidence") or source.get("ocr_avg_confidence"),
            "ocr_quality_status": passage_doc.get("ocr_quality_status") or source.get("ocr_quality_status"),
            "source_role": passage_doc.get("source_role") or source.get("source_role"),
            "source_role_group": passage_doc.get("source_role_group") or source.get("source_role_group"),
            "source_resolution_query": passage_doc.get("source_resolution_query") or source.get("source_resolution_query"),
            "source_candidate_rank": passage_doc.get("source_candidate_rank") or source.get("source_candidate_rank"),
            "page_image_url": passage_doc.get("page_image_url") or source.get("page_image_url"),
        }

    def _evidence_location_fields(self, passage_doc: dict) -> dict:
        author = passage_doc.get("author_name")
        work = passage_doc.get("work_title") or passage_doc.get("work_id")
        source = passage_doc.get("source_title") or passage_doc.get("source_id")
        page = passage_doc.get("page_ref") or passage_doc.get("source_page")
        parts = [str(part) for part in (author, work, source, page) if part]
        location_label = " · ".join(parts)
        citation_bits = [str(part) for part in (author, work) if part]
        if source:
            citation_bits.append(f"source: {source}")
        if page:
            citation_bits.append(f"loc. {page}")
        return {
            "library_id": passage_doc.get("library_id"),
            "source_id": passage_doc.get("source_id"),
            "source_title": passage_doc.get("source_title"),
            "source_url": self._public_url(passage_doc.get("source_url")),
            "source_page_url": self._public_url(passage_doc.get("source_page_url")),
            "author_id": passage_doc.get("author_id"),
            "page_ref": passage_doc.get("page_ref"),
            "source_page": passage_doc.get("source_page"),
            "location_label": location_label or passage_doc.get("passage_id"),
            "citation_hint": "; ".join(citation_bits),
            "ocr_confidence": passage_doc.get("ocr_confidence"),
            "source_role": passage_doc.get("source_role"),
            "source_resolution_query": passage_doc.get("source_resolution_query"),
            "source_candidate_rank": passage_doc.get("source_candidate_rank"),
            "ocr_quality_status": passage_doc.get("ocr_quality_status"),
        }

    def _evidence_anchor_fields(self, passage_doc: dict, quote: str | None = None) -> dict:
        text = str(passage_doc.get("text_raw") or "")
        quote_text = str(quote or text)
        start = text.find(quote_text) if quote_text else -1
        if start < 0 and quote_text and quote_text.strip() == text.strip():
            start = max(0, text.find(text.strip()))
        end = start + len(quote_text) if start >= 0 else None
        before = text[max(0, start - 90):start] if start >= 0 else None
        after = text[end:min(len(text), end + 90)] if end is not None else None
        page_image_url = self._public_url(passage_doc.get("page_image_url"))
        if passage_doc.get("page_ref"):
            locator_kind = "page_ref"
        elif passage_doc.get("source_page"):
            locator_kind = "source_page"
        else:
            locator_kind = "passage_id"
        verification_status = "anchored_quote" if start >= 0 else "unanchored_quote"
        if start >= 0 and not (passage_doc.get("page_ref") or passage_doc.get("source_page")):
            verification_status = "anchored_passage_only"
        return {
            "quote_start_char": start if start >= 0 else None,
            "quote_end_char": end,
            "anchor_text_before": before,
            "anchor_text_after": after,
            "page_image_url": page_image_url,
            "page_image_available": bool(page_image_url),
            "source_locator_kind": locator_kind,
            "verification_status": verification_status,
        }

    def _public_url(self, value: str | None) -> str | None:
        if not value:
            return None
        text = str(value)
        if text.startswith("gs://"):
            return None
        return text

    def _citation_context_fit(self, passage: str, passage_doc: dict) -> float:
        markers = ["قيل", "ذكر", "زعم", "قال", "ينسب", "بعضهم", "الفلاسفه", "المعتزله"]
        normalized = normalize_arabic(passage)
        marker_score = 0.35 if any(marker in normalized for marker in markers) else 0.15
        lexical = self._lexical_similarity(passage, passage_doc["text_raw"])
        return min(1.0, marker_score + lexical)

    def _source_quality(self, source_id: str) -> float:
        if self.client and self.health() == "connected":
            try:
                result = self.client.get(index="hermeneut_sources", id=source_id)
                if result.get("found"):
                    return float(result["_source"].get("quality", 0.5))
            except Exception:
                pass
        source = next((item for item in SOURCES if item["source_id"] == source_id), None)
        return float(source["quality"]) if source else 0.5

    def _cosine(self, left: Counter, right: Counter) -> float:
        if not left or not right:
            return 0.0
        dot = sum(left[key] * right[key] for key in left.keys() & right.keys())
        left_mag = sqrt(sum(value * value for value in left.values()))
        right_mag = sqrt(sum(value * value for value in right.values()))
        if left_mag == 0 or right_mag == 0:
            return 0.0
        return dot / (left_mag * right_mag)

    def _explain_match(self, plan_item: SearchPlanItem, passage_doc: dict, confidence: float) -> str:
        work = self.work_by_id(passage_doc["work_id"]) or {}
        title = passage_doc.get("work_title") or work.get("title") or passage_doc["work_id"]
        return (
            f"{plan_item.type.value} search linked the query to {title}; "
            f"confidence {confidence:.2f} reflects text overlap, concept fit, metadata, and source quality."
        )

    def _ensure_seed_indexed(self) -> None:
        try:
            count = self.client.count(index="hermeneut_passages")["count"]
        except Exception:
            count = 0
        if count < len(PASSAGES):
            self.bootstrap_seed_corpus()

    def _index_seed_docs(self) -> None:
        for author in AUTHORS:
            self.client.index(index="hermeneut_authors", id=author["author_id"], document=author)
        for work in WORKS:
            self.client.index(index="hermeneut_works", id=work["work_id"], document=work)
        for source in SOURCES:
            self.client.index(index="hermeneut_sources", id=source["source_id"], document=source)
        for edge_index, edge in enumerate(EDGES):
            self.client.index(
                index="hermeneut_edges",
                id=f"edge-{edge_index}",
                document=self._enrich_edge(edge, edge_index),
            )
        for passage_order, passage_doc in enumerate(PASSAGES, start=1):
            work = self.work_by_id(passage_doc["work_id"]) or {}
            author = self.author_for_work(passage_doc["work_id"]) or {}
            source = next(
                (item for item in SOURCES if item["source_id"] == passage_doc["source_id"]),
                {},
            )
            document = {
                **passage_doc,
                "text_normalized": normalize_arabic(passage_doc["text_raw"]),
                "work_title": work.get("title"),
                "work_title_ar": work.get("title_ar"),
                "author_id": author.get("author_id"),
                "author_name": author.get("name"),
                "domain": work.get("domain"),
                "library_id": work.get("library_id", "demo_kalam"),
                "source_quality": source.get("quality", 0.5),
                "passage_order": int(passage_doc.get("passage_order") or passage_order),
                "chunk_index": int(passage_doc.get("chunk_index") or passage_order),
                "semantic_vector": self._semantic_vector(
                    " ".join(
                        [
                            passage_doc["text_raw"],
                            passage_doc.get("translation_hint", ""),
                            " ".join(passage_doc.get("concepts", [])),
                        ]
                    )
                ),
                "semantic_model": self.settings.gemini_embedding_model,
            }
            self.client.index(
                index="hermeneut_passages",
                id=passage_doc["passage_id"],
                document=document,
            )

    def _enrich_edge(self, edge: dict, edge_index: int) -> dict:
        relation = edge.get("type", "RELATED_TO")
        return {
            **edge,
            "edge_id": f"edge-{edge_index}",
            "from_type": "author" if edge.get("from") in {author["author_id"] for author in AUTHORS} else "work",
            "from_id": edge.get("from"),
            "to_type": "work" if edge.get("to") in {work["work_id"] for work in WORKS} else "concept",
            "to_id": edge.get("to"),
            "relation": relation.lower(),
            "source_url": "https://openiti.org/",
            "provenance": "hermeneut_seed_graph",
            "confidence": 0.82 if relation == "AUTHOR_WROTE_WORK" else 0.68,
            "verification_status": "demo_curated",
        }

    def _search_library_elastic(self, query: str) -> dict:
        indices = {
            "authors": "hermeneut_authors",
            "works": "hermeneut_works",
            "sources": "hermeneut_sources",
            "edges": "hermeneut_edges",
            "passages": "hermeneut_passages",
        }
        response: dict[str, list[dict] | dict] = {}
        for key, index_name in indices.items():
            result = self.client.search(
                index=index_name,
                size=10 if key != "passages" else 8,
                query={
                    "multi_match": {
                        "query": query,
                        "fields": ["*"],
                        "lenient": True,
                    }
                },
            )
            rows = []
            for hit in result["hits"]["hits"]:
                row = hit["_source"]
                if key == "passages":
                    row = self._enrich_passage_doc(row)
                    row = row | self._evidence_location_fields(row)
                    row = row | self._evidence_anchor_fields(row, row.get("text_raw"))
                    row = {**row, "text_raw": row.get("text_raw", "")[:900]}
                rows.append(row | {"elastic_score": hit.get("_score")})
            response[key] = rows
        counts = self.index_counts()
        response["meta"] = {
            "backend": "elasticsearch",
            "query": query,
            "counts": {
                "authors": counts.get("hermeneut_authors", 0),
                "works": counts.get("hermeneut_works", 0),
                "sources": counts.get("hermeneut_sources", 0),
                "passages": counts.get("hermeneut_passages", 0),
                "edges": counts.get("hermeneut_edges", 0),
                "evidence": counts.get("hermeneut_evidence", 0),
            },
        }
        return response

    def _search_passages_elastic(
        self,
        passage: str,
        plan: list[SearchPlanItem],
        web_research: dict,
        library_id: str | None = None,
    ) -> list[EvidenceItem]:
        evidence_by_passage: dict[str, EvidenceItem] = {}
        for plan_item in plan:
            query = self._elastic_query(plan_item)
            if library_id:
                query = {"bool": {"must": [query], "filter": [{"term": {"library_id": library_id}}]}}
            result = self.client.search(index="hermeneut_passages", size=8, query=query)
            max_score = float(result["hits"].get("max_score") or 1.0)
            for hit in result["hits"]["hits"]:
                passage_doc = self._enrich_passage_doc(hit["_source"])
                elastic_score = float(hit.get("_score") or 0.0)
                normalized_elastic_score = elastic_score / max(max_score, 1.0)
                lexical = self._lexical_similarity(plan_item.query, passage_doc["text_raw"])
                semantic = max(
                    self._semantic_similarity(plan_item.query, passage_doc),
                    normalized_elastic_score * 0.8,
                )
                metadata = max(
                    self._metadata_fit(plan_item.query, passage_doc),
                    0.35 if plan_item.type == SearchType.metadata else 0.0,
                )
                context = self._citation_context_fit(passage, passage_doc)
                source_quality = float(passage_doc.get("source_quality", self._source_quality(passage_doc["source_id"])))
                relationship = self._relationship_fit(passage_doc["work_id"], web_research)
                confidence = confidence_score(
                    lexical,
                    semantic,
                    metadata,
                    context,
                    source_quality,
                    relationship,
                )
                existing = evidence_by_passage.get(passage_doc["passage_id"])
                if existing and existing.confidence >= confidence:
                    continue

                evidence_by_passage[passage_doc["passage_id"]] = EvidenceItem(
                    evidence_id=f"ev-{passage_doc['passage_id']}",
                    passage_id=passage_doc["passage_id"],
                    work_id=passage_doc["work_id"],
                    work_title=passage_doc.get("work_title"),
                    author_name=passage_doc.get("author_name"),
                    **self._evidence_location_fields(passage_doc),
                    **self._evidence_anchor_fields(passage_doc, passage_doc["text_raw"]),
                    match_type=plan_item.type.value,
                    quote=passage_doc["text_raw"],
                    translation_hint=passage_doc.get("translation_hint"),
                    lexical_score=round(lexical, 3),
                    semantic_score=round(semantic, 3),
                    metadata_score=round(metadata, 3),
                    citation_context_score=round(context, 3),
                    source_quality_score=round(source_quality, 3),
                    relationship_fit_score=round(relationship, 3),
                    retrieval_mode="hybrid",
                    confidence=confidence,
                    explanation=self._explain_match(plan_item, passage_doc, confidence),
                    retrieval_backend="elasticsearch",
                    elastic_index=hit["_index"],
                    elastic_score=round(elastic_score, 3),
                    tool_trace={
                        "backend": "elasticsearch",
                        "index": hit["_index"],
                        "document_id": hit["_id"],
                        "query_type": plan_item.type.value,
                        "query": plan_item.query,
                        "elastic_query": query,
                        "elastic_score": elastic_score,
                        "max_score": max_score,
                        "relationship_fit": relationship,
                    },
                    model_trace={
                        "research_model": self.settings.gemini_research_model,
                        "report_model": self.settings.gemini_report_model,
                        "embedding_model": self.settings.gemini_embedding_model,
                    },
                )

        return sorted(evidence_by_passage.values(), key=lambda item: item.confidence, reverse=True)

    def _semantic_hits_to_evidence(
        self,
        hits: list[dict],
        query: str,
        plan_item: SearchPlanItem,
        web_research: dict,
        fallback: bool = False,
    ) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        max_score = max((float(hit.get("_score") or 0.0) for hit in hits), default=1.0)
        for hit in hits:
            enriched_doc = self._enrich_passage_doc(hit["_source"])
            elastic_score = float(hit.get("_score") or 0.0)
            normalized_score = elastic_score / max(max_score, 1.0)
            lexical = self._lexical_similarity(query, enriched_doc["text_raw"])
            semantic = max(self._semantic_similarity(query, enriched_doc), normalized_score)
            metadata = self._metadata_fit(query, enriched_doc)
            context = self._citation_context_fit(query, enriched_doc)
            source_quality = float(enriched_doc.get("source_quality", self._source_quality(enriched_doc["source_id"])))
            relationship = self._relationship_fit(enriched_doc["work_id"], web_research)
            confidence = confidence_score(lexical, semantic, metadata, context, source_quality, relationship)
            evidence.append(
                EvidenceItem(
                    evidence_id=f"sem-{enriched_doc['passage_id']}",
                    passage_id=enriched_doc["passage_id"],
                    work_id=enriched_doc["work_id"],
                    work_title=enriched_doc.get("work_title"),
                    author_name=enriched_doc.get("author_name"),
                    **self._evidence_location_fields(enriched_doc),
                    **self._evidence_anchor_fields(enriched_doc, enriched_doc["text_raw"]),
                    match_type="semantic_vector",
                    quote=enriched_doc["text_raw"],
                    translation_hint=enriched_doc.get("translation_hint"),
                    lexical_score=round(lexical, 3),
                    semantic_score=round(semantic, 3),
                    metadata_score=round(metadata, 3),
                    citation_context_score=round(context, 3),
                    source_quality_score=round(source_quality, 3),
                    relationship_fit_score=round(relationship, 3),
                    retrieval_mode="semantic_vector",
                    confidence=confidence,
                    explanation=self._explain_match(plan_item, enriched_doc, confidence),
                    retrieval_backend="elasticsearch-knn" if not fallback else "seed-vector",
                    elastic_index=hit.get("_index"),
                    elastic_score=round(elastic_score, 3),
                    tool_trace={
                        "backend": "elasticsearch-knn" if not fallback else "seed-vector",
                        "tool": "hermeneut.semantic_passage_lookup",
                        "query_type": "semantic_vector",
                        "query": query,
                        "semantic_model": self.settings.gemini_embedding_model,
                        "semantic_vector_dims": 8,
                        "elastic_score": elastic_score,
                        "max_score": max_score,
                        "relationship_fit": relationship,
                    },
                    model_trace={
                        "research_model": self.settings.gemini_research_model,
                        "report_model": self.settings.gemini_report_model,
                        "embedding_model": self.settings.gemini_embedding_model,
                    },
                )
            )
        return sorted(evidence, key=lambda item: item.confidence, reverse=True)

    def passage_context(self, passage_id: str, window: int = 2) -> dict:
        window = max(0, min(window, 5))
        passage_doc = self._get_passage_doc(passage_id)
        if not passage_doc:
            return {"passage_id": passage_id, "items": []}
        enriched = self._enrich_passage_doc(passage_doc)
        library_id = enriched.get("library_id")
        source_id = enriched.get("source_id")
        context_docs: list[dict] = []

        if self.client and self.health() == "connected" and source_id:
            try:
                result = self.client.search(
                    index="hermeneut_passages",
                    size=10000,
                    query={
                        "bool": {
                            "filter": [
                                {"term": {"source_id": source_id}},
                                {"term": {"library_id": library_id}},
                            ]
                        }
                    },
                )
                hits = [hit["_source"] for hit in result["hits"]["hits"]]
                hits.sort(key=self._passage_sort_key)
                index = next((idx for idx, doc in enumerate(hits) if doc.get("passage_id") == passage_id), -1)
                selected = hits[max(0, index - window): index + window + 1] if index >= 0 else hits[: window * 2 + 1]
                context_docs = [self._enrich_passage_doc(doc) for doc in selected]
            except Exception:
                context_docs = []

        if not context_docs and self.preview.available and source_id:
            preview_docs = self.preview.passages_for_source(str(source_id), str(library_id) if library_id else None)
            index = next((idx for idx, doc in enumerate(preview_docs) if doc.get("passage_id") == passage_id), -1)
            selected = preview_docs[max(0, index - window): index + window + 1] if index >= 0 else preview_docs[: window * 2 + 1]
            context_docs = [self._enrich_passage_doc(doc) for doc in selected]

        if not context_docs:
            same_source = [
                doc
                for doc in PASSAGES
                if doc.get("source_id") == source_id and (doc.get("library_id") or "demo_kalam") == library_id
            ]
            same_source.sort(key=self._passage_sort_key)
            index = next((idx for idx, doc in enumerate(same_source) if doc.get("passage_id") == passage_id), -1)
            selected = same_source[max(0, index - window): index + window + 1] if index >= 0 else same_source[: window * 2 + 1]
            context_docs = [self._enrich_passage_doc(doc) for doc in selected]

        return {
            "passage_id": passage_id,
            "library_id": library_id,
            "source_id": source_id,
            "items": [self._public_context_doc(doc) for doc in context_docs],
        }

    def _get_passage_doc(self, passage_id: str) -> dict | None:
        if self.client and self.health() == "connected":
            try:
                result = self.client.get(index="hermeneut_passages", id=passage_id)
                if result.get("found"):
                    return result["_source"]
            except Exception:
                pass
            try:
                result = self.client.search(
                    index="hermeneut_passages",
                    size=1,
                    query={"term": {"passage_id": passage_id}},
                )
                hits = result.get("hits", {}).get("hits", [])
                if hits:
                    return hits[0]["_source"]
            except Exception:
                pass
        preview_doc = self.preview.passage(passage_id) if self.preview.available else None
        if preview_doc:
            return preview_doc
        return next((doc for doc in PASSAGES if doc.get("passage_id") == passage_id), None)

    def _public_context_doc(self, passage_doc: dict) -> dict:
        location = self._evidence_location_fields(passage_doc)
        anchor = self._evidence_anchor_fields(passage_doc, passage_doc.get("text_raw"))
        return {
            "passage_id": passage_doc.get("passage_id"),
            "work_id": passage_doc.get("work_id"),
            "work_title": passage_doc.get("work_title"),
            "author_name": passage_doc.get("author_name"),
            **location,
            **anchor,
            "passage_order": passage_doc.get("passage_order"),
            "chunk_index": passage_doc.get("chunk_index"),
            "text_raw": passage_doc.get("text_raw"),
            "translation_hint": passage_doc.get("translation_hint"),
        }

    def _passage_sort_key(self, passage_doc: dict) -> tuple:
        passage_order = passage_doc.get("passage_order")
        try:
            order_key = int(passage_order)
        except (TypeError, ValueError):
            order_key = 10**9
        source_page = str(passage_doc.get("source_page") or passage_doc.get("page_number") or "")
        page_ref = str(passage_doc.get("page_ref") or passage_doc.get("section_ref") or "")
        passage_id = str(passage_doc.get("passage_id") or "")
        numbers = [int(value) for value in re.findall(r"\d+", f"{source_page} {page_ref} {passage_id}")]
        numeric_key = tuple(numbers[:6]) if numbers else (10**9,)
        return (order_key, numeric_key, source_page, page_ref, passage_id)

    def _relationship_fit(self, work_id: str, web_research: dict) -> float:
        relationships = web_research.get("relationships", [])
        if not web_research.get("enabled") and not relationships:
            return 0.0

        candidate_work_ids = {
            work.get("work_id")
            for work in web_research.get("candidate_works", [])
            if work.get("work_id")
        }
        if work_id in candidate_work_ids:
            return 0.85

        outgoing_edges = [
            edge
            for edge in relationships
            if edge.get("from_id") == work_id and edge.get("to_type") == "work"
        ]
        if outgoing_edges:
            relation_bonus = {
                "comments_on": 0.72,
                "glosses": 0.64,
                "depends_on": 0.58,
                "same_debate_as": 0.38,
                "chronologically_prior_to": 0.30,
            }
            return max(
                min(0.78, relation_bonus.get(str(edge.get("relation")), 0.35) * float(edge.get("confidence", 0.6)))
                for edge in outgoing_edges
            )

        incoming_edges = [
            edge
            for edge in relationships
            if edge.get("to_id") == work_id and edge.get("from_type") == "work"
        ]
        if incoming_edges:
            authority = 0.0
            for edge in incoming_edges:
                relation = str(edge.get("relation"))
                confidence = float(edge.get("confidence", 0.6))
                if relation in {"comments_on", "glosses", "depends_on"}:
                    authority = max(authority, 0.85 * confidence)
                elif relation == "chronologically_prior_to":
                    authority = max(authority, 0.55 * confidence)
                elif relation == "same_debate_as":
                    authority = max(authority, 0.32 * confidence)
            if authority:
                return min(0.88, authority)

        related_work_ids = {
            edge.get("to_id")
            for edge in relationships
            if edge.get("to_type") == "work" and edge.get("to_id")
        }
        if work_id in related_work_ids:
            return 0.55

        return 0.15

    def _semantic_vector(self, text: str) -> list[float]:
        tokens = tokenize(text)
        if not tokens:
            return [0.0] * 8

        vector = [0.0] * 8
        for token in tokens:
            bucket = sum(ord(char) for char in token) % len(vector)
            vector[bucket] += 1.0

        magnitude = sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            return vector
        return [round(value / magnitude, 6) for value in vector]

    def _dense_cosine(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
        left_mag = sqrt(sum(value * value for value in left))
        right_mag = sqrt(sum(value * value for value in right))
        if left_mag == 0 or right_mag == 0:
            return 0.0
        return dot / (left_mag * right_mag)

    def _elastic_query(self, plan_item: SearchPlanItem) -> dict:
        if plan_item.type == SearchType.lexical:
            return {
                "multi_match": {
                    "query": plan_item.query,
                    "fields": ["text_raw^4", "text_normalized^3"],
                    "operator": "or",
                }
            }
        if plan_item.type == SearchType.semantic:
            return {
                "multi_match": {
                    "query": plan_item.query,
                    "fields": ["text_raw^2", "translation_hint^2", "concepts^3"],
                    "operator": "or",
                    "lenient": True,
                }
            }
        if plan_item.type == SearchType.metadata:
            return {
                "multi_match": {
                    "query": plan_item.query,
                    "fields": ["work_title^3", "work_title_ar^3", "author_name^2", "domain", "concepts"],
                    "operator": "or",
                    "lenient": True,
                }
            }
        return {
            "bool": {
                "should": [
                    {
                        "multi_match": {
                            "query": plan_item.query,
                            "fields": ["text_raw^4", "text_normalized^3"],
                            "operator": "or",
                        }
                    },
                    {
                        "multi_match": {
                            "query": plan_item.query,
                            "fields": ["translation_hint^2", "concepts^3", "work_title", "author_name"],
                            "operator": "or",
                            "lenient": True,
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        }
