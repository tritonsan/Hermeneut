import asyncio
import os
import sys

from app.services.agent import ResearchAgent
from app.services.catalog_curator import CatalogCuratorService
from app.services.elastic_service import ElasticService
from app.services.job_queue import JobQueueService
from app.services.run_execution import RunExecutionService
from app.services.run_repository import RunRepository
from app.services.source_discovery import SourceDiscoveryService
from app.services.source_lifecycle import SourceLifecycleService
from app.services.web_research import WebResearchService
from app.settings import get_settings


async def _main() -> int:
    job_kind = os.getenv("HERMENEUT_JOB_KIND")
    source_id = os.getenv("HERMENEUT_SOURCE_ID") or os.getenv("HERMENEUT_JOB_SOURCE_ID")
    run_id = os.getenv("HERMENEUT_RUN_ID")
    library_id = os.getenv("HERMENEUT_LIBRARY_ID")
    if not job_kind:
        job_kind = "run" if run_id else "source"
    if job_kind == "run":
        if not run_id:
            print("HERMENEUT_RUN_ID is required for run jobs.", file=sys.stderr)
            return 2
        return await asyncio.to_thread(_execute_run_job, run_id)
    if job_kind == "catalog_source":
        if not source_id:
            print("HERMENEUT_SOURCE_ID is required for catalog_source jobs.", file=sys.stderr)
            return 2
        return await asyncio.to_thread(_execute_catalog_source_job, source_id)
    if job_kind == "catalog_library":
        if not library_id:
            print("HERMENEUT_LIBRARY_ID is required for catalog_library jobs.", file=sys.stderr)
            return 2
        return await asyncio.to_thread(_execute_catalog_library_job, library_id)
    if job_kind != "source":
        print(f"Unsupported HERMENEUT_JOB_KIND: {job_kind}", file=sys.stderr)
        return 2
    if not source_id:
        print("HERMENEUT_SOURCE_ID is required for source jobs.", file=sys.stderr)
        return 2
    return await _execute_source_job(source_id)


def _execute_run_job(run_id: str) -> int:
    try:
        settings = get_settings()
        elastic = ElasticService(settings)
        runs = RunRepository(elastic)
        sources = SourceDiscoveryService(settings)
        agent = ResearchAgent(elastic, WebResearchService(settings))
        lifecycle = SourceLifecycleService(agent, elastic, sources, runs)
        executor = RunExecutionService(agent, runs, lifecycle)
        try:
            attempt = int(os.getenv("HERMENEUT_JOB_ATTEMPT", "1"))
        except ValueError:
            attempt = 1
        result = executor.execute(run_id, attempt=attempt)
        if not result:
            print(f"Cloud Run investigation job found no runnable payload for {run_id}.", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"Cloud Run investigation job failed for {run_id}: {exc}", file=sys.stderr)
        return 1
    print(f"Cloud Run investigation job completed for {run_id}: {result.status.value}")
    return 0


async def _execute_source_job(source_id: str) -> int:
    service = SourceDiscoveryService(get_settings())
    try:
        result = await service.process_with_library_graph(source_id)
    except Exception as exc:
        source_doc = service._source_doc(source_id)
        if source_doc:
            service._mark_source_job(
                source_doc,
                job_status="failed",
                lifecycle_status="failed",
                progress_percent=100,
                note=f"Cloud Run OCR/index job failed: {exc}",
                ocr_status="ocr_failed",
                extra={"job_error": str(exc)},
                completed=True,
            )
        print(f"Cloud Run OCR/index job failed for {source_id}: {exc}", file=sys.stderr)
        return 1
    print(
        f"Cloud Run OCR/index job completed for {source_id}: "
        f"{result.ingestion_status} / {result.metadata.get('indexed_passage_count', 0)} passages"
    )
    if result.ingestion_status == "ocr_partial" and result.metadata.get("ocr_resume_available"):
        try:
            await JobQueueService(service.settings).enqueue_source_processing(source_id)
            print(f"OCR continuation job queued for {source_id}.")
        except Exception as exc:
            print(f"OCR continuation enqueue deferred for {source_id}: {exc}", file=sys.stderr)
    if result.ingestion_status == "searchable":
        try:
            await JobQueueService(service.settings).enqueue_catalog_source_analysis(source_id)
            print(f"Catalog curator job queued for {source_id}.")
        except Exception as exc:
            print(f"Catalog curator enqueue deferred for {source_id}: {exc}", file=sys.stderr)
    return 0


def _execute_catalog_source_job(source_id: str) -> int:
    settings = get_settings()
    service = CatalogCuratorService(settings, ElasticService(settings))
    try:
        result = service.analyze_source(source_id)
    except Exception as exc:
        print(f"Catalog source analysis failed for {source_id}: {exc}", file=sys.stderr)
        return 1
    print(f"Catalog source analysis completed for {source_id}: {result['stored_proposal_count']} proposal(s)")
    return 0


def _execute_catalog_library_job(library_id: str) -> int:
    settings = get_settings()
    service = CatalogCuratorService(settings, ElasticService(settings))
    try:
        result = service.analyze_library(library_id)
    except Exception as exc:
        print(f"Catalog library analysis failed for {library_id}: {exc}", file=sys.stderr)
        return 1
    print(f"Catalog library analysis completed for {library_id}: {result['analysis_job']['proposal_count']} proposal(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
