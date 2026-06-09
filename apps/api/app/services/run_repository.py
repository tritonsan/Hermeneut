from datetime import datetime, timedelta, timezone

from app.models import AgentRun, RunCreate, RunStatus
from app.services.elastic_service import ElasticService
from app.services.storage import run_store


class RunRepository:
    def __init__(self, elastic: ElasticService):
        self.elastic = elastic

    def save_initial(self, run: AgentRun, payload: RunCreate) -> bool:
        run_store.save(run)
        run_store.save_payload(run.run_id, payload)
        return self.elastic.write_run_snapshot(
            run,
            payload=payload,
            metadata={
                "attempt": 0,
                "queued_at": _now(),
                "worker_status": "queued",
            },
        )

    def save_snapshot(self, run: AgentRun, payload: RunCreate | None = None, metadata: dict | None = None) -> bool:
        run_store.save(run)
        if payload:
            run_store.save_payload(run.run_id, payload)
        return self.elastic.write_run_snapshot(run, payload=payload, metadata=metadata)

    def get_run(self, run_id: str) -> AgentRun | None:
        run = run_store.get(run_id)
        snapshot = self.elastic.get_run_snapshot(run_id)
        if snapshot:
            if not run or _is_newer_or_more_complete(snapshot, run):
                run_store.save(snapshot)
                run = snapshot
        return self.execution_view(run) if run else None

    def execution_view(self, run: AgentRun) -> AgentRun:
        metadata = self.get_metadata(run.run_id)
        started = _parse_dt(metadata.get("started_at") or metadata.get("queued_at"))
        elapsed = max(0, int((datetime.now(timezone.utc) - started).total_seconds())) if started else 0
        active = run.status in {RunStatus.queued, RunStatus.running, RunStatus.waiting_source, RunStatus.waiting_for_approval}
        stalled = active and elapsed >= 300
        delayed = active and elapsed >= 60
        if stalled:
            run = run.model_copy(
                update={
                    "status": RunStatus.failed,
                    "current_phase": "stalled",
                    "current_step": "Research worker stalled",
                    "blocked_reason": "The durable research worker did not make progress within five minutes.",
                    "estimated_remaining_seconds": 0,
                }
            )
        return run.model_copy(
            update={
                "execution_status": "stalled" if stalled else run.status.value,
                "elapsed_seconds": elapsed,
                "delayed": delayed,
                "retryable": stalled or run.status == RunStatus.failed,
            }
        )

    def get_payload(self, run_id: str) -> RunCreate | None:
        payload = run_store.get_payload(run_id)
        if payload:
            return payload
        payload = self.elastic.get_run_payload(run_id)
        if payload:
            run_store.save_payload(run_id, payload)
        return payload

    def get_metadata(self, run_id: str) -> dict:
        return self.elastic.get_run_metadata(run_id)

    def mark_enqueued(self, run: AgentRun, operation_name: str | None) -> None:
        self.save_snapshot(
            run,
            metadata={
                "job_operation_name": operation_name,
                "worker_status": "enqueued",
            },
        )

    def mark_enqueue_failed(self, run: AgentRun, error: str) -> AgentRun:
        failed = run.model_copy(
            update={
                "status": RunStatus.failed,
                "current_step": "Run worker enqueue failed",
                "current_phase": "failed",
                "blocked_reason": error,
                "estimated_remaining_seconds": 0,
                "final_report": f"Investigation could not be queued for durable execution: {error}",
            }
        )
        self.save_snapshot(
            failed,
            metadata={
                "last_error": error,
                "worker_status": "enqueue_failed",
                "completed_at": _now(),
            },
        )
        return failed

    def acquire_lock(self, run: AgentRun, attempt: int, lock_seconds: int = 900) -> AgentRun | None:
        if run.status == RunStatus.completed:
            return None
        metadata = self.get_metadata(run.run_id)
        locked_until = _parse_dt(metadata.get("locked_until"))
        if run.status == RunStatus.running and locked_until and locked_until > datetime.now(timezone.utc):
            return None
        running = run.model_copy(
            update={
                "status": RunStatus.running,
                "current_step": "Running durable research worker",
                "current_phase": "running",
                "progress_percent": max(run.progress_percent, 5),
                "estimated_remaining_seconds": max(run.estimated_remaining_seconds, 15),
            }
        )
        self.save_snapshot(
            running,
            metadata={
                "attempt": attempt,
                "locked_until": (datetime.now(timezone.utc) + timedelta(seconds=lock_seconds)).isoformat(),
                "started_at": metadata.get("started_at") or _now(),
                "worker_status": "running",
            },
        )
        return running

    def mark_failed(self, run: AgentRun, error: str) -> AgentRun:
        failed = run.model_copy(
            update={
                "status": RunStatus.failed,
                "current_step": "Failed",
                "current_phase": "failed",
                "blocked_reason": error,
                "estimated_remaining_seconds": 0,
                "final_report": f"Investigation failed before completion: {error}",
            }
        )
        self.save_snapshot(
            failed,
            metadata={
                "last_error": error,
                "worker_status": "failed",
                "locked_until": _now(),
                "completed_at": _now(),
            },
        )
        return failed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_newer_or_more_complete(snapshot: AgentRun, current: AgentRun) -> bool:
    snapshot_active_sources = _active_source_processing(snapshot)
    current_active_sources = _active_source_processing(current)
    if snapshot_active_sources and not current_active_sources:
        return True
    if current.status == RunStatus.completed and snapshot.status in {RunStatus.running, RunStatus.waiting_source}:
        return True
    status_rank = {
        RunStatus.queued: 0,
        RunStatus.running: 1,
        RunStatus.waiting_source: 2,
        RunStatus.waiting_for_approval: 2,
        RunStatus.failed: 3,
        RunStatus.completed: 4,
    }
    if status_rank.get(snapshot.status, 0) > status_rank.get(current.status, 0):
        return True
    if snapshot.progress_percent > current.progress_percent:
        return True
    if len(snapshot.evidence) > len(current.evidence):
        return True
    snapshot_searchable = sum(1 for item in snapshot.source_lifecycle_records if item.get("lifecycle_status") == "searchable")
    current_searchable = sum(1 for item in current.source_lifecycle_records if item.get("lifecycle_status") == "searchable")
    if snapshot_searchable > current_searchable:
        return True
    return len(snapshot.timeline) > len(current.timeline)


def _active_source_processing(run: AgentRun) -> bool:
    return any(
        str(source.get("lifecycle_status") or "") in {"download_approved", "raw_stored", "ocr_running", "indexing"}
        for source in run.source_lifecycle_records
    )
