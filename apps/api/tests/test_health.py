from app.routers.health import elastic_mcp_status, run_worker_status
from app.settings import Settings


def test_elastic_mcp_status_not_configured() -> None:
    settings = Settings(elastic_mcp_endpoint=None, elastic_mcp_api_key=None)

    assert elastic_mcp_status(settings) == "not-configured"


def test_elastic_mcp_status_configured_without_api_key() -> None:
    settings = Settings(elastic_mcp_endpoint="https://elastic.example/mcp", elastic_mcp_api_key=None)

    assert elastic_mcp_status(settings) == "configured"


def test_run_worker_status_reports_cloud_run_jobs() -> None:
    settings = Settings(
        run_execution_mode="async",
        job_backend="cloud_run_jobs",
        google_cloud_project="hermeneut",
        cloud_run_job_location="europe-west4",
        cloud_run_job_name="worker",
    )

    assert run_worker_status(settings) == "cloud_run_jobs"
