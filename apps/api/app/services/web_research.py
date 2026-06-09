import re
from hashlib import sha1
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import google.auth
import httpx
from google.auth.transport.requests import Request

from app.data.seed import AUTHORS, SOURCES, WORKS
from app.models import DetectedContext, Hypothesis, RunCreate
from app.services.academic_research_chain import AcademicResearchChain
from app.services.bibliographic_reasoning import BibliographicReasoningService
from app.services.normalization import normalize_arabic
from app.settings import Settings


class WebResearchService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bibliographic_reasoning = BibliographicReasoningService(settings)
        self.academic_chain = AcademicResearchChain(settings)

    def research(
        self,
        payload: RunCreate,
        context: DetectedContext,
        hypotheses: list[Hypothesis],
    ) -> dict:
        if not payload.enable_web_research:
            return {
                "enabled": False,
                "model": self.settings.gemini_research_model,
                "research_questions": [],
                "candidate_authors": [],
                "candidate_works": [],
                "relationships": [],
                "source_candidates": [],
                "policy": "Web research disabled by user input.",
            }

        research_questions = self._research_questions(payload, context)
        web_hits, grounding_mode = self._grounded_or_backend_web_hits(payload, context, research_questions)
        academic_intelligence = self.academic_chain.run(payload, context, hypotheses, web_hits)
        reasoning = self.bibliographic_reasoning.reason(
            payload,
            context,
            hypotheses,
            web_hits,
            grounding_mode,
            academic_intelligence,
        )
        candidate_work_ids = [
            item["work_id"]
            for item in reasoning.get("work_candidates", [])
            if not str(item.get("work_id", "")).startswith(("web-work-", "tusi-", "katibi-", "jurjani-"))
        ]
        relationships = reasoning.get("relationship_graph", [])
        source_candidates = [
            *reasoning.get("source_candidates", []),
            *self._source_candidates(candidate_work_ids, payload),
            *self._web_source_candidates(web_hits, payload),
        ]

        return {
            "enabled": True,
            "model": self.settings.gemini_research_model,
            "report_model": self.settings.gemini_report_model,
            "embedding_model": self.settings.gemini_embedding_model,
            "grounding_mode": grounding_mode,
            "context_profile": reasoning.get("context_profile", {}),
            "academic_intelligence": academic_intelligence,
            "research_questions": research_questions,
            "grounded_search_queries": self._grounded_search_queries(payload, context, research_questions),
            "web_hits": web_hits,
            "candidate_authors": reasoning.get("author_candidates", []),
            "candidate_works": reasoning.get("work_candidates", []),
            "candidate_dossiers": academic_intelligence.get("candidate_dossiers", []),
            "decision_calibration": academic_intelligence.get("decision_calibration", {}),
            "decision_tier": academic_intelligence.get("decision_tier", "weak_lead"),
            "external_candidate_works": self._external_candidate_works(web_hits),
            "relationships": relationships,
            "phrase_variants": reasoning.get("phrase_variants", []),
            "candidate_web_searches": reasoning.get("candidate_web_searches", []),
            "web_hit_assessments": academic_intelligence.get("web_hit_assessments", []),
            "source_selection": academic_intelligence.get("source_selection", {}),
            "top_pdf_targets": reasoning.get("top_pdf_targets", []),
            "rejected_candidates": reasoning.get("rejected_candidates", []),
            "model_routing": reasoning.get("model_routing", {}),
            "source_candidates": source_candidates[: payload.max_source_candidates],
            "policy": (
                "Grounded bibliographic intelligence narrows candidates only; final attribution still "
                "requires Elastic textual evidence. Backend verification resolves source metadata, PDF URLs, "
                "GCS vault status, OCR state, and searchable indexing."
            ),
        }

    def relationship_fit(self, work_id: str, web_research: dict) -> float:
        if not web_research.get("enabled"):
            return 0.0
        candidate_ids = {work.get("work_id") for work in web_research.get("candidate_works", [])}
        if work_id in candidate_ids:
            return 0.85
        related_ids = {
            item.get("to_id")
            for item in web_research.get("relationships", [])
            if item.get("to_type") == "work"
        }
        return 0.55 if work_id in related_ids else 0.15

    def _research_questions(self, payload: RunCreate, context: DetectedContext) -> list[str]:
        containing_author = payload.containing_author or payload.suspected_author
        containing_work = payload.containing_work or payload.suspected_work
        questions = [
            f"Which classical authors discuss {', '.join(context.key_terms[:3]) or context.domain}?",
            "Which works cite, summarize, or refute this wording or doctrine?",
            "Which open-access text or scan candidates can be safely inspected?",
        ]
        if containing_author:
            questions.append(
                f"In works by {containing_author}, which earlier authors or books are likely citation targets for this phrase?"
            )
        if containing_work:
            questions.append(
                f"What source traditions does {containing_work} cite, summarize, or refute around this phrase?"
            )
        if payload.period_hint:
            questions.append(f"Which candidates fit the period hint: {payload.period_hint}?")
        return questions

    def _grounded_search_queries(
        self,
        payload: RunCreate,
        context: DetectedContext,
        research_questions: list[str],
    ) -> list[dict]:
        base = " ".join(
            item
            for item in [
                payload.passage,
                payload.containing_author or payload.suspected_author or "",
                payload.containing_work or payload.suspected_work or "",
                payload.period_hint or "",
                payload.domain_hint or context.domain,
            ]
            if item
        )
        return [
            {
                "query": f"{base} source work author PDF manuscript",
                "purpose": "Find source candidates and downloadable editions.",
                "grounding_provider": "Gemini Google Search grounding with backend fallback",
            },
            {
                "query": f"{base} OpenITI Internet Archive Wikidata",
                "purpose": "Resolve structured metadata and public text repositories.",
                "grounding_provider": "Gemini Google Search grounding with backend fallback",
            },
            *[
                {
                    "query": question,
                    "purpose": "Research question generated from the passage context.",
                    "grounding_provider": "Gemini Google Search grounding with backend fallback",
                }
                for question in research_questions[:2]
            ],
        ]

    def _grounded_or_backend_web_hits(
        self,
        payload: RunCreate,
        context: DetectedContext,
        research_questions: list[str],
    ) -> tuple[list[dict], str]:
        grounded_hits = self._gemini_grounded_hits(payload, context, research_questions)
        if grounded_hits:
            return grounded_hits, "gemini_google_search_grounding"
        backend_hits = self._general_web_hits(payload, context, research_questions)
        return backend_hits, "backend_web_search_fallback"

    def _gemini_grounded_hits(
        self,
        payload: RunCreate,
        context: DetectedContext,
        research_questions: list[str],
    ) -> list[dict]:
        if not self.settings.google_cloud_project:
            return []
        model_id = self.settings.gemini_research_model.removeprefix("google/")
        location = self.settings.vertex_openai_location or "global"
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        url = (
            f"https://{host}/v1/projects/"
            f"{self.settings.google_cloud_project}/locations/{location}/publishers/google/models/"
            f"{model_id}:generateContent"
        )
        prompt = (
            "Act as a cautious professor-level bibliographic research assistant for classical texts. First analyze "
            "the phrase as an academic problem, then identify possible source authors, works, schools, opponents, "
            "transmitters, doxographers, and parallel formulations. Do not over-privilege commentary/hashiya unless "
            "the containing context demands it. For each lead, search for public catalog pages, OpenITI, Internet "
            "Archive, Wikidata records, PDFs, OCR text, or classical-text repositories. Mark uncertainty and avoid "
            "final attribution; return source leads, relationship clues, and downloadable-source hints only.\n\n"
            f"Passage: {payload.passage}\n"
            f"Containing author: {payload.containing_author or 'unknown'}\n"
            f"Containing work: {payload.containing_work or 'unknown'}\n"
            f"Domain: {payload.domain_hint or context.domain}\n"
            f"Research questions: {' | '.join(research_questions[:4])}"
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
        }
        try:
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            credentials.refresh(Request())
            headers = {"Authorization": f"Bearer {credentials.token}", "Content-Type": "application/json"}
            response = httpx.post(url, headers=headers, json=body, timeout=12)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return []
        return self._grounding_response_hits(data, payload)

    def _grounding_response_hits(self, data: dict, payload: RunCreate) -> list[dict]:
        hits: list[dict] = []
        seen: set[str] = set()
        candidates = data.get("candidates", [])
        for candidate in candidates:
            metadata = candidate.get("groundingMetadata", {})
            chunks = metadata.get("groundingChunks", [])
            supports = metadata.get("groundingSupports", [])
            for chunk in chunks:
                web = chunk.get("web", {})
                uri = web.get("uri")
                if not uri or uri in seen:
                    continue
                seen.add(uri)
                title = web.get("title") or uri
                hits.append(self._normalized_web_hit(title, uri, "", payload.passage, "gemini_google_search_grounding"))
            for support in supports:
                segment = support.get("segment", {}).get("text", "")
                for index in support.get("groundingChunkIndices", []):
                    if isinstance(index, int) and index < len(chunks):
                        web = chunks[index].get("web", {})
                        uri = web.get("uri")
                        if uri and uri not in seen:
                            seen.add(uri)
                            title = web.get("title") or uri
                            hits.append(
                                self._normalized_web_hit(
                                    title,
                                    uri,
                                    segment,
                                    payload.passage,
                                    "gemini_google_search_grounding",
                                )
                            )
        return hits[:8]

    def _candidate_author_ids(
        self,
        payload: RunCreate,
        hypotheses: list[Hypothesis],
        normalized: str,
    ) -> list[str]:
        ids: list[str] = []

        def add(author_id: str) -> None:
            if author_id not in ids:
                ids.append(author_id)

        for hypothesis in hypotheses:
            for author in AUTHORS:
                haystack = normalize_arabic(" ".join([author["name"], author["name_ar"], *author["aliases"]]))
                if normalize_arabic(hypothesis.author) in haystack:
                    add(author["author_id"])

        hint = normalize_arabic(payload.suspected_author or "")
        if hint:
            for author in AUTHORS:
                haystack = normalize_arabic(" ".join([author["name"], author["name_ar"], *author["aliases"]]))
                if hint in haystack or haystack in hint:
                    add(author["author_id"])

        if any(term in normalized for term in ["العالم", "قديم", "الفلاسفه", "الاول"]):
            for author_id in ["al-ghazali", "ibn-sina", "al-farabi", "fakhr-al-din-al-razi"]:
                add(author_id)
        if any(term in normalized for term in ["المعتزله", "العبد", "فعله"]):
            add("al-ashari")
        return ids or ["al-ghazali", "ibn-sina", "fakhr-al-din-al-razi"]

    def _candidate_work_ids(
        self,
        payload: RunCreate,
        hypotheses: list[Hypothesis],
        author_ids: list[str],
        normalized: str,
    ) -> list[str]:
        ids: list[str] = []

        def add(work_id: str) -> None:
            if work_id not in ids:
                ids.append(work_id)

        for hypothesis in hypotheses:
            if hypothesis.work_id:
                add(hypothesis.work_id)

        hint = normalize_arabic(payload.suspected_work or "")
        for work in WORKS:
            haystack = normalize_arabic(" ".join([work["title"], work["title_ar"]]))
            if work["author_id"] in author_ids or (hint and (hint in haystack or haystack in hint)):
                add(work["work_id"])

        if any(term in normalized for term in ["العالم", "قديم", "الفلاسفه"]):
            for work_id in ["ghazali-tahafut", "ibn-sina-isharat", "razi-muhassal"]:
                add(work_id)
        return ids

    def _relationships(self, author_ids: list[str], work_ids: list[str], normalized: str) -> list[dict]:
        relationships: list[dict] = []
        for work in WORKS:
            if work["work_id"] in work_ids:
                relationships.append(
                    {
                        "edge_id": f"{work['author_id']}-wrote-{work['work_id']}",
                        "from_type": "author",
                        "from_id": work["author_id"],
                        "to_type": "work",
                        "to_id": work["work_id"],
                        "relation": "wrote",
                        "source_url": "seed://hermeneut/authors-works",
                        "provenance": "curated_seed",
                        "confidence": 0.9,
                        "verification_status": "curator_confirmed",
                    }
                )
        if "al-ghazali" in author_ids and any(term in normalized for term in ["الفلاسفه", "العالم", "قديم"]):
            relationships.append(
                {
                    "edge_id": "al-ghazali-refutes-falasifa",
                    "from_type": "author",
                    "from_id": "al-ghazali",
                    "to_type": "concept_or_group",
                    "to_id": "falasifa",
                    "relation": "refutes",
                    "source_url": "https://openiti.org/",
                    "provenance": "OpenITI-style metadata and demo curation",
                    "confidence": 0.82,
                    "verification_status": "machine_suggested",
                }
            )
        return relationships

    def _source_candidates(self, work_ids: list[str], payload: RunCreate) -> list[dict]:
        if not payload.allow_source_download_suggestions:
            return []
        candidates: list[dict] = []
        for source in SOURCES:
            if source["work_id"] in work_ids:
                candidates.append(
                    {
                        "source_id": source["source_id"],
                        "work_id": source["work_id"],
                        "provider": source["provider"],
                        "url": source["url"],
                        "download_policy": "admin_approval_required",
                        "ingestion_status": source.get("ingestion_status", "indexed"),
                        "lifecycle_status": "searchable" if source.get("ingestion_status") == "indexed" else "download_candidate",
                        "relationship_reason": "Candidate source belongs to a narrowed work candidate.",
                        "provenance": "curated_seed",
                        "verification_status": "curator_confirmed",
                        "license_status": source.get("license_status", "public_domain_or_demo"),
                        "download_url": source.get("url"),
                        "gcs_raw_path": source.get("gcs_raw_path"),
                        "gcs_ocr_path": source.get("gcs_ocr_path"),
                        "gcs_normalized_path": source.get("gcs_normalized_path"),
                    }
                )
        return candidates

    def _general_web_hits(
        self,
        payload: RunCreate,
        context: DetectedContext,
        research_questions: list[str],
    ) -> list[dict]:
        queries = [
            payload.passage,
            f"{payload.passage} PDF",
            f"{payload.passage} {payload.containing_author or payload.suspected_author or ''} {payload.containing_work or payload.suspected_work or ''}",
            *research_questions[:2],
        ]
        hits: list[dict] = []
        seen: set[str] = set()
        with httpx.Client(timeout=8, follow_redirects=True, headers={"User-Agent": "HermeneutHackathon/0.2"}) as client:
            for query in queries:
                if not query.strip():
                    continue
                for hit in self._duckduckgo_html_hits(client, query):
                    url = hit["url"]
                    if url in seen:
                        continue
                    seen.add(url)
                    hits.append({**hit, "query": query, "domain_hint": context.domain})
                    if len(hits) >= 8:
                        return hits
        return hits

    def _duckduckgo_html_hits(self, client: httpx.Client, query: str) -> list[dict]:
        try:
            response = client.get("https://duckduckgo.com/html/", params={"q": query})
            response.raise_for_status()
        except Exception:
            return []
        html = response.text
        rows = re.findall(
            r'<a rel="nofollow" class="result__a" href="(?P<url>.*?)".*?>(?P<title>.*?)</a>.*?'
            r'<a class="result__snippet".*?>(?P<snippet>.*?)</a>',
            html,
            flags=re.S,
        )
        hits: list[dict] = []
        for url, title, snippet in rows[:5]:
            clean_url = self._clean_result_url(unescape(re.sub(r"<.*?>", "", url)))
            hits.append(self._normalized_web_hit(self._clean_html(title), clean_url, self._clean_html(snippet), query))
        return hits

    def _normalized_web_hit(
        self,
        title: str,
        url: str,
        snippet: str,
        query: str,
        provenance: str = "duckduckgo_html_web_search",
    ) -> dict:
        parsed = urlparse(url)
        is_pdf = parsed.scheme == "https" and parsed.path.lower().endswith(".pdf")
        return {
            "title": title,
            "url": url,
            "source_page_url": url,
            "download_url": url if is_pdf else None,
            "snippet": snippet,
            "host": parsed.netloc,
            "file_type": "pdf" if is_pdf else "html",
            "is_pdf": is_pdf,
            "query": query,
            "provenance": provenance,
            "verification_status": "web_search_result_unverified",
            "license_status": "needs_review",
            "lifecycle_status": "download_candidate" if is_pdf else "requires_human_review",
            "grounding_metadata": {"provider": provenance, "query": query},
        }

    def _web_source_candidates(self, web_hits: list[dict], payload: RunCreate) -> list[dict]:
        if not payload.allow_source_download_suggestions:
            return []
        candidates: list[dict] = []
        for index, hit in enumerate(web_hits[: payload.max_source_candidates], start=1):
            url = hit["url"]
            is_pdf = bool(hit.get("is_pdf")) or ".pdf" in url.lower()
            candidates.append(
                {
                    "source_id": f"web-{index}-{sha1(url.encode()).hexdigest()[:10]}",
                    "work_id": "web-discovered-work",
                    "provider": "Web Search",
                    "url": url,
                    "source_page_url": hit.get("source_page_url", url),
                    "download_url": url if is_pdf else None,
                    "file_type": hit.get("file_type", "pdf" if is_pdf else "html"),
                    "download_policy": "admin_approval_required",
                    "ingestion_status": "web_discovered",
                    "lifecycle_status": "download_candidate" if is_pdf else "requires_human_review",
                    "relationship_reason": "Real web search result connected to the ambiguous phrase or containing-text context.",
                    "provenance": hit.get("provenance", "web_search"),
                    "verification_status": "metadata_only",
                    "license_status": "needs_review",
                    "grounding_metadata": hit.get("grounding_metadata", {}),
                    "title": hit.get("title"),
                    "snippet": hit.get("snippet"),
                    "host": hit.get("host"),
                }
            )
        return candidates

    def _external_candidate_works(self, web_hits: list[dict]) -> list[dict]:
        return [
            {
                "title": hit.get("title"),
                "url": hit.get("url"),
                "snippet": hit.get("snippet"),
                "reason": "External web result; must be downloaded/indexed before it can count as textual evidence.",
            }
            for hit in web_hits[:5]
        ]

    def _clean_html(self, value: str) -> str:
        cleaned = re.sub(r"<.*?>", "", value)
        return unescape(cleaned).strip()

    def _clean_result_url(self, url: str) -> str:
        if url.startswith("//"):
            url = f"https:{url}"
        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc:
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                return unquote(target)
        return url
