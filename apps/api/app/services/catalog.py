from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from app.models import CatalogSearchRequest
from app.services.elastic_service import ElasticService
from app.services.safe_http import allowed_host_list, fetch_limited_bytes
from app.settings import Settings

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "srw": "http://www.loc.gov/zing/srw/",
    "marc": "http://www.loc.gov/MARC21/slim",
}


class MarcXmlParser:
    def parse_record(self, record: ET.Element, *, protocol: str, query: str, record_url: str | None = None) -> dict[str, Any]:
        title = self._subfield(record, "245", ["a", "b"]) or self._dc(record, "title") or "Untitled catalog record"
        author = self._subfield(record, "100", ["a"]) or self._subfield(record, "700", ["a"]) or self._dc(record, "creator")
        subjects = self._subfields(record, "650", ["a", "x"]) + self._dc_all(record, "subject")
        shelfmark = self._subfield(record, "852", ["h", "j"]) or self._subfield(record, "090", ["a"]) or self._subfield(record, "099", ["a"])
        holding = self._subfield(record, "852", ["a", "b"]) or self._dc(record, "publisher")
        physical = self._subfield(record, "300", ["a", "b", "c"])
        dates = self._subfield(record, "260", ["c"]) or self._subfield(record, "264", ["c"]) or self._dc(record, "date")
        variants = self._subfields(record, "246", ["a", "b"])
        raw = ET.tostring(record, encoding="unicode")
        digest = hashlib.sha1(f"{protocol}:{query}:{title}:{author}:{shelfmark}:{raw[:300]}".encode()).hexdigest()[:16]
        return {
            "catalog_id": f"catalog-{digest}",
            "title": self._clean(title),
            "variant_titles": [self._clean(item) for item in variants if item],
            "author": self._clean(author or "Unknown"),
            "dates": self._clean(dates or ""),
            "subjects": [self._clean(item) for item in subjects if item],
            "holding_institution": self._clean(holding or ""),
            "shelfmark": self._clean(shelfmark or ""),
            "archive_number": self._clean(shelfmark or ""),
            "physical_description": self._clean(physical or ""),
            "record_url": record_url or "",
            "protocol": protocol,
            "evidence_status": "catalog_lead",
            "query": query,
            "raw_payload": {"xml": raw[:12000]},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _dc(self, record: ET.Element, tag: str) -> str | None:
        values = self._dc_all(record, tag)
        return values[0] if values else None

    def _dc_all(self, record: ET.Element, tag: str) -> list[str]:
        return [node.text or "" for node in record.findall(f".//dc:{tag}", NS) if node.text]

    def _subfield(self, record: ET.Element, field: str, codes: list[str]) -> str | None:
        values = self._subfields(record, field, codes)
        return " ".join(values) if values else None

    def _subfields(self, record: ET.Element, field: str, codes: list[str]) -> list[str]:
        values: list[str] = []
        for datafield in record.findall(f".//marc:datafield[@tag='{field}']", NS):
            for subfield in datafield.findall("marc:subfield", NS):
                if subfield.attrib.get("code") in codes and subfield.text:
                    values.append(subfield.text)
        return values

    def _clean(self, value: str) -> str:
        return " ".join(value.replace("/", " ").replace(":", " ").split())


class CatalogIntelligenceService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.elastic = ElasticService(settings)
        self.parser = MarcXmlParser()

    async def search(self, request: CatalogSearchRequest) -> list[dict]:
        if not request.endpoint_url:
            return self._demo_records(request)
        if request.protocol.lower() == "sru":
            records = await self._sru_search(request)
        elif request.protocol.lower() in {"oai_pmh", "oai-pmh", "oai"}:
            records = await self._oai_list_records(request)
        else:
            records = await self._sru_search(request)
            if not records:
                records = await self._oai_list_records(request)
        return [self._public_record(record) for record in records[: request.limit]]

    async def harvest(self, request: CatalogSearchRequest) -> list[dict]:
        records = await self.search(request)
        self.elastic.index_catalog_records(records)
        return records

    def indexed_records(self, query: str, limit: int = 20) -> list[dict]:
        return self.elastic.search_catalog_records(query, limit=limit)

    async def _sru_search(self, request: CatalogSearchRequest) -> list[dict]:
        params = {
            "version": "1.1",
            "operation": "searchRetrieve",
            "query": self._query(request),
            "maximumRecords": str(request.limit),
            "recordSchema": "marcxml",
        }
        url = httpx.URL(request.endpoint_url or "").copy_merge_params(params)
        fetched = await fetch_limited_bytes(
            str(url),
            allowed_hosts=allowed_host_list(self.settings.catalog_allowed_hosts),
            max_bytes=self.settings.catalog_response_max_bytes,
            timeout=15,
        )
        root = ET.fromstring(fetched.content.decode("utf-8", errors="replace"))
        records = root.findall(".//srw:recordData/*", NS)
        return [
            self.parser.parse_record(record, protocol="sru", query=request.query, record_url=request.endpoint_url)
            for record in records
        ]

    async def _oai_list_records(self, request: CatalogSearchRequest) -> list[dict]:
        params = {"verb": "ListRecords", "metadataPrefix": "marcxml"}
        url = httpx.URL(request.endpoint_url or "").copy_merge_params(params)
        fetched = await fetch_limited_bytes(
            str(url),
            allowed_hosts=allowed_host_list(self.settings.catalog_allowed_hosts),
            max_bytes=self.settings.catalog_response_max_bytes,
            timeout=15,
        )
        root = ET.fromstring(fetched.content.decode("utf-8", errors="replace"))
        records = root.findall(".//marc:record", NS) or root.findall(".//oai:metadata/*", NS)
        parsed = [
            self.parser.parse_record(record, protocol="oai_pmh", query=request.query, record_url=request.endpoint_url)
            for record in records
        ]
        terms = {term.lower() for term in [request.query, request.author or "", request.work or ""] if term}
        return [
            record
            for record in parsed
            if not terms or any(term in " ".join(map(str, record.values())).lower() for term in terms)
        ][: request.limit]

    def _query(self, request: CatalogSearchRequest) -> str:
        parts = [request.query, request.author or "", request.work or ""]
        return " or ".join(f'cql.anywhere="{part}"' for part in parts if part.strip())

    def _demo_records(self, request: CatalogSearchRequest) -> list[dict]:
        title = request.work or request.query
        record = {
            "catalog_id": f"catalog-demo-{hashlib.sha1(request.query.encode()).hexdigest()[:12]}",
            "title": title,
            "variant_titles": [request.query],
            "author": request.author or "Unknown",
            "dates": "",
            "subjects": ["catalog lead", "manuscript witness"],
            "holding_institution": "Demo catalog adapter",
            "shelfmark": "DEMO-MSS-001",
            "archive_number": "DEMO-MSS-001",
            "physical_description": "Catalog lead only; not textual evidence.",
            "record_url": request.endpoint_url or "demo://catalog",
            "protocol": request.protocol,
            "evidence_status": "catalog_lead",
            "query": request.query,
            "raw_payload": {"demo": True},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return [self._public_record(record)]

    def _public_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if key != "raw_payload"}
