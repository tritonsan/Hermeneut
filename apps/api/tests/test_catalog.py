import xml.etree.ElementTree as ET

from app.models import CatalogSearchRequest
from app.services.catalog import CatalogIntelligenceService, MarcXmlParser
from app.services.safe_http import UnsafeExternalUrlError, validate_external_url
from app.settings import Settings

MARCXML = """
<record xmlns="http://www.loc.gov/MARC21/slim">
  <datafield tag="100"><subfield code="a">Qutb al-Din al-Razi</subfield></datafield>
  <datafield tag="245"><subfield code="a">Sharh al-Matali</subfield></datafield>
  <datafield tag="246"><subfield code="a">شرح المطالع</subfield></datafield>
  <datafield tag="300"><subfield code="a">250 folios</subfield></datafield>
  <datafield tag="650"><subfield code="a">Logic</subfield></datafield>
  <datafield tag="852"><subfield code="a">Demo Library</subfield><subfield code="h">MS 42</subfield></datafield>
</record>
"""


def test_marcxml_parser_extracts_manuscript_fields():
    record = ET.fromstring(MARCXML)
    parsed = MarcXmlParser().parse_record(record, protocol="marcxml", query="شرح المطالع")

    assert parsed["title"] == "Sharh al-Matali"
    assert parsed["author"] == "Qutb al-Din al-Razi"
    assert parsed["shelfmark"] == "MS 42"
    assert parsed["holding_institution"] == "Demo Library"
    assert parsed["evidence_status"] == "catalog_lead"


def test_catalog_demo_search_returns_lead_not_evidence():
    service = CatalogIntelligenceService(Settings())
    service.elastic.index_catalog_records = lambda _records: (_ for _ in ()).throw(AssertionError("demo search must not index"))  # type: ignore[method-assign]
    records = service._demo_records(CatalogSearchRequest(query="شرح المطالع", protocol="demo"))

    assert records[0]["evidence_status"] == "catalog_lead"
    assert records[0]["shelfmark"] == "DEMO-MSS-001"
    assert "raw_payload" not in records[0]


def test_external_url_policy_rejects_private_hosts():
    try:
        validate_external_url("https://127.0.0.1/catalog", ("127.0.0.1",))
    except UnsafeExternalUrlError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("Expected private catalog host to be rejected")
