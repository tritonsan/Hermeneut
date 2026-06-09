from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "local"
    admin_api_token: str | None = None
    # Demo-only switch. When true, operator endpoints (OCR editor, catalog
    # curator, library mutations, run actions) are reachable without an admin
    # token so a jury can see every feature. Defaults to False (secure); turn
    # on only for a controlled demo window and turn back off afterwards.
    public_demo_mode: bool = False
    jury_access_enabled: bool = False
    jury_proxy_token: str | None = None
    jury_access_code_hash: str | None = None
    jury_session_max_age_seconds: int = 172_800
    cors_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    run_execution_mode: str = "sync"
    elasticsearch_url: str | None = None
    elasticsearch_api_key: str | None = None
    elastic_mcp_endpoint: str | None = None
    elastic_mcp_api_key: str | None = None
    google_cloud_project: str | None = None
    google_application_credentials: str | None = None
    google_service_account_json: str | None = None
    gcs_bucket: str = "hermeneut-sources"
    agent_builder_agent_id: str | None = None
    gemini_model: str = "gemini-3-pro"
    gemini_research_model: str = "google/gemini-3.1-flash-lite"
    gemini_report_model: str = "google/gemini-3.1-pro-preview"
    gemini_catalog_model: str = "google/gemini-3.5-flash"
    gemini_catalog_judge_model: str = "google/gemini-3.1-pro-preview"
    gemini_embedding_model: str = "gemini-embedding-001"
    vertex_openai_location: str = "global"
    vertex_openai_model: str = "google/gemini-2.5-pro"
    openai_proxy_api_key: str | None = None
    mcp_proxy_token: str | None = None
    ocr_engine: str = "google_vision"
    ocr_max_pages: int = 5
    ocr_render_dpi: int = 180
    source_download_max_bytes: int = 20_000_000
    catalog_response_max_bytes: int = 2_000_000
    library_multipart_upload_max_bytes: int = 50_000_000
    library_direct_upload_max_bytes: int = 500_000_000
    shamsiyya_import_max_bytes: int = 150_000_000
    ocr_page_batch_size: int = 40
    ocr_full_document_max_pages: int = 600
    source_download_allowed_hosts: str = "openiti.org,archive.org,ia800,ia801,ia802,ia803,ia804,ia902,ia903"
    catalog_allowed_hosts: str = "loc.gov,worldcat.org,search.worldcat.org,hathitrust.org,catalog.hathitrust.org,archive.org,openiti.org"
    job_backend: str = "local"
    cloud_run_job_name: str | None = None
    cloud_run_job_location: str | None = None
    cloud_run_job_task_timeout_seconds: int = 3600

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator(
        "elasticsearch_url",
        "elasticsearch_api_key",
        "elastic_mcp_endpoint",
        "elastic_mcp_api_key",
        "agent_builder_agent_id",
        "openai_proxy_api_key",
        "mcp_proxy_token",
        "admin_api_token",
        "jury_proxy_token",
        "jury_access_code_hash",
        mode="before",
    )
    @classmethod
    def empty_placeholder_values(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized or normalized.lower() in {"placeholder", "none", "null", "not-configured"}:
            return None
        return normalized

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
