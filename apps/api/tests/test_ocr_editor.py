from app.models import OcrCorrectionRequest
from app.services.ocr_editor import OcrEditorService
from app.settings import Settings


def test_ocr_correction_rebuilds_page_passages_and_ground_truth(monkeypatch):
    service = OcrEditorService(Settings(gcs_bucket="demo-bucket"))
    source_doc = {
        "source_id": "demo-source",
        "work_id": "demo-work",
        "title": "Demo Work",
        "library_id": "demo-library",
        "gcs_ocr_path": "gs://demo-bucket/ocr/demo-library/demo-source/ocr.json",
        "gcs_normalized_path": "gs://demo-bucket/normalized/demo-library/demo-source/passages.jsonl",
    }
    ocr_payload = {
        "source_id": "demo-source",
        "pages": [{"page_number": 1, "text": "bad ocr", "text_layer": "", "vision_text": "", "confidence": 0.4}],
    }
    writes = {}

    monkeypatch.setattr(service, "_source_doc", lambda source_id: source_doc)
    monkeypatch.setattr(service, "_ocr_payload", lambda doc: ocr_payload)
    monkeypatch.setattr(service.ocr, "_write_gcs", lambda path, content: writes.setdefault(path, content) or True)
    monkeypatch.setattr(service.ocr, "_store_json", lambda path, payload: writes.setdefault(path, payload) or True)
    monkeypatch.setattr(service.ocr, "_store_jsonl", lambda path, rows: writes.setdefault(path, rows) or True)
    monkeypatch.setattr(service.elastic, "delete_source_page_passages", lambda source_id, page_number: 1)
    monkeypatch.setattr(service.elastic, "index_extracted_passages", lambda source_doc, passages, replace_source=True: len(passages))
    monkeypatch.setattr(service.elastic, "index_ocr_correction", lambda correction: True)
    monkeypatch.setattr(service.elastic, "index_source_metadata", lambda source_doc: True)

    result = service.save_correction(
        "demo-source",
        1,
        OcrCorrectionRequest(corrected_text="corrected Arabic OCR text", correction_reason="test"),
    )

    assert result.reindexed_passage_count == 1
    assert result.ground_truth_path == "gs://demo-bucket/ground_truth/demo-library/demo-source/page-1.json"
    assert ocr_payload["pages"][0]["extraction_method"] == "human_corrected"
    assert result.metadata["correction"]["training_status"] == "ground_truth_candidate"
