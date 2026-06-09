from threading import Lock

from app.models import AgentRun, RunCreate, RunStatus


class RunStore:
    def __init__(self):
        self._runs: dict[str, AgentRun] = {}
        self._payloads: dict[str, RunCreate] = {}
        self._lock = Lock()

    def save(self, run: AgentRun) -> None:
        with self._lock:
            self._runs[run.run_id] = run

    def get(self, run_id: str) -> AgentRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def save_payload(self, run_id: str, payload: RunCreate) -> None:
        with self._lock:
            self._payloads[run_id] = payload

    def get_payload(self, run_id: str) -> RunCreate | None:
        with self._lock:
            return self._payloads.get(run_id)

    def update(self, run_id: str, **changes) -> AgentRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return None
            updated = run.model_copy(update=changes)
            self._runs[run_id] = updated
            return updated

    def action(self, run_id: str, note: str) -> AgentRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return None
            updated = run.model_copy(
                update={
                    "status": RunStatus.running,
                    "current_step": "Continuing after approval",
                    "estimated_remaining_seconds": max(run.estimated_remaining_seconds, 20),
                }
            )
            updated.timeline.append(
                run.timeline[-1].model_copy(
                    update={
                        "label": "Human action received",
                        "detail": note,
                        "tool": "Hermeneut approval API",
                        "status": "completed",
                        "requires_action": False,
                        "payload": {"note": note},
                    }
                )
            )
            self._runs[run_id] = updated
            return updated


run_store = RunStore()
