from time import sleep

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import (
    get_app_settings,
    get_job_queue_service,
    get_research_agent,
    get_run_execution_service,
    get_run_repository,
    get_source_lifecycle_service,
)
from app.models import (
    AgentRun,
    EvidenceMemoryRecord,
    RunActionRequest,
    RunActionResult,
    RunActionType,
    RunCreate,
    RunStatus,
    SourceIngestRequest,
    StepStatus,
    TimelineEvent,
)
from app.security import require_admin, require_jury_or_admin, sanitize_run
from app.services.agent import ResearchAgent
from app.services.elastic_service import ElasticService
from app.services.job_queue import JobQueueNotConfiguredError, JobQueueService
from app.services.run_execution import RunExecutionService
from app.services.run_repository import RunRepository
from app.services.scoring import claimworthy_evidence, decision_tier
from app.services.source_discovery import SourceDiscoveryService
from app.services.source_lifecycle import SourceLifecycleService
from app.services.storage import run_store
from app.settings import Settings

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("", response_model=AgentRun)
async def create_run(
    payload: RunCreate,
    _access: None = Depends(require_jury_or_admin),
    agent: ResearchAgent = Depends(get_research_agent),
    runs: RunRepository = Depends(get_run_repository),
    executor: RunExecutionService = Depends(get_run_execution_service),
    queue: JobQueueService = Depends(get_job_queue_service),
    settings: Settings = Depends(get_app_settings),
) -> AgentRun:
    if settings.environment.lower() == "production" and agent.elastic.mode() != "elasticsearch":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "live_elastic_required",
                "message": "Research requires Live Elastic. Backup Preview remains available for Library browsing.",
            },
        )
    run = agent.initial_run(payload)
    persisted = runs.save_initial(run, payload)
    if settings.run_execution_mode.lower() == "async" and settings.job_backend == "cloud_run_jobs":
        if settings.environment.lower() == "production" and not persisted:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "run_snapshot_unavailable",
                    "message": "The durable run snapshot store is unavailable, so the worker was not queued.",
                },
            )
        try:
            enqueue_result = await queue.enqueue_run_execution(run.run_id, attempt=1)
            runs.mark_enqueued(run, enqueue_result.get("operation_name"))
        except (JobQueueNotConfiguredError, RuntimeError) as exc:
            run = runs.mark_enqueue_failed(run, str(exc))
        return sanitize_run(run)
    if settings.run_execution_mode.lower() == "async":
        run = executor.execute_sync(payload, run.run_id)
        return sanitize_run(run)
    run = executor.execute_sync(payload, run.run_id)
    return sanitize_run(run)


@router.post("/{run_id}/retry", response_model=AgentRun)
async def retry_run(
    run_id: str,
    _access: None = Depends(require_jury_or_admin),
    agent: ResearchAgent = Depends(get_research_agent),
    runs: RunRepository = Depends(get_run_repository),
    queue: JobQueueService = Depends(get_job_queue_service),
    settings: Settings = Depends(get_app_settings),
) -> AgentRun:
    if agent.elastic.mode() != "elasticsearch":
        raise HTTPException(status_code=503, detail={"code": "live_elastic_required", "message": "Retry requires Live Elastic."})
    current = runs.get_run(run_id)
    payload = runs.get_payload(run_id)
    if not current or not payload:
        raise HTTPException(status_code=404, detail="Run or its original payload was not found.")
    if not current.retryable and current.status != RunStatus.failed:
        raise HTTPException(status_code=409, detail="Only stalled or failed runs can be retried.")
    attempt = int(runs.get_metadata(run_id).get("attempt") or 0) + 1
    retry = agent.initial_run(payload, run_id).model_copy(update={"execution_status": "queued"})
    persisted = runs.save_snapshot(retry, payload=payload, metadata={"attempt": attempt, "queued_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "worker_status": "queued"})
    if settings.run_execution_mode.lower() == "async" and settings.job_backend == "cloud_run_jobs" and settings.environment.lower() == "production" and not persisted:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "run_snapshot_unavailable",
                "message": "The durable run snapshot store is unavailable, so the retry worker was not queued.",
            },
        )
    if settings.run_execution_mode.lower() == "async" and settings.job_backend == "cloud_run_jobs":
        result = await queue.enqueue_run_execution(run_id, attempt=attempt)
        runs.mark_enqueued(retry, result.get("operation_name"))
    return sanitize_run(retry)


@router.get("/{run_id}", response_model=AgentRun)
def get_run(
    run_id: str,
    request: Request,
    debug: bool = False,
    runs: RunRepository = Depends(get_run_repository),
    settings: Settings = Depends(get_app_settings),
) -> AgentRun:
    run = runs.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if debug:
        _require_debug(request, settings)
    return sanitize_run(run, debug=debug)


