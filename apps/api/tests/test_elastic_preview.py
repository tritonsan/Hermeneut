from fastapi.testclient import TestClient

from app.dependencies import get_app_settings
from app.main import app
from app.services.elastic_service import ElasticService
from app.services.elastic_preview import ElasticBackupPreview
from app.settings import Settings


def test_backup_preview_contains_shamsiyya_works_and_relationships():
    preview = ElasticBackupPreview()

    catalog = preview.catalog()
    shamsiyya_works = [
        work for work in catalog["works"] if work.get("library_id") == "shamsiyya_hashiya_demo"
    ]
    work_ids = {work["work_id"] for work in shamsiyya_works}

    assert catalog["meta"]["backend"] == "elastic_backup_preview"
    assert catalog["meta"]["read_only"] is True
    assert {
        "katibi-shamsiyya",
        "qutb-razi-tahrir-shamsiyya",
        "sayyid-sharif-hashiya-shamsiyya",
        "siyalkuti-hashiya-shamsiyya",
        "issam-hashiya-shamsiyya",
    }.issubset(work_ids)
    assert preview.relationships("shamsiyya_hashiya_demo")


def test_elastic_service_prefers_backup_preview_when_elastic_is_not_configured():
    service = ElasticService(Settings(elasticsearch_url=None, elasticsearch_api_key=None))

    catalog = service.search_library("Shamsiyya")

    assert service.mode() == "elastic_backup_preview"
    assert catalog["meta"]["backend"] == "elastic_backup_preview"
    assert catalog["meta"]["read_only"] is True


def test_backup_preview_source_is_available_to_source_resolution():
    preview = ElasticBackupPreview()

    source = preview.source("shamsiyya-katibi-matn")

    assert source
    assert source["work_id"] == "katibi-shamsiyya"
    assert "gcs_raw_path" not in source


def test_backup_preview_passage_context_uses_fixture_passages():
    service = ElasticService(Settings())
    catalog = service.search_library("الشمسية")
    passage_id = catalog["passages"][0]["passage_id"]

    result = service.passage_context(passage_id, window=1)

    assert result["items"]
    assert result["items"][0].get("location_label")
    assert result["items"][0].get("citation_hint")


def test_backup_preview_rejects_operator_mutations():
    app.dependency_overrides[get_app_settings] = lambda: Settings(admin_api_token="preview-admin")
    try:
        response = TestClient(app).post(
            "/api/libraries/shamsiyya_hashiya_demo/sources",
            headers={"Authorization": "Bearer preview-admin"},
            data={"provider": "Institutional Upload", "url": "https://example.org/source.pdf"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "preview_read_only"
