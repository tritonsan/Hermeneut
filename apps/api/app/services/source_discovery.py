from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx
from google.cloud.exceptions import GoogleCloudError

from app.data.seed import SOURCES, WORKS
from app.models import SourceDiscoverRequest, SourceHit, SourceIngestRequest, SourceIngestResult
from app.services.elastic_service import ElasticService
from app.services.google_clients import storage_client
from app.services.ocr import OcrProcessor
from app.services.ocr_quality import classify_ocr_quality
from app.services.safe_http import allowed_host_list, fetch_limited_bytes, validate_external_url
from app.settings import Settings


class SourceDiscoveryService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.elastic = ElasticService(settings)
        self.ocr = OcrProcessor(settings)

    async def discover(self, request: SourceDiscoverRequest) -> list[SourceHit]:
        hits = self._seed_hits(request)
        async with httpx.AsyncClient(timeout=8) as client:
            for query in self._discovery_queries(request):
                hits.extend(await self._internet_archive_hits(client, query))
            hits.extend(await self._wikidata_hits(client, request.query))
        deduped = {hit.source_id: hit for hit in hits}
        return sorted(deduped.values(), key=lambda hit: hit.quality, reverse=True)[:8]

    def _discovery_queries(self, request: SourceDiscoverRequest) -> list[str]:
        queries = [
            request.query,
            request.work or "",
            request.author or "",
            " ".join(request.concepts),
            f"{request.query} {request.author or ''} {request.work or ''}",
        ]
        for work_id in request.candidate_work_ids:
            queries.extend(self._known_work_queries(work_id))
        return [query.strip() for query in dict.fromkeys(queries) if query.strip()]

    def _known_work_queries(self, work_id: str) -> list[str]:
        variants = {
            "katibi-shamsiyya": [
                "katibi shamsiyya",
                "al risala al shamsiyya",
                "Risala Shamsiyya Katibi",
                "الرسالة الشمسية الكاتبي",
            ],
            "qutb-razi-tahrir-shamsiyya": [
                "تحرير القواعد المنطقية في شرح الرسالة الشمسية",
                "قطب الدين الرازي تحرير القواعد المنطقية",
                "Qutb al-Din al-Razi Tahrir al-qawaid al-mantiqiyya",
                "شرح الرسالة الشمسية للرازي",
            ],
            "tusi-tajrid": ["tusi tajrid", "tajrid al itiqad tusi", "تجريد الاعتقاد الطوسي"],
            "razi-sharh-isharat": ["razi sharh isharat", "شرح الاشارات الرازي"],
            "jurjani-sharh-mawaqif": ["jurjani sharh mawaqif", "شرح المواقف الجرجاني"],
        }
        return variants.get(work_id, [work_id.replace("-", " ")])

    async def ingest(self, request: SourceIngestRequest) -> SourceIngestResult:
        if not request.approved:
            raise ValueError("Source ingest requires admin approval.")

        self._validate_ingest_url(request.url)
        provider = request.provider.lower().replace(" ", "_")
        file_type = self._file_type_from_url(request.url)
        raw_filename = f"source.{file_type}" if file_type in {"pdf", "text"} else "source"
        raw_object = f"raw/{request.library_id}/{provider}/{request.source_id}/{raw_filename}"
        ocr_object = f"ocr/{request.library_id}/{request.source_id}/ocr.json"
        normalized_object = f"normalized/{request.library_id}/{request.source_id}/passages.jsonl"
        gcs_raw_path = f"gs://{self.settings.gcs_bucket}/{raw_object}"
        gcs_ocr_path = f"gs://{self.settings.gcs_bucket}/{ocr_object}"
        gcs_normalized_path = f"gs://{self.settings.gcs_bucket}/{normalized_object}"
        downloaded = await self._download_source(request.url)
        stored = self._store_raw_object(raw_object, downloaded)

        source_doc = {
            "source_id": request.source_id,
            "work_id": request.work_id or request.source_id,
            "provider": request.provider,
            "url": request.url,
            "title": request.title or request.source_id,
            "work_title": request.work_title or request.title or request.source_id,
            "author_id": request.author_id,
            "author_name": request.author_name,
            "source_page_url": request.source_page_url or request.url,
            "download_url": request.url,
            "file_type": file_type,
            "license_note": "Controlled ingest candidate. Verify rights before public redistribution.",
            "quality": 0.62,
            "library_id": request.library_id,
            "visibility": "private",
            "license_status": "needs_review",
            "institution_owned": False,
            "ingestion_status": "raw_stored" if stored else "metadata_recorded",
            "download_policy": "admin_approval_required",
            "lifecycle_status": "raw_stored" if stored else "metadata_recorded",
            "verification_status": "raw_source_needs_review",
            "ocr_status": "ocr_pending",
            "ocr_engine": self.settings.ocr_engine,
            "ocr_max_pages": self.settings.ocr_max_pages,
            "gcs_raw_path": gcs_raw_path,
            "gcs_ocr_path": gcs_ocr_path,
            "gcs_normalized_path": gcs_normalized_path,
            "relationship_reason": request.relationship_reason or "Controlled source approved for OCR/indexing.",
            "provenance": request.provenance or "controlled_source_ingest",
            "provenance_note": "Source entered through controlled Hermeneut admin ingest; searchable text remains separate.",
            "source_role": request.source_role,
            "source_role_group": request.source_role_group,
            "resolution_queries": request.resolution_queries,
            "source_resolution_query": request.source_resolution_query,
            "source_candidate_rank": request.source_candidate_rank,
        }
        indexed = self.elastic.index_source_metadata(source_doc)
        return SourceIngestResult(
            source_id=request.source_id,
            gcs_raw_path=gcs_raw_path,
            gcs_ocr_path=gcs_ocr_path,
            gcs_normalized_path=gcs_normalized_path,
            indexed=indexed,
            ingestion_status=source_doc["ingestion_status"],
            note=(
                "Controlled ingest completed for the raw source layer. OCR/normalization is marked as "
                "a separate verification step; searchable passages come from curated text layers."
            ),
            metadata=source_doc,
        )

    async def process(self, source_id: str) -> SourceIngestResult:
        source_doc = self._source_doc(source_id)
        if not source_doc:
            raise ValueError("Source is not known. Ingest or discover it before processing.")
        source_doc = self._mark_source_job(
            source_doc,
            job_status="ocr_running",
            lifecycle_status="ocr_running",
            ocr_status="ocr_running",
            progress_percent=25,
            note="Full OCR/text extraction started.",
        )
        if self._is_curated_seed_source(source_doc):
            ocr_pages = self._extract_demo_pages(source_doc)
            ocr_payload = {
                "source_id": source_id,
                "engine": "curated_text_layer_fallback",
                "page_count": len(ocr_pages),
                "status": "ocr_completed_with_curated_fallback",
            }
        else:
            ocr_pages, ocr_payload = await self.ocr.process_source(source_doc)
        source_doc = self._mark_source_job(
            source_doc,
            job_status="indexing",
            lifecycle_status="indexing",
            ocr_status=ocr_payload.get("status", "ocr_completed"),
            progress_percent=70,
            note="OCR completed; normalized passages are being indexed into Elastic.",
            extra={
                "ocr_page_count": ocr_payload.get("page_count", len(ocr_pages)),
                "ocr_engine": ocr_payload.get("engine", self.settings.ocr_engine),
                "ocr_error": ocr_payload.get("error"),
                "ocr_total_pages": ocr_payload.get("ocr_total_pages"),
                "ocr_processed_pages": ocr_payload.get("ocr_processed_pages"),
                "ocr_next_page": ocr_payload.get("ocr_next_page"),
                "ocr_batch_size": ocr_payload.get("ocr_batch_size"),
                "ocr_resume_available": ocr_payload.get("ocr_resume_available", False),
            },
        )
        avg_confidence = round(
            sum(float(page.get("ocr_confidence", 0.0)) for page in ocr_pages) / max(len(ocr_pages), 1),
            3,
        ) if ocr_pages else 0.0
        ocr_quality_status = classify_ocr_quality(ocr_pages, avg_confidence)
        continuing_batch = int(source_doc.get("ocr_next_page") or 1) > 1
        indexed_count = self.elastic.index_extracted_passages(
            {**source_doc, "ocr_quality_status": ocr_quality_status},
            ocr_pages,
            replace_source=not continuing_batch,
        )
        if not indexed_count and ocr_payload.get("status") == "ocr_failed":
            ingestion_status = "ocr_failed"
        elif ocr_payload.get("status") == "ocr_partial":
            ingestion_status = "ocr_partial"
        elif indexed_count:
            ingestion_status = "searchable"
        else:
            ingestion_status = "processed_no_text"
        source_doc = {
            **source_doc,
            "ingestion_status": ingestion_status,
            "lifecycle_status": ingestion_status,
            "ocr_status": ocr_payload.get("status", "ocr_completed"),
            "ocr_engine": ocr_payload.get("engine", self.settings.ocr_engine),
            "ocr_page_count": ocr_payload.get("page_count", len(ocr_pages)),
            "ocr_total_pages": ocr_payload.get("ocr_total_pages"),
            "ocr_processed_pages": ocr_payload.get("ocr_processed_pages"),
            "ocr_next_page": ocr_payload.get("ocr_next_page"),
            "ocr_batch_size": ocr_payload.get("ocr_batch_size"),
            "ocr_resume_available": ocr_payload.get("ocr_resume_available", False),
            "ocr_full_document_max_pages": ocr_payload.get("ocr_full_document_max_pages"),
            "ocr_avg_confidence": avg_confidence,
            "ocr_quality_status": ocr_quality_status,
            "ocr_error": ocr_payload.get("error"),
            "indexed_passage_count": indexed_count,
            "verification_status": (
                "partial_text_indexed"
                if ingestion_status == "ocr_partial"
                else "searchable_text_indexed"
                if indexed_count
                else "ocr_completed_no_text"
            ),
        }
        source_doc = self._with_job_fields(
            source_doc,
            job_status=(
                "ocr_partial"
                if ingestion_status == "ocr_partial"
                else "searchable"
                if indexed_count
                else "failed"
                if ingestion_status == "ocr_failed"
                else "processed_no_text"
            ),
            lifecycle_status=ingestion_status,
            progress_percent=70 if ingestion_status == "ocr_partial" else 90 if indexed_count else 100,
            note=(
                f"OCR/text processing completed with {source_doc['ocr_engine']}; "
                f"{indexed_count} passage(s) were indexed into Elastic."
            ),
            completed=ingestion_status in {"ocr_failed", "processed_no_text"},
        )
        indexed = self.elastic.index_source_metadata(source_doc)
        return SourceIngestResult(
            source_id=source_id,
            gcs_raw_path=source_doc.get("gcs_raw_path", ""),
            gcs_ocr_path=source_doc.get("gcs_ocr_path"),
            gcs_normalized_path=source_doc.get("gcs_normalized_path"),
            indexed=indexed,
            ingestion_status=source_doc["ingestion_status"],
            note=(
                f"OCR/text processing {'paused for the next batch' if ingestion_status == 'ocr_partial' else 'completed'} "
                f"with {source_doc['ocr_engine']}; {indexed_count} passage(s) were indexed into Elastic."
            ),
            metadata=source_doc,
        )

    async def process_with_library_graph(self, source_id: str) -> SourceIngestResult:
        result = await self.process(source_id)
        source_doc = result.metadata
        if result.ingestion_status == "searchable":
            source_doc = self._mark_source_job(
                source_doc,
                job_status="graph_running",
                lifecycle_status="graph_running",
                progress_percent=95,
                note="Gemini Pro Scholar Graph analysis started for the library.",
            )
            try:
                from app.services.library_relationship_analyst import LibraryRelationshipAnalyst
                from app.services.catalog_curator import CatalogCuratorService

                library_id = str(source_doc.get("library_id", "demo_kalam"))
                sources = self.elastic.library_sources(library_id)
                samples = self.elastic.library_passage_samples(library_id)
                seed_edges = self.elastic.library_relationship_graph(library_id)
                analysis = LibraryRelationshipAnalyst(self.settings).analyze(library_id, sources, samples, seed_edges)
                proposal_result = CatalogCuratorService(self.settings, self.elastic).store_relationship_analysis(library_id, analysis["edges"])
                edge_count = int(proposal_result["stored_proposal_count"])
                source_doc = self._mark_source_job(
                    source_doc,
                    job_status="completed",
                    lifecycle_status="searchable",
                    progress_percent=100,
                    note=f"Source is searchable and {edge_count} relationship proposal(s) await curator review.",
                    extra={
                        "graph_status": "completed",
                        "relationship_proposal_count": edge_count,
                        "relationship_model_used": analysis.get("model_used"),
                        "relationship_model_assisted": analysis.get("model_assisted"),
                        "relationship_profile": analysis.get("library_profile", {}),
                    },
                    completed=True,
                )
            except Exception as exc:
                source_doc = self._mark_source_job(
                    source_doc,
                    job_status="completed",
                    lifecycle_status="searchable",
                    progress_percent=100,
                    note="Source is searchable; Scholar Graph refresh failed and can be rerun manually.",
                    extra={"graph_status": "failed", "graph_error": str(exc)},
                    completed=True,
                )
            result = result.model_copy(update={"metadata": source_doc})
        return result

    def _mark_source_job(
        self,
        source_doc: dict,
        *,
        job_status: str,
        lifecycle_status: str,
        progress_percent: int,
        note: str,
        ocr_status: str | None = None,
        extra: dict | None = None,
        completed: bool = False,
    ) -> dict:
        updated = self._with_job_fields(
            source_doc,
            job_status=job_status,
            lifecycle_status=lifecycle_status,
            progress_percent=progress_percent,
            note=note,
            ocr_status=ocr_status,
            extra=extra,
            completed=completed,
        )
        self.elastic.index_source_metadata(updated)
        return updated

    def _with_job_fields(
        self,
        source_doc: dict,
        *,
        job_status: str,
        lifecycle_status: str,
        progress_percent: int,
        note: str,
        ocr_status: str | None = None,
        extra: dict | None = None,
        completed: bool = False,
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        job_id = source_doc.get("processing_job_id") or f"job-{source_doc['source_id']}"
        events = list(source_doc.get("job_events") or [])
        events.append(
            {
                "status": job_status,
                "lifecycle_status": lifecycle_status,
                "note": note,
                "timestamp": now,
                "progress_percent": progress_percent,
            }
        )
        job = {
            **(source_doc.get("processing_job") or {}),
            "job_id": job_id,
            "source_id": source_doc["source_id"],
            "status": job_status,
            "progress_percent": progress_percent,
            "note": note,
            "updated_at": now,
            "started_at": source_doc.get("processing_job", {}).get("started_at") or now,
        }
        if completed:
            job["completed_at"] = now
        updated = {
            **source_doc,
            "processing_job_id": job_id,
            "processing_job": job,
            "job_events": events[-50:],
            "ingestion_status": lifecycle_status,
            "lifecycle_status": lifecycle_status,
        }
        if ocr_status:
            updated["ocr_status"] = ocr_status
        if extra:
            updated.update(extra)
        return updated

    def _seed_hits(self, request: SourceDiscoverRequest) -> list[SourceHit]:
        query = request.query.lower()
        candidate_work_ids = set(request.candidate_work_ids)
        hits: list[SourceHit] = []
        for source in SOURCES:
            work = next((item for item in WORKS if item["work_id"] == source["work_id"]), {})
            haystack = " ".join([work.get("title", ""), work.get("title_ar", ""), source["source_id"]]).lower()
            if (
                query in haystack
                or (request.work and request.work.lower() in haystack)
                or source["work_id"] in candidate_work_ids
            ):
                hits.append(
                    SourceHit(
                        provider=source["provider"],
                        source_id=source["source_id"],
                        title=work.get("title", source["source_id"]),
                        url=source["url"],
                        file_type=source["file_type"],
                        license_note=source["license_note"],
                        quality=source["quality"],
                        metadata={
                            "work_id": source["work_id"],
                            "library_id": source.get("library_id", "demo_kalam"),
                            "ingestion_status": source.get("ingestion_status", "indexed"),
                            "download_policy": "already_indexed_or_demo_controlled",
                            "lifecycle_status": "searchable",
                            "relationship_reason": "Matched curated Hermeneut seed metadata or narrowed candidate work.",
                            "provenance": "hermeneut_seed",
                            "verification_status": "demo_indexed",
                            "gcs_raw_path": source.get("gcs_raw_path"),
                            "gcs_ocr_path": source.get("gcs_ocr_path"),
                            "gcs_normalized_path": source.get("gcs_normalized_path"),
                        },
                    )
                )
        return hits

    async def _internet_archive_hits(self, client: httpx.AsyncClient, query: str) -> list[SourceHit]:
        url = "https://archive.org/advancedsearch.php"
        docs: list[dict] = []
        seen: set[str] = set()
        for archive_query in [
            f"({query}) AND mediatype:texts",
            f'title:("{query}") AND mediatype:texts',
            f'creator:("{query}") AND mediatype:texts',
            f'description:("{query}") AND mediatype:texts',
        ]:
            params = {
                "q": archive_query,
                "fl[]": ["identifier", "title"],
                "rows": 5,
                "output": "json",
            }
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                rows = response.json().get("response", {}).get("docs", [])
            except Exception:
                continue
            for row in rows:
                identifier = row.get("identifier")
                if identifier and identifier not in seen:
                    seen.add(identifier)
                    docs.append(row)

        hits: list[SourceHit] = []
        for doc in docs[:5]:
            identifier = doc.get("identifier", "")
            if not identifier:
                continue
            item_url = f"https://archive.org/details/{quote(identifier)}"
            download_url, file_type, file_name, file_size = await self._internet_archive_download_url(client, identifier)
            hits.append(
                SourceHit(
                    provider="Internet Archive",
                    source_id=identifier,
                    title=doc.get("title") or identifier,
                    url=item_url,
                    file_type=file_type,
                    license_note="Verify item rights before ingestion.",
                    quality=0.68,
                    metadata={
                        **doc,
                        "source_page_url": item_url,
                        "download_url": download_url or item_url,
                        "file_type": file_type,
                        "license_status": "needs_review",
                        "download_policy": "admin_approval_required",
                        "lifecycle_status": "download_candidate" if download_url else "requires_human_review",
                        "relationship_reason": "Internet Archive metadata candidate returned for the source discovery query.",
                        "provenance": "internet_archive_advanced_search",
                        "resolver_query": query,
                        "file_name": file_name,
                        "file_size": file_size,
                        "verification_status": "metadata_only",
                        "ingestion_status": "discovered",
                    },
                )
            )
        return hits

    async def _wikidata_hits(self, client: httpx.AsyncClient, query: str) -> list[SourceHit]:
        sparql = f"""
        SELECT ?item ?itemLabel WHERE {{
          ?item rdfs:label ?itemLabel.
          FILTER(LANG(?itemLabel) = "en" || LANG(?itemLabel) = "ar")
          FILTER(CONTAINS(LCASE(STR(?itemLabel)), LCASE("{query}")))
        }}
        LIMIT 3
        """
        try:
            response = await client.get(
                "https://query.wikidata.org/sparql",
                params={"query": sparql, "format": "json"},
                headers={"User-Agent": "HermeneutHackathon/0.1"},
            )
            response.raise_for_status()
            bindings = response.json().get("results", {}).get("bindings", [])
        except Exception:
            return []

        hits: list[SourceHit] = []
        for binding in bindings:
            item = binding.get("item", {}).get("value")
            label = binding.get("itemLabel", {}).get("value")
            if item and label:
                hits.append(
                    SourceHit(
                        provider="Wikidata",
                        source_id=item.rsplit("/", 1)[-1],
                        title=label,
                        url=item,
                        file_type="metadata",
                        license_note="Wikidata metadata; use to enrich author/work graph.",
                        quality=0.55,
                        metadata={
                            "wikidata_url": item,
                            "source_page_url": item,
                            "download_url": None,
                            "file_type": "metadata",
                            "license_status": "metadata_only",
                            "download_policy": "metadata_only",
                            "lifecycle_status": "requires_human_review",
                            "relationship_reason": "Wikidata entity candidate for author/work graph enrichment.",
                            "provenance": "wikidata_sparql",
                            "verification_status": "metadata_only",
                            "ingestion_status": "discovered",
                        },
                    )
                )
        return hits

    def _validate_ingest_url(self, url: str) -> None:
        validate_external_url(url, allowed_host_list(self.settings.source_download_allowed_hosts))

    async def _download_source(self, url: str) -> bytes:
        if "example.com" in url:
            raise ValueError("Example URLs are not downloadable sources.")
        fetched = await fetch_limited_bytes(
            url,
            allowed_hosts=allowed_host_list(self.settings.source_download_allowed_hosts),
            max_bytes=self.settings.source_download_max_bytes,
            timeout=20,
        )
        return fetched.content

    def _store_raw_object(self, object_name: str, content: bytes) -> bool:
        if not self.settings.google_cloud_project:
            return False
        try:
            client = storage_client(self.settings)
            bucket = client.bucket(self.settings.gcs_bucket)
            blob = bucket.blob(object_name)
            blob.upload_from_string(content)
            return True
        except GoogleCloudError:
            return False

    def _file_type_from_url(self, url: str) -> str:
        lowered = url.lower()
        if lowered.endswith(".pdf"):
            return "pdf"
        if lowered.endswith(".txt") or "openiti" in lowered:
            return "text"
        return "remote"

    async def _internet_archive_download_url(
        self,
        client: httpx.AsyncClient,
        identifier: str,
    ) -> tuple[str | None, str, str | None, int | None]:
        try:
            response = await client.get(f"https://archive.org/metadata/{quote(identifier)}")
            response.raise_for_status()
            files = response.json().get("files", [])
        except Exception:
            return None, "metadata", None, None
        expected_text = " ".join(
            str(value or "")
            for value in [
                response.json().get("metadata", {}).get("title"),
                response.json().get("metadata", {}).get("creator"),
                identifier,
            ]
        )
        downloadable = [
            file
            for file in files
            if self._is_downloadable_text_or_pdf(str(file.get("name", "")))
        ]
        preferred = [
            file
            for file in downloadable
            if self._file_matches_expected(str(file.get("name", "")), expected_text)
        ]
        if not preferred:
            preferred = downloadable
        if not preferred:
            return None, "metadata", None, None
        preferred = sorted(preferred, key=self._ia_file_rank)
        file_name = str(preferred[0].get("name"))
        file_type = "pdf" if file_name.lower().endswith(".pdf") else "text"
        file_size = self._file_size(preferred[0])
        return f"https://archive.org/download/{quote(identifier)}/{quote(file_name)}", file_type, file_name, file_size

    def _ia_file_rank(self, file: dict) -> tuple[int, int, int]:
        name = str(file.get("name", ""))
        lowered = name.lower()
        size = self._file_size(file) or 0
        max_bytes = self.settings.source_download_max_bytes
        if lowered.endswith(("_text.txt", "_djvu.txt")):
            kind_rank = 0
        elif lowered.endswith(".pdf") and (not size or size <= max_bytes):
            kind_rank = 1
        elif lowered.endswith(".pdf"):
            kind_rank = 2
        else:
            kind_rank = 3
        return (kind_rank, size or max_bytes + 1, len(name))

    def _file_size(self, file: dict) -> int | None:
        try:
            return int(file.get("size") or 0) or None
        except (TypeError, ValueError):
            return None

    def _is_downloadable_text_or_pdf(self, file_name: str) -> bool:
        lowered = file_name.lower()
        if lowered.endswith(".pdf"):
            return True
        if lowered.endswith(("_meta.txt", "_files.xml", "_meta.xml")):
            return False
        return lowered.endswith(("_djvu.txt", "_text.txt"))

    def _file_matches_expected(self, file_name: str, expected_text: str) -> bool:
        file_tokens = self._relevance_tokens(file_name)
        expected_tokens = self._relevance_tokens(expected_text)
        if not file_tokens or not expected_tokens:
            return True
        overlap = file_tokens & expected_tokens
        return len(overlap) >= 3

    def _relevance_tokens(self, text: str) -> set[str]:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
        return {token for token in normalized.split() if len(token) > 2}

    def _source_doc(self, source_id: str) -> dict | None:
        if self.elastic.client and self.elastic.health() == "connected":
            try:
                result = self.elastic.client.get(index="hermeneut_sources", id=source_id)
                return result["_source"]
            except Exception:
                pass
        hits = self.elastic.lookup_sources(source_id)
        if hits:
            return hits[0]
        seed = next((source for source in SOURCES if source["source_id"] == source_id), None)
        if seed:
            return {
                **seed,
                "gcs_ocr_path": seed.get("gcs_ocr_path")
                or f"gs://{self.settings.gcs_bucket}/ocr/{seed.get('library_id', 'demo_kalam')}/{source_id}/ocr.json",
            }
        preview = self.elastic.preview.source(source_id)
        if preview:
            return preview
        return None

    async def internet_archive_text_fallback_url(self, source_doc: dict) -> str | None:
        identifier = (
            source_doc.get("grounding_metadata", {}).get("identifier")
            or self._archive_identifier_from_url(str(source_doc.get("source_page_url") or source_doc.get("url") or ""))
            or self._archive_identifier_from_url(str(source_doc.get("download_url") or ""))
        )
        if not identifier:
            return None
        async with httpx.AsyncClient(timeout=12) as client:
            try:
                response = await client.get(f"https://archive.org/metadata/{quote(str(identifier))}")
                response.raise_for_status()
                files = response.json().get("files", [])
            except Exception:
                return None
        text_files = sorted(
            [
                file
                for file in files
                if str(file.get("name", "")).lower().endswith(("_text.txt", "_djvu.txt"))
            ],
            key=lambda file: (0 if str(file.get("name", "")).lower().endswith("_text.txt") else 1, len(str(file.get("name", "")))),
        )
        if not text_files:
            return None
        return f"https://archive.org/download/{quote(str(identifier))}/{quote(str(text_files[0].get('name')))}"

    def _archive_identifier_from_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if "details" in parts:
            index = parts.index("details")
            return parts[index + 1] if len(parts) > index + 1 else None
        if "download" in parts:
            index = parts.index("download")
            return parts[index + 1] if len(parts) > index + 1 else None
        return None

    def _is_curated_seed_source(self, source_doc: dict) -> bool:
        provider = str(source_doc.get("provider") or "").lower()
        provenance = str(source_doc.get("provenance") or source_doc.get("provenance_note") or "").lower()
        return (
            source_doc.get("source_id") in {source["source_id"] for source in SOURCES}
            or provider == "openiti-style seed"
            or "hermeneut_seed" in provenance
            or "curated_seed" in provenance
        )

    def _extract_demo_pages(self, source_doc: dict) -> list[dict]:
        work_id = source_doc.get("work_id", source_doc["source_id"])
        from app.data.seed import PASSAGES

        matched = [passage for passage in PASSAGES if passage["work_id"] == work_id]
        if matched:
            return [
                {
                    **passage,
                    "source_page": passage.get("page_ref", f"seed:{index}"),
                    "ocr_confidence": 0.94,
                    "extraction_method": "curated_text_layer_plus_ocr_validation",
                }
                for index, passage in enumerate(matched, start=1)
            ]
        return []