@router.post("/{run_id}/actions", response_model=RunActionResult)
async def run_action(
    run_id: str,
    payload: RunActionRequest,
    _access: None = Depends(require_jury_or_admin),
    runs: RunRepository = Depends(get_run_repository),
    lifecycle: SourceLifecycleService = Depends(get_source_lifecycle_service),
) -> RunActionResult:
    run = runs.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not payload.source_id:
        raise HTTPException(status_code=400, detail="Source action requires source_id.")

    source = lifecycle.find_source(run, payload.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found in this run.")

    try:
        if payload.action == RunActionType.reject_source:
            return lifecycle.reject_source(run, source)

        if payload.action == RunActionType.continue_without_source:
            return lifecycle.continue_without_source(run, source)

        if payload.action == RunActionType.approve_download:
            return await lifecycle.approve_and_process(run, source)

        if payload.action == RunActionType.retry_ocr:
            return await lifecycle.retry_process(run, source)
    except ValueError as exc:
        failed_source = {**source, "lifecycle_status": "failed", "failure_reason": str(exc), "counts_as_evidence": False}
        updated = lifecycle.replace_source(run, payload.source_id, failed_source)
        updated = lifecycle.append_event(updated, "Source lifecycle failed", str(exc), {"source": failed_source})
        runs.save_snapshot(updated)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raise HTTPException(status_code=400, detail=f"Unsupported action {payload.action.value}.")


@router.get("/{run_id}/events", response_model=AgentRun)
def get_run_events(
    run_id: str,
    request: Request,
    debug: bool = False,
    runs: RunRepository = Depends(get_run_repository),
    settings: Settings = Depends(get_app_settings),
) -> AgentRun:
    return get_run(run_id, request, debug, runs, settings)


def _require_debug(request: Request, settings: Settings) -> None:
    require_admin(request, settings)


def _execute_run(
    payload: RunCreate,
    run_id: str,
    agent: ResearchAgent,
    elastic: ElasticService,
    sources: SourceDiscoveryService,
) -> None:
    try:
        for snapshot in agent.live_snapshots(payload, run_id):
            run_store.save(snapshot)
            elastic.write_run_snapshot(snapshot)
            if snapshot.status != RunStatus.completed:
                sleep(1.2)
        final_run = run_store.get(run_id)
        if final_run:
            _auto_process_open_discovery_sources(final_run, payload, agent, elastic, sources)
    except Exception as exc:
        current = run_store.get(run_id) or agent.initial_run(payload, run_id)
        failed = current.model_copy(
            update={
                "status": RunStatus.failed,
                "current_step": "Failed",
                "current_phase": "failed",
                "estimated_remaining_seconds": 0,
                "final_report": f"Investigation failed before completion: {exc}",
            }
        )
        run_store.save(failed)
        elastic.write_run_snapshot(failed)


def _auto_process_open_discovery_sources(
    run: AgentRun,
    payload: RunCreate,
    agent: ResearchAgent,
    elastic: ElasticService,
    sources: SourceDiscoveryService,
) -> None:
    if payload.mode.value != "open_discovery" or not payload.auto_download_sources:
        return
    if payload.ocr_mode.value == "skip":
        return
    processed = 0
    max_downloads = max(0, payload.max_pdf_downloads)
    current = run
    for source in current.source_lifecycle_records:
        if processed >= max_downloads:
            break
        if not _is_auto_processable_source(source):
            continue
        source_id = str(source.get("source_id"))
        approved = {
            **source,
            "library_id": source.get("library_id") or payload.library_id,
            "lifecycle_status": "download_approved",
            "counts_as_evidence": False,
        }
        current = _replace_source(current, source_id, approved)
        current = _append_event(
            current,
            "Auto source download approved",
            f"Auto-processing trusted direct source {source_id}.",
            {"source": approved, "policy": "trusted_direct_source_auto_process"},
        )
        run_store.save(current)
        elastic.write_run_snapshot(current)
        try:
            current = _ingest_process_refresh_sync(current, approved, agent, elastic, sources, "Source auto-processed")
            processed += 1
        except Exception as exc:
            failed = {
                **source,
                "lifecycle_status": "failed",
                "failure_reason": str(exc),
                "counts_as_evidence": False,
            }
            current = _replace_source(current, source_id, failed)
            current = _append_event(
                current,
                "Auto source processing failed",
                f"Auto-processing failed for {source_id}: {exc}",
                {"source": failed},
            )
            run_store.save(current)
            elastic.write_run_snapshot(current)


def _is_auto_processable_source(source: dict) -> bool:
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


def _ingest_process_refresh_sync(
    run: AgentRun,
    source: dict,
    agent: ResearchAgent,
    elastic: ElasticService,
    sources: SourceDiscoveryService,
    label: str,
) -> AgentRun:
    import asyncio

    async def _runner() -> AgentRun:
        source_id = str(source["source_id"])
        ingest_result = await sources.ingest(
            SourceIngestRequest(
                provider=str(source.get("provider") or "Web Search"),
                source_id=source_id,
                url=str(source.get("download_url") or source.get("url")),
                work_id=str(source.get("work_id") or source_id),
                title=str(source.get("title") or source_id),
                source_page_url=str(source.get("source_page_url") or source.get("url") or source.get("download_url")),
                relationship_reason=str(source.get("relationship_reason") or "Auto-approved trusted source candidate."),
                provenance=str(source.get("provenance") or "auto_source_candidate"),
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
        updated = _replace_source(run, source_id, raw_source)
        updated = _append_event(updated, "Source raw stored", f"Stored {source_id} in the GCS document vault.", ingest_result.metadata)
        run_store.save(updated)
        elastic.write_run_snapshot(updated)
        processing = {**raw_source, "lifecycle_status": "ocr_running", "ocr_status": "ocr_running", "counts_as_evidence": False}
        updated = _replace_source(updated, source_id, processing)
        run_store.save(updated)
        elastic.write_run_snapshot(updated)
        process_result = await sources.process(source_id)
        if process_result.ingestion_status == "ocr_failed":
            fallback_url = await sources.internet_archive_text_fallback_url({**raw_source, **process_result.metadata})
            if fallback_url and fallback_url != raw_source.get("download_url"):
                fallback_ingest = await sources.ingest(
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
                updated = _replace_source(updated, source_id, raw_source)
                updated = _append_event(updated, "Text fallback raw stored", f"Stored text fallback for {source_id}.", fallback_ingest.metadata)
                run_store.save(updated)
                elastic.write_run_snapshot(updated)
                process_result = await sources.process(source_id)
        searchable = process_result.ingestion_status == "searchable"
        processed_source = {
            **raw_source,
            **process_result.metadata,
            "lifecycle_status": process_result.ingestion_status,
            "counts_as_evidence": searchable,
        }
        updated = _replace_source(updated, source_id, processed_source)
        updated = _append_event(updated, label, process_result.note, process_result.metadata)
        refreshed = _refresh_evidence(updated, agent, elastic)
        run_store.save(refreshed)
        elastic.write_run_snapshot(refreshed)
        return refreshed

    try:
        return asyncio.run(_runner())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_runner())
        finally:
            loop.close()


def _find_source(run: AgentRun, source_id: str) -> dict | None:
    return next((dict(source) for source in run.source_lifecycle_records if str(source.get("source_id")) == source_id), None)


def _replace_source(run: AgentRun, source_id: str, replacement: dict) -> AgentRun:
    records = [
        replacement if str(source.get("source_id")) == source_id else source
        for source in run.source_lifecycle_records
    ]
    return run.model_copy(update={"source_lifecycle_records": records, "ocr_jobs": _ocr_jobs_from_sources(run, records)})


def _append_event(run: AgentRun, label: str, detail: str, payload: dict) -> AgentRun:
    event = TimelineEvent(
        label=label,
        detail=detail,
        tool="Hermeneut source lifecycle",
        status=StepStatus.completed,
        payload=payload,
    )
    return run.model_copy(update={"timeline": [*run.timeline, event]})


def _ocr_jobs_from_sources(run: AgentRun, records: list[dict]) -> list[dict]:
    jobs: list[dict] = []
    for source in records:
        lifecycle = source.get("lifecycle_status")
        jobs.append(
            {
                "source_id": source.get("source_id"),
                "title": source.get("title"),
                "status": (
                    "searchable"
                    if lifecycle == "searchable"
                    else "ready_for_ocr"
                    if lifecycle in {"raw_stored", "download_approved"}
                    else "waiting_for_download_or_approval"
                ),
                "ocr_mode": "full",
                "ocr_engine": source.get("ocr_engine", "google_vision"),
                "download_url": source.get("download_url"),
                "gcs_raw_path": source.get("gcs_raw_path"),
                "gcs_ocr_path": source.get("gcs_ocr_path"),
                "gcs_normalized_path": source.get("gcs_normalized_path"),
                "counts_as_evidence": source.get("counts_as_evidence", False),
                "run_id": run.run_id,
            }
        )
    return jobs


async def _approve_and_process(
    run: AgentRun,
    source: dict,
    agent: ResearchAgent,
    elastic: ElasticService,
    sources: SourceDiscoveryService,
) -> RunActionResult:
    source_id = str(source["source_id"])
    approved = {**source, "lifecycle_status": "download_approved", "counts_as_evidence": False}
    updated = _replace_source(run, source_id, approved)
    updated = _append_event(updated, "Source download approved", f"Starting controlled ingest for {source_id}.", {"source": approved})
    run_store.save(updated)
    elastic.write_run_snapshot(updated)

    ingest_result = await sources.ingest(
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
    updated = _replace_source(updated, source_id, raw_source)
    updated = _append_event(updated, "Source raw stored", f"Stored {source_id} in the GCS document vault.", ingest_result.metadata)
    run_store.save(updated)
    elastic.write_run_snapshot(updated)
    return await _process_and_refresh(updated, raw_source, agent, elastic, sources, "Source processed after approval")


async def _retry_process(
    run: AgentRun,
    source: dict,
    agent: ResearchAgent,
    elastic: ElasticService,
    sources: SourceDiscoveryService,
) -> RunActionResult:
    retrying = {**source, "lifecycle_status": "ocr_running", "counts_as_evidence": False}
    updated = _replace_source(run, str(source["source_id"]), retrying)
    updated = _append_event(updated, "OCR retry started", f"Retrying OCR/text processing for {source['source_id']}.", {"source": retrying})
    run_store.save(updated)
    elastic.write_run_snapshot(updated)
    return await _process_and_refresh(updated, retrying, agent, elastic, sources, "Source processed after OCR retry")


async def _process_and_refresh(
    run: AgentRun,
    source: dict,
    agent: ResearchAgent,
    elastic: ElasticService,
    sources: SourceDiscoveryService,
    label: str,
) -> RunActionResult:
    source_id = str(source["source_id"])
    processing = {**source, "lifecycle_status": "ocr_running", "ocr_status": "ocr_running", "counts_as_evidence": False}
    updated = _replace_source(run, source_id, processing)
    run_store.save(updated)
    elastic.write_run_snapshot(updated)

    process_result = await sources.process(source_id)
    if process_result.ingestion_status == "ocr_failed":
        fallback_url = await sources.internet_archive_text_fallback_url({**source, **process_result.metadata})
        if fallback_url and fallback_url != source.get("download_url"):
            fallback_ingest = await sources.ingest(
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
            updated = _replace_source(updated, source_id, source)
            updated = _append_event(updated, "Text fallback raw stored", f"Stored text fallback for {source_id}.", fallback_ingest.metadata)
            run_store.save(updated)
            elastic.write_run_snapshot(updated)
            process_result = await sources.process(source_id)
    searchable = process_result.ingestion_status == "searchable"
    processed_source = {
        **source,
        **process_result.metadata,
        "lifecycle_status": process_result.ingestion_status,
        "counts_as_evidence": searchable,
    }
    updated = _replace_source(updated, source_id, processed_source)
    updated = _append_event(updated, label, process_result.note, process_result.metadata)
    refreshed = _refresh_evidence(updated, agent, elastic)
    run_store.save(refreshed)
    elastic.write_run_snapshot(refreshed)
    return RunActionResult(run_id=run.run_id, status=refreshed.status, note=process_result.note)


def _refresh_evidence(run: AgentRun, agent: ResearchAgent, elastic: ElasticService) -> AgentRun:
    web_event = next((event for event in run.timeline if event.label == "Bibliographic web intelligence gathered"), None)
    web_research = web_event.payload if web_event else {}
    payload = run_store.get_payload(run.run_id)
    library_id = payload.library_id if payload else None
    evidence = elastic.search_passages(run.input_passage, run.search_plan, web_research, library_id=library_id)
    semantic = elastic.semantic_passage_lookup(run.input_passage, web_research, library_id=library_id)
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
    candidates = agent._rank_candidates(evidence)
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
    elastic.write_evidence_memory(records)
    final_report = agent._write_report(candidates, evidence, run.detected_context, web_research, run.mode)
    claimworthy = claimworthy_evidence(evidence)
    return run.model_copy(
        update={
            "status": RunStatus.completed,
            "current_step": "Completed",
            "current_phase": "completed",
            "blocked_reason": None
            if claimworthy
            else "No OCR/indexed open-discovery source yielded claim-worthy Elastic evidence yet.",
            "progress_percent": 100,
            "estimated_remaining_seconds": 0,
            "evidence": evidence,
            "elastic_evidence": [item.model_dump(mode="json") for item in evidence],
            "candidates": candidates,
            "ocr_jobs": _ocr_jobs_from_sources(run, run.source_lifecycle_records),
            "decision_tier": decision_tier(evidence, bool(run.author_candidates or run.work_candidates or run.relationship_graph)),
            "final_report": final_report,
        }
    )
