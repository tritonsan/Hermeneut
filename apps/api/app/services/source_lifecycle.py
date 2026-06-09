import asyncio

from app.models import (
    AgentRun,
    EvidenceMemoryRecord,
    RunActionResult,
    RunCreate,
    RunStatus,
    SourceIngestRequest,
    StepStatus,
    TimelineEvent,
)
from app.services.agent import ResearchAgent
from app.services.elastic_service import ElasticService
from app.services.run_repository import RunRepository
from app.services.scoring import claimworthy_evidence, decision_tier
from app.services.source_discovery import SourceDiscoveryService


class SourceLifecycleService:
    def __init__(
        self,
        agent: ResearchAgent,
        elastic: ElasticService,
        sources: SourceDiscoveryService,
        runs: RunRepository,
    ):
        self.agent = agent
        self.elastic = elastic
        self.sources = sources
        self.runs = runs

    def find_source(self, run: AgentRun, source_id: str) -> dict | None:
        return next((dict(source) for source in run.source_lifecycle_records if str(source.get("source_id")) == source_id), None)

    def reject_source(self, run: AgentRun, source: dict) -> RunActionResult:
        source_id = str(source["source_id"])
        updated = self.replace_source(run, source_id, {**source, "lifecycle_status": "rejected", "counts_as_evidence": False})
        updated = self.append_event(updated, "Source rejected", f"Source {source_id} was rejected by the user.", {"source": source_id})
        self.runs.save_snapshot(updated)
        return RunActionResult(run_id=run.run_id, status=updated.status, note="Source rejected and removed from evidence flow.")

    def continue_without_source(self, run: AgentRun, source: dict) -> RunActionResult:
        source_id = str(source["source_id"])
        updated = self.replace_source(run, source_id, {**source, "lifecycle_status": "skipped", "counts_as_evidence": False})
        updated = self.append_event(updated, "Source skipped", f"Run continued without source {source_id}.", {"source": source_id})
        self.runs.save_snapshot(updated)
        return RunActionResult(run_id=run.run_id, status=updated.status, note="Continuing without this source.")

    async def approve_and_process(self, run: AgentRun, source: dict) -> RunActionResult:
        source_id = str(source["source_id"])
        approved = {**source, "lifecycle_status": "download_approved", "counts_as_evidence": False}
        updated = self.replace_source(run, source_id, approved)
        updated = self.append_event(updated, "Source download approved", f"Starting controlled ingest for {source_id}.", {"source": approved})
        self.runs.save_snapshot(updated)

        ingest_result = await self.sources.ingest(
            SourceIngestRequest(
                provider=str(source.get("provider") or "Web Search"),
                source_id=source_id,
                url=str(source.get("download_url") or source.get("url")),
                work_id=str(source.get("work_id") or source_id),
                title=str(source.get("title") or source_id),
                source_page_url=str(source.get("source_page_url") or source.get("url") or source.get("download_url")),
                relationship_reason=str(source.get("relationship_reason") or "Approved from Open Discovery source candidate."),
                provenance=str(source.get("provenance") or "run_source_candidate"),
                library_id=str(source.get("library_id") or "demo_kalam"),
                approved=True,
            )
        )
        raw_source = {
            **source,
            **ingest_result.metadata,
            "lifecycle_status": "raw_stored",
            "counts_as_evidence": False,
        }
        updated = self.replace_source(updated, source_id, raw_source)
        updated = self.append_event(updated, "Source raw stored", f"Stored {source_id} in the GCS document vault.", ingest_result.metadata)
        self.runs.save_snapshot(updated)
        return await self.process_and_refresh(updated, raw_source, "Source processed after approval")

    async def retry_process(self, run: AgentRun, source: dict) -> RunActionResult:
        retrying = {**source, "lifecycle_status": "ocr_running", "counts_as_evidence": False}
        updated = self.replace_source(run, str(source["source_id"]), retrying)
        updated = self.append_event(updated, "OCR retry started", f"Retrying OCR/text processing for {source['source_id']}.", {"source": retrying})
        self.runs.save_snapshot(updated)
        return await self.process_and_refresh(updated, retrying, "Source processed after OCR retry")

    async def process_and_refresh(self, run: AgentRun, source: dict, label: str) -> RunActionResult:
        source_id = str(source["source_id"])
        processing = {**source, "lifecycle_status": "ocr_running", "ocr_status": "ocr_running", "counts_as_evidence": False}
        updated = self.replace_source(run, source_id, processing)
        self.runs.save_snapshot(updated)

        process_result = await self.sources.process(source_id)
        if process_result.ingestion_status == "ocr_failed":
            fallback_url = await self.sources.internet_archive_text_fallback_url({**source, **process_result.metadata})
            if fallback_url and fallback_url != source.get("download_url"):
                fallback_ingest = await self.sources.ingest(
                    SourceIngestRequest(
                        provider=str(source.get("provider") or "Internet Archive"),
                        source_id=source_id,
                        url=fallback_url,
                        work_id=str(source.get("work_id") or source_id),
                        title=str(source.get("title") or source_id),
                        source_page_url=str(source.get("source_page_url") or source.get("url") or fallback_url),
                        relationship_reason=str(source.get("relationship_reason") or "Text fallback after PDF OCR failure."),
                        provenance=str(source.get("provenance") or "internet_archive_text_fallback"),
                        library_id=str(source.get("library_id") or "demo_kalam"),
                        approved=True,
                    )
                )
                source = {
                    **source,
                    **fallback_ingest.metadata,
                    "fallback_from_url": source.get("download_url") or source.get("url"),
                    "fallback_reason": "PDF OCR failed; Internet Archive text layer was used as fallback.",
                    "lifecycle_status": "raw_stored",
                    "counts_as_evidence": False,
                }
                updated = self.replace_source(updated, source_id, source)
                updated = self.append_event(updated, "Text fallback raw stored", f"Stored text fallback for {source_id}.", fallback_ingest.metadata)
                self.runs.save_snapshot(updated)
                process_result = await self.sources.process(source_id)
        searchable = process_result.ingestion_status == "searchable"
        processed_source = {
            **source,
            **process_result.metadata,
            "lifecycle_status": process_result.ingestion_status,
            "counts_as_evidence": searchable,
        }
        updated = self.replace_source(updated, source_id, processed_source)
        updated = self.append_event(updated, label, process_result.note, process_result.metadata)
        refreshed = self.refresh_evidence(updated)
        self.runs.save_snapshot(refreshed)
        return RunActionResult(run_id=run.run_id, status=refreshed.status, note=process_result.note)

    def auto_process_open_discovery_sources(self, run: AgentRun, payload: RunCreate) -> AgentRun:
        if payload.mode.value != "open_discovery" or not payload.auto_download_sources:
            return run
        if payload.ocr_mode.value == "skip":
            return run
        processed = 0
        role_limits = {
            "containing_layer": max(0, payload.max_containing_source_downloads),
            "citation_chain": max(0, payload.max_citation_source_downloads),
        }
        max_downloads = max(payload.max_pdf_downloads or 0, sum(role_limits.values()))
        processed_by_role = {"containing_layer": 0, "citation_chain": 0, "parallel_witness": 0}
        current = run
        current = current.model_copy(
            update={
                "status": RunStatus.running,
                "current_step": "Processing Open Discovery sources",
                "current_phase": "source_processing",
                "progress_percent": min(max(current.progress_percent, 70), 92),
                "estimated_remaining_seconds": max(current.estimated_remaining_seconds, 20),
            }
        )
        self.runs.save_snapshot(current)
        ordered_sources = sorted(
            current.source_lifecycle_records,
            key=lambda item: (
                0 if self._source_role(item) == "containing_layer" else 1 if self._source_role(item) == "citation_chain" else 2,
                int(item.get("source_candidate_rank") or 999),
            ),
        )
        available_roles = {self._source_role(item) for item in ordered_sources if self.is_auto_processable_source(item)}
        for source in ordered_sources:
            if processed >= max_downloads:
                break
            if not self.is_auto_processable_source(source):
                continue
            role = self._source_role(source)
            if role in role_limits and processed_by_role.get(role, 0) >= role_limits[role]:
                if any(
                    other in available_roles and processed_by_role.get(other, 0) < limit
                    for other, limit in role_limits.items()
                    if other != role
                ):
                    continue
            source_id = str(source.get("source_id"))
            approved = {
                **source,
                "library_id": source.get("library_id") or payload.library_id,
                "lifecycle_status": "download_approved",
                "ingestion_status": "selected",
                "counts_as_evidence": False,
            }
            current = self.replace_source(current, source_id, approved)
            current = self.append_event(
                current,
                "Auto source download approved",
                f"Auto-processing trusted direct source {source_id}.",
                {"source": approved, "policy": "trusted_direct_source_auto_process"},
            )
            self.runs.save_snapshot(current)
            try:
                current = self.ingest_process_refresh_sync(current, approved, "Source auto-processed")
                processed += 1
                processed_by_role[role] = processed_by_role.get(role, 0) + 1
            except Exception as exc:
                failed = {
                    **source,
                    "lifecycle_status": "failed",
                    "failure_reason": str(exc),
                    "failure_reason_public": self._public_failure_reason(str(exc)),
                    "counts_as_evidence": False,
                }
                current = self.replace_source(current, source_id, failed)
                current = self.append_event(
                    current,
                    "Auto source processing failed",
                    f"Auto-processing failed for {source_id}: {exc}",
                    {"source": failed},
                )
                self.runs.save_snapshot(current)
        return current

    def ingest_process_refresh_sync(self, run: AgentRun, source: dict, label: str) -> AgentRun:
        async def _runner() -> AgentRun:
            source_id = str(source["source_id"])
            active_source = source
            try:
                ingest_result = await self.sources.ingest(self._ingest_request_for_source(active_source, source_id))
            except Exception:
                fallback_url = await self.sources.internet_archive_text_fallback_url(source)
                if not fallback_url or fallback_url == source.get("download_url"):
                    raise
                active_source = {
                    **source,
                    "download_url": fallback_url,
                    "file_type": "text",
                    "fallback_from_url": source.get("download_url") or source.get("url"),
                    "fallback_reason": "Primary PDF download failed; Internet Archive text layer was used.",
                }
                ingest_result = await self.sources.ingest(self._ingest_request_for_source(active_source, source_id))
            raw_source = {
                **active_source,
                **ingest_result.metadata,
                "lifecycle_status": "raw_stored",
                "counts_as_evidence": False,
            }
            updated = self.replace_source(run, source_id, raw_source)
            updated = self.append_event(updated, "Source raw stored", f"Stored {source_id} in the GCS document vault.", ingest_result.metadata)
            self.runs.save_snapshot(updated)
            processing = {**raw_source, "lifecycle_status": "ocr_running", "ocr_status": "ocr_running", "counts_as_evidence": False}
            updated = self.replace_source(updated, source_id, processing)
            self.runs.save_snapshot(updated)
            process_result = await self.sources.process(source_id)
            if process_result.ingestion_status == "ocr_failed":
                fallback_url = await self.sources.internet_archive_text_fallback_url({**raw_source, **process_result.metadata})
                if fallback_url and fallback_url != raw_source.get("download_url"):
                    fallback_ingest = await self.sources.ingest(
                        SourceIngestRequest(
                            provider=str(source.get("provider") or "Internet Archive"),
                            source_id=source_id,
                            url=fallback_url,
                            work_id=str(source.get("work_id") or source_id),
                            title=str(source.get("title") or source_id),
                            source_page_url=str(source.get("source_page_url") or source.get("url") or fallback_url),
                            relationship_reason=str(source.get("relationship_reason") or "Auto text fallback after PDF OCR failure."),
                            provenance=str(source.get("provenance") or "internet_archive_text_fallback"),
                            library_id=str(source.get("library_id") or "demo_kalam"),
                            author_id=source.get("author_id"),
                            author_name=source.get("author_name"),
                            work_title=source.get("work_title") or source.get("title"),
                            source_role=source.get("source_role"),
                            source_role_group=source.get("source_role_group"),
                            resolution_queries=list(source.get("resolution_queries") or []),
                            source_resolution_query=source.get("source_resolution_query"),
                            source_candidate_rank=source.get("source_candidate_rank"),
                            approved=True,
                        )
                    )
                    raw_source = {
                        **raw_source,
                        **fallback_ingest.metadata,
                        "fallback_from_url": source.get("download_url") or source.get("url"),
                        "fallback_reason": "PDF OCR failed; Internet Archive text layer was used as fallback.",
                        "lifecycle_status": "raw_stored",
                        "counts_as_evidence": False,
                    }
                    updated = self.replace_source(updated, source_id, raw_source)
                    updated = self.append_event(updated, "Text fallback raw stored", f"Stored text fallback for {source_id}.", fallback_ingest.metadata)
                    self.runs.save_snapshot(updated)
                    process_result = await self.sources.process(source_id)
            searchable = process_result.ingestion_status == "searchable"
            processed_source = {
                **raw_source,
                **process_result.metadata,
                "lifecycle_status": process_result.ingestion_status,
                "counts_as_evidence": searchable,
            }
            updated = self.replace_source(updated, source_id, processed_source)
            updated = self.append_event(updated, label, process_result.note, process_result.metadata)
            refreshed = self.refresh_evidence(updated)
            self.runs.save_snapshot(refreshed)
            return refreshed

        try:
            return asyncio.run(_runner())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_runner())
            finally:
                loop.close()

    def _ingest_request_for_source(self, source: dict, source_id: str) -> SourceIngestRequest:
        return SourceIngestRequest(
            provider=str(source.get("provider") or "Web Search"),
            source_id=source_id,
            url=str(source.get("download_url") or source.get("url")),
            work_id=str(source.get("work_id") or source_id),
            title=str(source.get("title") or source_id),
            source_page_url=str(source.get("source_page_url") or source.get("url") or source.get("download_url")),
            author_id=source.get("author_id"),
            author_name=source.get("author_name"),
            work_title=source.get("work_title") or source.get("title"),
            source_role=source.get("source_role"),
            source_role_group=source.get("source_role_group"),
            resolution_queries=list(source.get("resolution_queries") or []),
            source_resolution_query=source.get("source_resolution_query"),
            source_candidate_rank=source.get("source_candidate_rank"),
            relationship_reason=str(source.get("relationship_reason") or "Auto-approved trusted source candidate."),
            provenance=str(source.get("provenance") or "auto_source_candidate"),
            library_id=str(source.get("library_id") or "demo_kalam"),
            approved=True,
        )

    def refresh_evidence(self, run: AgentRun) -> AgentRun:
        web_event = next((event for event in run.timeline if event.label == "Bibliographic web intelligence gathered"), None)
        web_research = web_event.payload if web_event else {}
        payload = self.runs.get_payload(run.run_id)
        library_id = payload.library_id if payload else None
        evidence = self.elastic.search_passages(run.input_passage, run.search_plan, web_research, library_id=library_id)
        semantic = self.elastic.semantic_passage_lookup(run.input_passage, web_research, library_id=library_id)
        combined = {item.passage_id: item for item in [*evidence, *semantic]}
        evidence = sorted(combined.values(), key=lambda item: item.confidence, reverse=True)
        if run.mode.value == "open_discovery":
            allowed_source_ids = {
                str(source.get("source_id"))
                for source in run.source_lifecycle_records
                if source.get("counts_as_evidence") and source.get("lifecycle_status") == "searchable"
            }
            evidence = [
                item for item in evidence
                if any(item.passage_id.startswith(f"{source_id}-") for source_id in allowed_source_ids)
            ]
        candidates = self.agent._rank_candidates(evidence)
        records = [
            EvidenceMemoryRecord(
                run_id=run.run_id,
                query=run.input_passage,
                tool_used="Elasticsearch refreshed retrieval after source processing",
                passage_id=item.passage_id,
                candidate_work=item.work_id,
                confidence=item.confidence,
                verification_note="Evidence refreshed after approved source processing.",
            )
            for item in evidence[:8]
        ]
        self.elastic.write_evidence_memory(records)
        claimworthy = claimworthy_evidence(evidence)
        active_source_processing = any(
            str(source.get("lifecycle_status") or "") in {"download_approved", "raw_stored", "ocr_running", "indexing"}
            for source in run.source_lifecycle_records
        )
        blocked_reason = None
        if not claimworthy and not active_source_processing:
            blocked_reason = (
                "Textual evidence was found after OCR/indexing, but it remained below the claim-worthy threshold."
                if evidence
                else "Processed OCR/indexed Open Discovery sources yielded no textual match yet."
            )
        final_report = self.agent._write_report(candidates, evidence, run.detected_context, web_research, run.mode)
        if run.mode.value == "open_discovery" and not claimworthy:
            final_report = self._open_discovery_source_processing_report(
                run,
                tier=decision_tier(evidence, bool(run.author_candidates or run.work_candidates or run.relationship_graph)),
                evidence_count=len(evidence),
                active_source_processing=active_source_processing,
            )
        return run.model_copy(
            update={
                "status": RunStatus.running if active_source_processing else RunStatus.completed,
                "current_step": "Processing Open Discovery sources" if active_source_processing else "Completed",
                "current_phase": "source_processing" if active_source_processing else "completed",
                "blocked_reason": None if claimworthy or active_source_processing else blocked_reason,
                "progress_percent": 92 if active_source_processing else 100,
                "estimated_remaining_seconds": 20 if active_source_processing else 0,
                "evidence": evidence,
                "elastic_evidence": [item.model_dump(mode="json") for item in evidence],
                "candidates": candidates,
                "ocr_jobs": self.ocr_jobs_from_sources(run, run.source_lifecycle_records),
                "decision_tier": decision_tier(evidence, bool(run.author_candidates or run.work_candidates or run.relationship_graph)),
                "final_report": final_report,
            }
        )

    def _open_discovery_source_processing_report(
        self,
        run: AgentRun,
        *,
        tier,
        evidence_count: int,
        active_source_processing: bool,
    ) -> str:
        records = run.source_lifecycle_records
        selected = [
            source
            for source in records
            if str(source.get("lifecycle_status") or "") not in {"requires_human_review", "requires_external_source"}
        ]
        searchable = [source for source in records if str(source.get("lifecycle_status") or "") == "searchable"]
        weak_ocr = [
            source
            for source in searchable
            if str(source.get("ocr_quality_status") or "") in {"weak_ocr_needs_manual_review", "usable_but_needs_review"}
        ]
        indexed_passages = sum(self._safe_int(source.get("indexed_passage_count")) for source in searchable)
        role_counts = self._source_role_counts(selected)
        role_note = ""
        if role_counts:
            role_note = " Source targeting covered " + ", ".join(f"{count} {role}" for role, count in role_counts.items()) + "."
        if active_source_processing:
            return (
                f"Decision tier: {tier}. Open Discovery has selected and started processing candidate sources; "
                f"{len(searchable)} source(s) are searchable so far and {indexed_passages} passage(s) have been indexed."
                f"{role_note} Evidence refresh is still in progress, so no final attribution should be made yet."
            )
        if evidence_count:
            return (
                f"Decision tier: {tier}. Open Discovery processed selected sources and retrieved {evidence_count} textual lead(s), "
                "but the retrieved evidence remained below the claim-worthy threshold."
                f" {len(searchable)} source(s) became searchable with {indexed_passages} indexed passage(s)."
                f"{role_note} Treat this as a research lead requiring human verification."
            )
        if searchable:
            quality_note = (
                f" {len(weak_ocr)} searchable source(s) were marked as weak/needs-review OCR, so they were not allowed to support a stronger claim."
                if weak_ocr
                else ""
            )
            return (
                f"Decision tier: {tier}. Open Discovery processed selected sources: {len(searchable)} source(s) became searchable "
                f"and {indexed_passages} passage(s) were indexed, but Elastic retrieval found no textual match for the query."
                f"{quality_note}{role_note} No final attribution can be made from these external leads."
            )
        return (
            f"Decision tier: {tier}. Open Discovery produced source leads, but none of the selected sources became searchable yet."
            f"{role_note} Approve/process a trusted PDF or text layer before treating the lead as evidence."
        )

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _source_role_counts(self, sources: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for source in sources:
            role = self._source_role(source)
            counts[role] = counts.get(role, 0) + 1
        return counts

    def replace_source(self, run: AgentRun, source_id: str, replacement: dict) -> AgentRun:
        records = [
            replacement if str(source.get("source_id")) == source_id else source
            for source in run.source_lifecycle_records
        ]
        return run.model_copy(update={"source_lifecycle_records": records, "ocr_jobs": self.ocr_jobs_from_sources(run, records)})

    def append_event(self, run: AgentRun, label: str, detail: str, payload: dict) -> AgentRun:
        event = TimelineEvent(
            label=label,
            detail=detail,
            tool="Hermeneut source lifecycle",
            status=StepStatus.completed,
            payload=payload,
        )
        return run.model_copy(update={"timeline": [*run.timeline, event]})

    def ocr_jobs_from_sources(self, run: AgentRun, records: list[dict]) -> list[dict]:
        jobs: list[dict] = []
        for source in records:
            lifecycle = source.get("lifecycle_status")
            status = self._job_status_for_lifecycle(str(lifecycle or ""))
            jobs.append(
                {
                    "source_id": source.get("source_id"),
                    "title": source.get("title"),
                    "status": status,
                    "ocr_mode": "full",
                    "ocr_engine": source.get("ocr_engine", "google_vision"),
                    "source_role": source.get("source_role"),
                    "source_role_group": source.get("source_role_group"),
                    "source_candidate_rank": source.get("source_candidate_rank"),
                    "failure_reason_public": source.get("failure_reason_public"),
                    "ocr_quality_status": source.get("ocr_quality_status"),
                    "indexed_passage_count": source.get("indexed_passage_count", 0),
                    "counts_as_evidence": source.get("counts_as_evidence", False),
                    "run_id": run.run_id,
                }
            )
        return jobs

    @staticmethod
    def _source_role(source: dict) -> str:
        role = str(source.get("source_role") or "")
        if role in {"containing_layer", "citation_chain", "parallel_witness"}:
            return role
        return "citation_chain"

    @staticmethod
    def _job_status_for_lifecycle(lifecycle: str) -> str:
        if lifecycle == "download_candidate":
            return "selected"
        if lifecycle == "download_approved":
            return "downloading"
        if lifecycle == "raw_stored":
            return "raw_stored"
        if lifecycle in {"ocr_running", "indexing"}:
            return "ocr_running"
        if lifecycle == "searchable":
            return "searchable"
        if lifecycle in {"failed", "ocr_failed", "rejected"}:
            return "failed"
        if lifecycle in {"requires_human_review", "requires_external_source"}:
            return "approval_required"
        return "candidate"

    @staticmethod
    def _public_failure_reason(error: str) -> str:
        lowered = error.lower()
        if "500" in lowered or "timeout" in lowered or "connection" in lowered:
            return "download_or_mirror_failed"
        if "too large" in lowered or "max" in lowered:
            return "source_size_or_policy_limit"
        return "source_processing_failed"

    @staticmethod
    def is_auto_processable_source(source: dict) -> bool:
        status = str(source.get("lifecycle_status") or "")
        if status != "download_candidate":
            return False
        download_url = str(source.get("download_url") or "")
        if not download_url.startswith("https://"):
            return False
        provider = str(source.get("provider") or "").lower()
        provenance = str(source.get("provenance") or "").lower()
        file_type = str(source.get("file_type") or "").lower()
        trusted_provider = (
            "internet archive" in provider
            or "archive" in provenance
            or "openiti" in provider
            or "openiti" in provenance
        )
        direct_file = file_type in {"pdf", "text"} or download_url.lower().endswith((".pdf", ".txt"))
        return trusted_provider and direct_file
