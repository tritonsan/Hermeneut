from time import sleep

from app.models import AgentRun, RunCreate, RunStatus
from app.services.agent import ResearchAgent
from app.services.run_repository import RunRepository
from app.services.source_lifecycle import SourceLifecycleService

UNLOCKED_AT = "1970-01-01T00:00:00+00:00"


class RunExecutionService:
    def __init__(
        self,
        agent: ResearchAgent,
        runs: RunRepository,
        source_lifecycle: SourceLifecycleService,
    ):
        self.agent = agent
        self.runs = runs
        self.source_lifecycle = source_lifecycle

    def execute(self, run_id: str, attempt: int = 1) -> AgentRun | None:
        payload = self.runs.get_payload(run_id)
        run = self.runs.get_run(run_id)
        if not payload:
            if run:
                return self.runs.mark_failed(run, "Run payload is missing from durable snapshot store.")
            return None
        if not run:
            run = self.agent.initial_run(payload, run_id)
            self.runs.save_initial(run, payload)
        if run.status == RunStatus.completed:
            return run

        locked = self.runs.acquire_lock(run, attempt)
        if locked is None:
            return self.runs.get_run(run_id)

        current = locked
        try:
            for snapshot in self.agent.live_snapshots(payload, run_id):
                current = self._merge_worker_state(snapshot)
                self.runs.save_snapshot(
                    current,
                    payload=payload,
                    metadata={
                        "worker_status": current.status.value,
                        **({"locked_until": UNLOCKED_AT} if current.status == RunStatus.completed else {}),
                    },
                )
                if current.status != RunStatus.completed:
                    sleep(1.2)
            if current.status == RunStatus.completed:
                current = self.source_lifecycle.auto_process_open_discovery_sources(current, payload)
                self.runs.save_snapshot(
                    current,
                    payload=payload,
                    metadata={"worker_status": "completed", "locked_until": UNLOCKED_AT},
                )
            return current
        except Exception as exc:
            return self.runs.mark_failed(current, str(exc))

    def execute_sync(self, payload: RunCreate, run_id: str) -> AgentRun:
        initial = self.runs.get_run(run_id) or self.agent.initial_run(payload, run_id)
        self.runs.save_initial(initial, payload)
        final = self.execute(run_id, attempt=1)
        return final or initial

    def _merge_worker_state(self, snapshot: AgentRun) -> AgentRun:
        if snapshot.status == RunStatus.completed:
            return snapshot.model_copy(
                update={
                    "current_phase": "completed",
                    "current_step": "Completed",
                    "progress_percent": 100,
                    "estimated_remaining_seconds": 0,
                }
            )
        return snapshot.model_copy(
            update={
                "status": RunStatus.running,
                "current_phase": snapshot.current_phase or "running",
                "progress_percent": max(snapshot.progress_percent, 5),
            }
        )
