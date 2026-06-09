import json
from dataclasses import dataclass
from urllib.parse import urlparse

from google.cloud.exceptions import GoogleCloudError

try:
    import fitz
except ImportError:  # pragma: no cover
    fitz = None

try:
    from google.cloud import vision
except ImportError:  # pragma: no cover
    vision = None

from app.services.google_clients import storage_client
from app.services.normalization import normalize_arabic
from app.services.safe_http import allowed_host_list, fetch_limited_bytes
from app.settings import Settings


@dataclass
class OcrPage:
    page_number: int
    text: str
    text_layer: str
    vision_text: str
    confidence: float
    extraction_method: str


class OcrProcessor:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def process_source(self, source_doc: dict) -> tuple[list[dict], dict]:
        try:
            raw_bytes = await self._load_source_bytes(source_doc)
            file_type = self._file_type(source_doc)
            if file_type == "pdf":
                pages, progress = self._process_pdf(raw_bytes, source_doc)
            else:
                pages = [self._process_text(raw_bytes)]
                progress = {
                    "ocr_total_pages": 1,
                    "ocr_processed_pages": 1,
                    "ocr_next_page": None,
                    "ocr_batch_size": 1,
                    "ocr_resume_available": False,
                }
        except Exception as exc:
            pages = []
            ocr_payload = {
                "source_id": source_doc["source_id"],
                "engine": self.settings.ocr_engine,
                "page_count": 0,
                "pages": [],
                "status": "ocr_failed",
                "error": str(exc),
            }
            self._store_json(source_doc.get("gcs_ocr_path"), ocr_payload)
            return [], ocr_payload

        engine = self._payload_engine(pages)
        ocr_payload = {
            "source_id": source_doc["source_id"],
            "engine": engine,
            "page_count": len(pages),
            "pages": [page.__dict__ for page in pages],
            "status": "ocr_partial" if progress.get("ocr_resume_available") else "ocr_completed" if pages else "ocr_failed",
            **progress,
        }
        self._store_json(source_doc.get("gcs_ocr_path"), ocr_payload)

        passages = []
        passage_order = 0
        for page in pages:
            for chunk_index, chunk in enumerate(self._chunk_text(page.text), start=1):
                passage_order += 1
                passages.append(
                    {
                        "passage_id": f"{source_doc['source_id']}-ocr-{page.page_number}-{chunk_index}",
                        "text_raw": chunk,
                        "text_normalized": normalize_arabic(chunk),
                        "translation_hint": "OCR/text-layer extraction from an ingested source; human verification required.",
                        "concepts": ["ocr", "source discovery", "institutional library"],
                        "page_ref": f"ocr:{page.page_number}:{chunk_index}",
                        "source_page": str(page.page_number),
                        "passage_order": passage_order,
                        "chunk_index": chunk_index,
                        "ocr_confidence": page.confidence,
                        "extraction_method": page.extraction_method,
                    }
                )
        self._store_jsonl(source_doc.get("gcs_normalized_path"), passages)
        return passages, ocr_payload

    def _payload_engine(self, pages: list[OcrPage]) -> str:
        methods = {page.extraction_method for page in pages}
        if methods <= {"text_file"}:
            return "internet_archive_or_text_layer"
        if "google_vision_full_ocr" in methods or "google_vision_ocr" in methods:
            return "google_vision"
        if "pdf_text_layer" in methods:
            return "pdf_text_layer"
        return self.settings.ocr_engine

    def _chunk_text(self, text: str, max_chars: int = 1800) -> list[str]:
        cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not cleaned:
            return []
        paragraphs = cleaned.split("\n")
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

    async def _load_source_bytes(self, source_doc: dict) -> bytes:
        gcs_raw_path = source_doc.get("gcs_raw_path")
        if gcs_raw_path:
            content = self._read_gcs(gcs_raw_path)
            if content:
                return content

        url = source_doc.get("download_url") or source_doc.get("url")
        if not url:
            raise ValueError("OCR requires a GCS raw object or a download URL.")
        if "example.com" in url:
            raise ValueError("Example URLs cannot be processed as OCR sources.")
        fetched = await fetch_limited_bytes(
            url,
            allowed_hosts=allowed_host_list(self.settings.source_download_allowed_hosts),
            max_bytes=self.settings.source_download_max_bytes,
            timeout=30,
        )
        return fetched.content

    def _process_pdf(self, raw_bytes: bytes, source_doc: dict) -> tuple[list[OcrPage], dict]:
        if not fitz:
            return [self._process_text(raw_bytes, method="pdf_text_fallback_no_renderer")], {
                "ocr_total_pages": 1,
                "ocr_processed_pages": 1,
                "ocr_next_page": None,
                "ocr_batch_size": 1,
                "ocr_resume_available": False,
            }

        document = fitz.open(stream=raw_bytes, filetype="pdf")
        pages: list[OcrPage] = []
        full_document = bool(source_doc.get("ocr_full_document")) or str(source_doc.get("ocr_mode", "")).lower() == "full"
        total_pages = len(document)
        hard_cap = int(source_doc.get("ocr_full_document_max_pages") or self.settings.ocr_full_document_max_pages)
        if full_document:
            max_pages = min(total_pages, hard_cap)
            start_page = max(1, int(source_doc.get("ocr_next_page") or 1))
            batch_size = max(1, int(source_doc.get("ocr_batch_size") or self.settings.ocr_page_batch_size))
            end_page = min(max_pages, start_page + batch_size - 1)
        else:
            max_pages = min(total_pages, int(source_doc.get("ocr_max_pages") or self.settings.ocr_max_pages))
            start_page = 1
            batch_size = max_pages
            end_page = max_pages
        for page_number, page in enumerate(document, start=1):
            if page_number < start_page:
                continue
            if page_number > end_page:
                break
            text_layer = page.get_text("text").strip()
            vision_text = self._vision_ocr_page(page)
            chosen = vision_text.strip() or text_layer
            method = "google_vision_full_ocr" if vision_text.strip() and full_document else "google_vision_ocr" if vision_text.strip() else "pdf_text_layer"
            confidence = 0.9 if vision_text.strip() else (0.72 if text_layer else 0.0)
            if chosen.strip():
                pages.append(
                    OcrPage(
                        page_number=page_number,
                        text=chosen.strip(),
                        text_layer=text_layer,
                        vision_text=vision_text.strip(),
                        confidence=confidence,
                        extraction_method=method,
                    )
                )
        document.close()
        processed_pages = max(0, end_page - start_page + 1) if pages else 0
        next_page = end_page + 1 if full_document and end_page < max_pages else None
        return pages, {
            "ocr_total_pages": total_pages,
            "ocr_processed_pages": int(source_doc.get("ocr_processed_pages") or 0) + processed_pages,
            "ocr_next_page": next_page,
            "ocr_batch_size": batch_size,
            "ocr_resume_available": next_page is not None,
            "ocr_full_document_max_pages": hard_cap,
        }

    def _vision_ocr_page(self, page) -> str:
        if self.settings.ocr_engine != "google_vision" or not vision or not self.settings.google_cloud_project:
            return ""
        try:
            zoom = self.settings.ocr_render_dpi / 72
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = vision.Image(content=pixmap.tobytes("png"))
            client = vision.ImageAnnotatorClient()
            response = client.document_text_detection(image=image)
            if response.error.message:
                return ""
            return response.full_text_annotation.text or ""
        except Exception:
            return ""

    def _process_text(self, raw_bytes: bytes, method: str = "text_file") -> OcrPage:
        text = raw_bytes.decode("utf-8", errors="ignore").strip()
        quality = self._text_quality(text)
        return OcrPage(
            page_number=1,
            text=text,
            text_layer=text,
            vision_text="",
            confidence=quality,
            extraction_method=method,
        )

    def _text_quality(self, text: str) -> float:
        if not text:
            return 0.0
        sample = text[:12000]
        letters = [char for char in sample if char.isalpha()]
        if not letters:
            return 0.15
        arabic_letters = [
            char for char in letters
            if "\u0600" <= char <= "\u06ff" or "\u0750" <= char <= "\u077f" or "\u08a0" <= char <= "\u08ff"
        ]
        arabic_ratio = len(arabic_letters) / max(len(letters), 1)
        replacement_ratio = sample.count("\ufffd") / max(len(sample), 1)
        compact_ratio = sum(1 for char in sample if char.isspace()) / max(len(sample), 1)
        if arabic_ratio >= 0.72 and replacement_ratio < 0.01:
            base = 0.88
        elif arabic_ratio >= 0.45 and replacement_ratio < 0.03 and compact_ratio > 0.06:
            base = 0.72
        elif arabic_ratio >= 0.25:
            base = 0.52
        else:
            base = 0.38
        # Script ratio alone cannot tell coherent text from garbled OCR (which is
        # still "Arabic letters"). Apply a linguistic-coherence penalty so broken
        # scans do not get stamped as strong evidence.
        return round(base * self._text_coherence(sample), 3)

    def _text_coherence(self, sample: str) -> float:
        """Heuristic 0..1 coherence score. Garbled OCR shows many 1-2 char
        fragments and stray digit-bearing tokens; coherent prose does not."""
        tokens = [token for token in sample.split() if token]
        if len(tokens) < 20:
            return 1.0  # too little text to judge; do not penalize short legit snippets
        short_ratio = sum(1 for token in tokens if len(token) <= 2) / len(tokens)
        digit_ratio = sum(1 for token in tokens if any(char.isdigit() for char in token)) / len(tokens)
        avg_len = sum(len(token) for token in tokens) / len(tokens)
        coherence = 1.0
        if digit_ratio > 0.05:
            coherence -= (digit_ratio - 0.05) * 4.0
        if short_ratio > 0.40:
            coherence -= (short_ratio - 0.40) * 1.5
        if avg_len < 3.0:
            coherence -= (3.0 - avg_len) * 0.2
        return max(0.25, min(1.0, coherence))

    def _file_type(self, source_doc: dict) -> str:
        file_type = str(source_doc.get("file_type") or "").lower()
        url = str(source_doc.get("download_url") or source_doc.get("url") or "").lower()
        if "pdf" in file_type or url.endswith(".pdf"):
            return "pdf"
        return "text"

    def _read_gcs(self, gcs_path: str) -> bytes | None:
        parsed = self._parse_gcs(gcs_path)
        if not parsed or not self.settings.google_cloud_project:
            return None
        bucket_name, object_name = parsed
        try:
            client = self._storage_client()
            return client.bucket(bucket_name).blob(object_name).download_as_bytes()
        except GoogleCloudError:
            return None

    def _store_json(self, gcs_path: str | None, payload: dict) -> bool:
        if not gcs_path:
            return False
        return self._write_gcs(gcs_path, json.dumps(payload, ensure_ascii=False, indent=2).encode())

    def _store_jsonl(self, gcs_path: str | None, rows: list[dict]) -> bool:
        if not gcs_path:
            return False
        content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode()
        return self._write_gcs(gcs_path, content)

    def _write_gcs(self, gcs_path: str, content: bytes) -> bool:
        parsed = self._parse_gcs(gcs_path)
        if not parsed or not self.settings.google_cloud_project:
            return False
        bucket_name, object_name = parsed
        try:
            client = self._storage_client()
            client.bucket(bucket_name).blob(object_name).upload_from_string(content)
            return True
        except GoogleCloudError:
            return False

    def _storage_client(self):
        return storage_client(self.settings)

    def _parse_gcs(self, gcs_path: str) -> tuple[str, str] | None:
        parsed = urlparse(gcs_path)
        if parsed.scheme != "gs" or not parsed.netloc:
            return None
        return parsed.netloc, parsed.path.lstrip("/")
