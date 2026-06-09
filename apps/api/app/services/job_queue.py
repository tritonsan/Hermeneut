import json
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx

from app.settings import Settings


class JobQueueNotConfiguredError(RuntimeError):
    pass


class JobQueueService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def enqueue_source_processing(self, source_id: str) -> dict[str, Any]:
        return await self._enqueue(
            [
                {"name": "HERMENEUT_JOB_KIND", "value": "source"},
                {"name": "HERMENEUT_SOURCE_ID", "value": source_id},
                {"name": "HERMENEUT_JOB_SOURCE_ID", "value": source_id},
            ]
        )

    async def enqueue_run_execution(self, run_id: str, attempt: int = 1) -> dict[str, Any]:
        return await self._enqueue(
            [
                {"name": "HERMENEUT_JOB_KIND", "value": "run"},
                {"name": "HERMENEUT_RUN_ID", "value": run_id},
                {"name": "HERMENEUT_JOB_ATTEMPT", "value": str(attempt)},
            ]
        )

    async def enqueue_catalog_source_analysis(self, source_id: str) -> dict[str, Any]:
        return await self._enqueue(
            [
                {"name": "HERMENEUT_JOB_KIND", "value": "catalog_source"},
                {"name": "HERMENEUT_SOURCE_ID", "value": source_id},
            ]
        )

    async def enqueue_catalog_library_analysis(self, library_id: str) -> dict[str, Any]:
        return await self._enqueue(
            [
                {"name": "HERMENEUT_JOB_KIND", "value": "catalog_library"},
                {"name": "HERMENEUT_LIBRARY_ID", "value": library_id},
            ]
        )

    async def _enqueue(self, env: list[dict[str, str]]) -> dict[str, Any]:
        if self.settings.job_backend != "cloud_run_jobs":
            raise JobQueueNotConfiguredError("Cloud Run Jobs backend is not configured.")
        if not self.settings.google_cloud_project:
            raise JobQueueNotConfiguredError("GOOGLE_CLOUD_PROJECT is required for Cloud Run Jobs.")
        if not self.settings.cloud_run_job_location:
            raise JobQueueNotConfiguredError("CLOUD_RUN_JOB_LOCATION is required for Cloud Run Jobs.")
        if not self.settings.cloud_run_job_name:
            raise JobQueueNotConfiguredError("CLOUD_RUN_JOB_NAME is required for Cloud Run Jobs.")

        location = self.settings.cloud_run_job_location
        job_name = self.settings.cloud_run_job_name
        token = _google_access_token()
        url = (
            f"https://run.googleapis.com/v2/projects/{self.settings.google_cloud_project}"
            f"/locations/{location}/jobs/{job_name}:run"
        )
        body = {
            "overrides": {
                "containerOverrides": [
                    {
                        "env": env
                    }
                ],
                "timeout": f"{self.settings.cloud_run_job_task_timeout_seconds}s",
            }
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(body),
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Cloud Run Job enqueue failed: {response.text}") from exc
        operation = response.json()
        return {
            "backend": "cloud_run_jobs",
            "job_name": job_name,
            "location": location,
            "operation_name": operation.get("name"),
            "operation": operation,
        }


def _google_access_token() -> str:
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token
