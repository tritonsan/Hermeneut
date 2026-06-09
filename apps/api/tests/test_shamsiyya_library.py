from pathlib import Path

import pytest

from app.services.shamsiyya_library import LIBRARY_ID, ShamsiyyaLibraryParser


def test_shamsiyya_parser_splits_docx_into_author_layers():
    root = Path(__file__).resolve().parents[3]
    paths = list((root / "docs").glob("TAS*HAS*2.docx"))
    if not paths:
        pytest.skip("Demo DOCX source files are intentionally omitted from the public repository.")
    files = [
        (path.name, path.read_bytes())
        for path in paths
    ]

    payload = ShamsiyyaLibraryParser(gcs_bucket="demo-bucket").parse_files(files)

    assert payload["library_id"] == LIBRARY_ID
    assert payload["source_count"] == 5
    assert payload["passage_count"] > 100
    assert len(payload["edges"]) >= 20
    source_ids = {source["source_id"] for source in payload["sources"]}
    assert source_ids == {
        "shamsiyya-katibi-matn",
        "shamsiyya-qutb-razi-sharh",
        "shamsiyya-sayyid-sharif-hashiya",
        "shamsiyya-siyalkuti-hashiya",
        "shamsiyya-issam-hashiya",
    }
    qutb = next(source for source in payload["sources"] if source["source_id"] == "shamsiyya-qutb-razi-sharh")
    assert qutb["library_id"] == "shamsiyya_hashiya_demo"
    assert qutb["work_id"] == "qutb-razi-tahrir-shamsiyya"
    assert qutb["gcs_raw_path"].startswith("gs://demo-bucket/raw/shamsiyya_hashiya_demo/")
    assert qutb["text_layer"] == "sharh"
    assert any(
        passage["source_id"] == "shamsiyya-issam-hashiya"
        and passage["library_id"] == "shamsiyya_hashiya_demo"
        and passage["text_layer"] == "hashiya"
        for passage in payload["passages"]
    )
    sayyid_passages = [
        passage["text_raw"]
        for passage in payload["passages"]
        if passage["source_id"] == "shamsiyya-sayyid-sharif-hashiya"
    ]
    assert not any("السيلكوتي" in text or "عبد الحكيم" in text for text in sayyid_passages)
    assert sum(1 for passage in payload["passages"] if passage["source_id"] == "shamsiyya-siyalkuti-hashiya") > 700
    assert any(
        edge["from_id"] == "siyalkuti-hashiya-shamsiyya"
        and edge["to_id"] == "qutb-razi-tahrir-shamsiyya"
        and edge["relation"] == "glosses"
        for edge in payload["edges"]
    )
    assert any(
        edge["from_id"] == "qutb-razi-tahrir-shamsiyya"
        and edge["to_id"] == "katibi-shamsiyya"
        and edge["relation"] == "comments_on"
        for edge in payload["edges"]
    )
