from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from hashlib import sha1
from io import BytesIO
from zipfile import ZipFile

from app.services.normalization import normalize_arabic

LIBRARY_ID = "shamsiyya_hashiya_demo"


AUTHOR_LAYERS = {
    "katibi": {
        "marker": "قال علي بن عمر الكاتبي",
        "author_name": "Najm al-Din al-Katibi",
        "author_name_ar": "علي بن عمر الكاتبي",
        "source_id": "shamsiyya-katibi-matn",
        "work_id": "katibi-shamsiyya",
        "work_title": "al-Risala al-Shamsiyya",
        "work_title_ar": "الرسالة الشمسية",
        "text_layer": "matn",
        "source_role": "base_text",
        "layer_rank": 1,
        "depends_on": [],
    },
    "qutb_razi": {
        "marker": "قال قطب الدين الرازي",
        "author_name": "Qutb al-Din al-Razi",
        "author_name_ar": "قطب الدين الرازي",
        "source_id": "shamsiyya-qutb-razi-sharh",
        "work_id": "qutb-razi-tahrir-shamsiyya",
        "work_title": "Tahrir al-qawaid al-mantiqiyya fi sharh al-Risala al-Shamsiyya",
        "work_title_ar": "تحرير القواعد المنطقية في شرح الرسالة الشمسية",
        "text_layer": "sharh",
        "source_role": "primary_commentary",
        "layer_rank": 2,
        "depends_on": ["katibi"],
    },
    "sayyid_sharif": {
        "marker": "السيد الشريف الجرجاني",
        "author_name": "Sayyid Sharif al-Jurjani",
        "author_name_ar": "السيد الشريف الجرجاني",
        "source_id": "shamsiyya-sayyid-sharif-hashiya",
        "work_id": "sayyid-sharif-hashiya-shamsiyya",
        "work_title": "Hashiya on Tahrir al-qawaid",
        "work_title_ar": "حاشية السيد الشريف على تحرير القواعد",
        "text_layer": "hashiya",
        "source_role": "hashiya",
        "layer_rank": 3,
        "depends_on": ["qutb_razi", "katibi"],
    },
    "siyalkuti": {
        "marker": "السيلكوتي",
        "author_name": "Abd al-Hakim al-Siyalkuti",
        "author_name_ar": "عبد الحكيم السيلكوتي",
        "source_id": "shamsiyya-siyalkuti-hashiya",
        "work_id": "siyalkuti-hashiya-shamsiyya",
        "work_title": "Hashiya on the Shamsiyya commentary tradition",
        "work_title_ar": "حاشية السيلكوتي على الشمسية",
        "text_layer": "hashiya",
        "source_role": "later_hashiya",
        "layer_rank": 4,
        "depends_on": ["sayyid_sharif", "qutb_razi", "katibi"],
    },
    "issam": {
        "marker": "عصام الدين الإسفراييني",
        "author_name": "Isam al-Din al-Isfarayini",
        "author_name_ar": "عصام الدين الإسفراييني",
        "source_id": "shamsiyya-issam-hashiya",
        "work_id": "issam-hashiya-shamsiyya",
        "work_title": "Hashiya on al-Shamsiyya",
        "work_title_ar": "حاشية عصام الدين على الشمسية",
        "text_layer": "hashiya",
        "source_role": "later_hashiya",
        "layer_rank": 4,
        "depends_on": ["qutb_razi", "katibi"],
    },
}

MARKER_PATTERN = re.compile(
    r"^(?:(?:\(?المبحث\s*-?\s*(?P<mabhath>\d+)\)?)\s*)?"
    r"(?P<marker>قال علي بن عمر الكاتبي|قال قطب الدين الرازي|السيد الشريف الجرجاني|عبد الحكيم السيلكوتي|السيلكوتي|عصام الدين الإسفراييني)\s*"
)

EMBEDDED_MARKER_PATTERN = re.compile(
    r"(?P<marker>قال علي بن عمر الكاتبي|قال قطب الدين الرازي|السيد الشريف الجرجاني|عبد الحكيم السيلكوتي|السيلكوتي|عصام الدين الإسفراييني)"
    r"(?=\s*(?:\(|قوله|قال|$))"
)

MABHATH_PATTERN = re.compile(r"^\(?المبحث\s*-?\s*(?P<mabhath>\d+)\)?\s*")


@dataclass
class LayerSegment:
    layer_id: str
    section: str
    mabhath: str | None
    paragraphs: list[str] = field(default_factory=list)


class ShamsiyyaLibraryParser:
    """Parse layered Shamsiyya DOCX files into separate author/source layers."""

    def __init__(self, gcs_bucket: str = "hermeneut-sources"):
        self.gcs_bucket = gcs_bucket

    def parse_files(self, files: list[tuple[str, bytes]]) -> dict:
        segments: list[LayerSegment] = []
        for filename, content in files:
            section = self._section_from_filename(filename)
            segments.extend(self._parse_file(filename, content, section))
        return self._build_library_payload(segments)

    def _parse_file(self, filename: str, content: bytes, section: str) -> list[LayerSegment]:
        paragraphs = self._docx_paragraphs(content)
        current_layer = "unknown"
        current_mabhath: str | None = None
        segments: list[LayerSegment] = []
        current: LayerSegment | None = None

        def flush() -> None:
            nonlocal current
            if current and current.paragraphs:
                segments.append(current)
            current = None

        for paragraph in paragraphs:
            text = paragraph.strip()
            if not text:
                continue
            pieces = self._split_embedded_markers(text)
            for text in pieces:
                marker_match = MARKER_PATTERN.match(text)
                mabhath_match = MABHATH_PATTERN.match(text)
                if marker_match:
                    flush()
                    current_mabhath = marker_match.group("mabhath") or current_mabhath
                    current_layer = self._layer_for_marker(marker_match.group("marker"))
                    text = text[marker_match.end() :].strip()
                    current = LayerSegment(layer_id=current_layer, section=section, mabhath=current_mabhath)
                    if text:
                        current.paragraphs.append(text)
                    continue
                if mabhath_match:
                    flush()
                    current_mabhath = mabhath_match.group("mabhath")
                    text = text[mabhath_match.end() :].strip()
                if current is None:
                    current = LayerSegment(layer_id=current_layer, section=section, mabhath=current_mabhath)
                if text:
                    current.paragraphs.append(text)
        flush()
        return [segment for segment in segments if segment.layer_id in AUTHOR_LAYERS and segment.paragraphs]

    def _split_embedded_markers(self, text: str) -> list[str]:
        """Split paragraphs when a later marginal layer starts inside the same DOCX paragraph."""
        matches = list(EMBEDDED_MARKER_PATTERN.finditer(text))
        if not matches:
            return [text]
        boundaries: list[int] = []
        for match in matches:
            if match.start() > 0:
                boundaries.append(match.start())
        if not boundaries:
            return [text]
        pieces: list[str] = []
        start = 0
        for boundary in boundaries:
            piece = text[start:boundary].strip()
            if piece:
                pieces.append(piece)
            start = boundary
        tail = text[start:].strip()
        if tail:
            pieces.append(tail)
        return pieces

    def _docx_paragraphs(self, content: bytes) -> list[str]:
        with ZipFile(BytesIO(content)) as archive:
            xml = archive.read("word/document.xml")
        root = ET.fromstring(xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", namespace):
            parts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
            text = "".join(parts).strip()
            if text:
                paragraphs.append(text)
        return paragraphs

    def _build_library_payload(self, segments: list[LayerSegment]) -> dict:
        grouped: dict[str, list[LayerSegment]] = {layer_id: [] for layer_id in AUTHOR_LAYERS}
        for segment in segments:
            grouped.setdefault(segment.layer_id, []).append(segment)
        sources: list[dict] = []
        passages: list[dict] = []
        active_layers: list[str] = []
        for layer_id, layer_segments in grouped.items():
            if not layer_segments:
                continue
            active_layers.append(layer_id)
            meta = AUTHOR_LAYERS[layer_id]
            source_doc = self._source_doc(meta, layer_segments)
            sources.append(source_doc)
            passages.extend(self._passages_for_layer(meta, layer_segments, source_doc))
        return {
            "library_id": LIBRARY_ID,
            "source_count": len(sources),
            "passage_count": len(passages),
            "sources": sources,
            "passages": passages,
            "edges": self._relationship_edges(active_layers),
        }

    def _source_doc(self, meta: dict, segments: list[LayerSegment]) -> dict:
        raw_object = f"raw/{LIBRARY_ID}/author_layers/{meta['source_id']}/source.txt"
        ocr_object = f"ocr/{LIBRARY_ID}/{meta['source_id']}/ocr.json"
        normalized_object = f"normalized/{LIBRARY_ID}/{meta['source_id']}/passages.jsonl"
        return {
            "source_id": meta["source_id"],
            "work_id": meta["work_id"],
            "provider": "Hermeneut Shamsiyya DOCX split",
            "title": meta["work_title"],
            "title_ar": meta["work_title_ar"],
            "author_name": meta["author_name"],
            "author_name_ar": meta["author_name_ar"],
            "url": f"local-docx://{LIBRARY_ID}/{meta['source_id']}",
            "source_page_url": None,
            "download_url": None,
            "file_type": "text",
            "license_note": "User-provided demo library source; verify rights before redistribution.",
            "quality": 0.82,
            "library_id": LIBRARY_ID,
            "visibility": "private",
            "license_status": "institution_owned",
            "institution_owned": True,
            "ingestion_status": "searchable",
            "lifecycle_status": "searchable",
            "download_policy": "institutional_upload_split",
            "verification_status": "searchable_text_indexed",
            "ocr_status": "docx_text_layer_completed",
            "ocr_engine": "docx_text_layer_splitter",
            "ocr_page_count": len(segments),
            "indexed_passage_count": 0,
            "source_role": meta["source_role"],
            "text_layer": meta["text_layer"],
            "layer_rank": meta["layer_rank"],
            "depends_on_work_ids": [AUTHOR_LAYERS[layer]["work_id"] for layer in meta["depends_on"]],
            "base_work": "al-Risala al-Shamsiyya",
            "commentary_chain": [
                "al-Katibi",
                "Qutb al-Din al-Razi",
                "Sayyid Sharif al-Jurjani",
                "Abd al-Hakim al-Siyalkuti",
                "Isam al-Din al-Isfarayini",
            ],
            "gcs_raw_path": f"gs://{self.gcs_bucket}/{raw_object}",
            "gcs_ocr_path": f"gs://{self.gcs_bucket}/{ocr_object}",
            "gcs_normalized_path": f"gs://{self.gcs_bucket}/{normalized_object}",
            "raw_object": raw_object,
            "ocr_object": ocr_object,
            "normalized_object": normalized_object,
        }

    def _passages_for_layer(self, meta: dict, segments: list[LayerSegment], source_doc: dict) -> list[dict]:
        passages: list[dict] = []
        passage_order = 0
        for segment_index, segment in enumerate(segments, start=1):
            for chunk_index, chunk in enumerate(self._chunk_paragraphs(segment.paragraphs), start=1):
                passage_order += 1
                passage_id = f"{meta['source_id']}-{segment.section}-{segment.mabhath or 'intro'}-{segment_index}-{chunk_index}"
                passages.append(
                    {
                        "passage_id": passage_id,
                        "text_raw": chunk,
                        "text_normalized": normalize_arabic(chunk),
                        "translation_hint": (
                            "Author-layer extraction from the Shamsiyya hashiya demo library; "
                            "human verification against the original DOCX/PDF is required."
                        ),
                        "concepts": [
                            "Shamsiyya",
                            "Arabic logic",
                            segment.section,
                            meta["text_layer"],
                            meta["source_role"],
                        ],
                        "source_id": meta["source_id"],
                        "work_id": meta["work_id"],
                        "work_title": meta["work_title"],
                        "author_name": meta["author_name"],
                        "domain": "Arabic logic/commentary",
                        "library_id": LIBRARY_ID,
                        "section_ref": segment.section,
                        "page_ref": f"{segment.section}:mabhath:{segment.mabhath or 'intro'}:{chunk_index}",
                        "source_page": segment.mabhath or "intro",
                        "passage_order": passage_order,
                        "chunk_index": chunk_index,
                        "ocr_confidence": 0.96,
                        "extraction_method": "docx_text_layer_author_split",
                        "text_layer": meta["text_layer"],
                        "source_role": meta["source_role"],
                        "layer_rank": meta["layer_rank"],
                        "depends_on_work_ids": [AUTHOR_LAYERS[layer]["work_id"] for layer in meta["depends_on"]],
                    }
                )
        return passages

    def _relationship_edges(self, active_layers: list[str]) -> list[dict]:
        active = set(active_layers)
        edges: list[dict] = []
        for layer_id in active_layers:
            meta = AUTHOR_LAYERS[layer_id]
            edges.append(
                self._edge(
                    from_id=meta["source_id"],
                    to_id=meta["work_id"],
                    from_type="source",
                    to_type="work",
                    relation="textual_layer_of",
                    confidence=0.98,
                    reasoning=(
                        "The uploaded DOCX was split by explicit author/layer markers; "
                        "this source object is the searchable text layer for the work."
                    ),
                    evidence_snippet=meta["marker"],
                )
            )
            for target_layer in meta["depends_on"]:
                if target_layer not in active:
                    continue
                target = AUTHOR_LAYERS[target_layer]
                relation = self._relation_for(meta, target)
                edges.append(
                    self._edge(
                        from_id=meta["work_id"],
                        to_id=target["work_id"],
                        from_type="work",
                        to_type="work",
                        relation=relation,
                        confidence=0.92 if target_layer == "qutb_razi" else 0.86,
                        reasoning=(
                            f"{meta['work_title']} belongs to the same Shamsiyya commentary tradition "
                            f"and structurally depends on {target['work_title']}."
                        ),
                        evidence_snippet=f"{meta['marker']} / {target['marker']}",
                    )
                )
                edges.append(
                    self._edge(
                        from_id=target["work_id"],
                        to_id=meta["work_id"],
                        from_type="work",
                        to_type="work",
                        relation="chronologically_prior_to",
                        confidence=0.9,
                        reasoning=(
                            "Chronology prevents later hashiyas from being sources for earlier works; "
                            "this edge is used as a guardrail during candidate ranking."
                        ),
                        evidence_snippet=f"{target['author_name']} precedes {meta['author_name']} in the commentary chain.",
                    )
                )
        for left in active_layers:
            for right in active_layers:
                if left >= right:
                    continue
                left_meta = AUTHOR_LAYERS[left]
                right_meta = AUTHOR_LAYERS[right]
                edges.append(
                    self._edge(
                        from_id=left_meta["work_id"],
                        to_id=right_meta["work_id"],
                        from_type="work",
                        to_type="work",
                        relation="same_debate_as",
                        confidence=0.72,
                        reasoning="Both works are indexed in the same institutional Shamsiyya logic library.",
                        evidence_snippet="shared library_id=shamsiyya_hashiya_demo",
                    )
                )
        return edges

    def _edge(
        self,
        from_id: str,
        to_id: str,
        from_type: str,
        to_type: str,
        relation: str,
        confidence: float,
        reasoning: str,
        evidence_snippet: str,
    ) -> dict:
        digest = sha1(f"{from_id}|{relation}|{to_id}".encode()).hexdigest()[:12]
        return {
            "edge_id": f"shamsiyya-{digest}",
            "library_id": LIBRARY_ID,
            "from": from_id,
            "to": to_id,
            "from_id": from_id,
            "to_id": to_id,
            "from_type": from_type,
            "to_type": to_type,
            "relation": relation,
            "type": relation.upper(),
            "direction": "directed",
            "confidence": confidence,
            "evidence_snippet": evidence_snippet,
            "reasoning_summary": reasoning,
            "source_url": "local-docx://shamsiyya_hashiya_demo",
            "provenance": "structured_docx_layer_analysis",
            "verification_status": "institutional_upload_curated",
            "chronology_status": "validated_by_commentary_chain",
            "model_trace": {
                "relationship_model": "structured_parser",
                "future_generalizer": "google/gemini-3.1-pro-preview",
            },
        }

    def _relation_for(self, meta: dict, target: dict) -> str:
        if meta["source_role"] == "primary_commentary":
            return "comments_on"
        if target["source_role"] == "primary_commentary":
            return "glosses"
        return "depends_on"

    def raw_text_for_source(self, source_doc: dict, passages: list[dict]) -> str:
        matching = [passage for passage in passages if passage["source_id"] == source_doc["source_id"]]
        return "\n\n".join(passage["text_raw"] for passage in matching)

    def _chunk_paragraphs(self, paragraphs: list[str], max_chars: int = 1800) -> list[str]:
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                if current:
                    chunks.append(current.strip())
                    current = ""
                chunks.extend(paragraph[index : index + max_chars].strip() for index in range(0, len(paragraph), max_chars))
                continue
            if current and len(current) + len(paragraph) + 1 > max_chars:
                chunks.append(current.strip())
                current = paragraph
            else:
                current = f"{current}\n{paragraph}".strip() if current else paragraph
        if current:
            chunks.append(current.strip())
        return chunks

    def _section_from_filename(self, filename: str) -> str:
        normalized = filename.lower()
        if "tasdik" in normalized or "tasd" in normalized:
            return "tasdiqat"
        return "tasawwurat"

    def _layer_for_marker(self, marker: str) -> str:
        if marker == "قال علي بن عمر الكاتبي":
            return "katibi"
        if marker == "قال قطب الدين الرازي":
            return "qutb_razi"
        if marker == "السيد الشريف الجرجاني":
            return "sayyid_sharif"
        if marker in {"السيلكوتي", "عبد الحكيم السيلكوتي"}:
            return "siyalkuti"
        if marker == "عصام الدين الإسفراييني":
            return "issam"
        return "unknown"

    def checksum(self, content: bytes) -> str:
        return sha1(content).hexdigest()
