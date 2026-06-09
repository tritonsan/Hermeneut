from __future__ import annotations

from hashlib import sha1
from urllib.parse import quote, quote_plus

import httpx

from app.data.seed import AUTHORS, WORKS
from app.models import DetectedContext, Hypothesis, RunCreate
from app.services.normalization import normalize_arabic
from app.services.scholarly_protocol import ScholarlyProtocol
from app.settings import Settings


class BibliographicReasoningService:
    """Builds auditable pre-OCR candidate intelligence for Open Discovery."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.protocol = ScholarlyProtocol()

    def reason(
        self,
        payload: RunCreate,
        context: DetectedContext,
        hypotheses: list[Hypothesis],
        web_hits: list[dict],
        grounding_mode: str,
        academic_intelligence: dict | None = None,
    ) -> dict:
        academic_intelligence = academic_intelligence or {}
        context_profile = self._context_profile(payload, context, academic_intelligence)
        phrase_variants = self._phrase_variants(payload, context, academic_intelligence)
        author_candidates = self._author_candidates(payload, context, hypotheses, phrase_variants, web_hits, academic_intelligence)
        work_candidates = self._work_candidates(author_candidates, payload, context, web_hits, academic_intelligence)
        relationships = self._relationship_graph(payload, author_candidates, work_candidates, academic_intelligence)
        candidate_web_searches = self._candidate_web_searches(author_candidates, work_candidates, phrase_variants, web_hits, academic_intelligence)
        resolved_sources = self._resolve_open_source_candidates(work_candidates, phrase_variants, payload, candidate_web_searches)
        self._active_selected_sources = academic_intelligence.get("selected_sources", [])
        source_candidates = self._source_candidates(work_candidates, candidate_web_searches, payload, resolved_sources)
        self._active_selected_sources = []
        top_pdf_targets = self._top_pdf_targets(source_candidates, payload)
        rejected_candidates = self._rejected_candidates(author_candidates, work_candidates, source_candidates, payload)

        return {
            "context_profile": context_profile,
            "relationship_graph": relationships,
            "author_candidates": author_candidates,
            "work_candidates": work_candidates,
            "phrase_variants": phrase_variants,
            "candidate_web_searches": candidate_web_searches,
            "resolved_open_sources": resolved_sources,
            "source_candidates": source_candidates,
            "top_pdf_targets": top_pdf_targets,
            "rejected_candidates": rejected_candidates,
            "model_routing": {
                "context_domain_analysis": f"{self.settings.gemini_research_model} academic_discovery_chain",
                "relationship_reasoning": f"{self.settings.gemini_research_model} academic_discovery_chain",
                "candidate_expansion": f"{self.settings.gemini_research_model} academic_discovery_chain",
                "phrase_variant_generation": f"{self.settings.gemini_research_model} academic_discovery_chain",
                "candidate_web_research": f"{self.settings.gemini_research_model} with Google Search grounding + backend resolvers",
                "source_candidate_selection": "model_assisted_candidate_intelligence_plus_deterministic_policy",
                "pdf_download_and_vault": "no_ai_backend_policy",
                "ocr_text_extraction": self.settings.ocr_engine,
                "ocr_cleanup_normalization": "deterministic_arabic_normalization",
                "semantic_indexing": self.settings.gemini_embedding_model,
                "final_scholarly_report": self.settings.gemini_report_model,
            },
            "grounding_mode": grounding_mode,
        }

    def _context_profile(self, payload: RunCreate, context: DetectedContext, academic_intelligence: dict | None = None) -> dict:
        academic_intelligence = academic_intelligence or {}
        normalized = normalize_arabic(" ".join([payload.passage, payload.context or ""]))
        containing_normalized = self._containing_text(payload)
        classification = self.protocol.classify_containing_text(payload)
        academic_profile = academic_intelligence.get("context_profile", {})
        tradition = "classical textual tradition"
        if academic_profile.get("discipline"):
            tradition = str(academic_profile["discipline"])
        if any(term in normalized for term in ["ممكن", "واجب", "الامكان", "بالامكان", "صدق", "زيد موجود"]):
            tradition = "Arabic logic / Avicennan metaphysics"
        if classification.get("tradition_label"):
            tradition = str(classification["tradition_label"])
        if any(term in normalized for term in ["العالم", "قديم", "الاول", "الفلاسفه"]):
            tradition = "falsafa-kalam polemic"
        if "المعتزله" in normalized:
            tradition = "kalam doxography"
        return {
            "language": context.language,
            "domain": payload.domain_hint or context.domain,
            "period_hint": payload.period_hint or context.period_hint,
            "citation_type": context.citation_type,
            "technical_terms": context.key_terms,
            "tradition": tradition,
            "containing_text_classification": classification,
            "academic_model_review": academic_profile,
            "academic_discovery_model_assisted": academic_intelligence.get("model_assisted", False),
            "academic_discovery_prompt_profile": academic_intelligence.get("prompt_profile"),
            "academic_subagents": academic_intelligence.get("subagents", []),
            "candidate_dossiers": academic_intelligence.get("candidate_dossiers", []),
            "source_selection_judge": academic_intelligence.get("source_selection", {}),
            "decision_calibration": academic_intelligence.get("decision_calibration", {}),
            "decision_tier": academic_intelligence.get("decision_tier", "weak_lead"),
            "relationship_questions": academic_intelligence.get("relationship_questions", []),
            "relationship_hypotheses": academic_intelligence.get("relationship_hypotheses", []),
            "research_protocol": classification["research_protocol"],
            "containing_author": payload.containing_author,
            "containing_work": payload.containing_work,
            "commentary_chain_detected": classification.get("tradition_id") if containing_normalized else None,
            "interpretation_policy": (
                "containing_author/work describe the text where the user found the ambiguous phrase; "
                "they are first-class containing-layer source targets and are also used to infer citation networks."
            ),
            "model_used": self.settings.gemini_research_model,
        }

    def _phrase_variants(self, payload: RunCreate, context: DetectedContext, academic_intelligence: dict | None = None) -> list[dict]:
        exact = payload.passage.strip()
        normalized = normalize_arabic(exact)
        variants = [
            {"kind": "exact_arabic", "query": exact, "purpose": "Direct phrase search."},
            {"kind": "normalized_arabic", "query": normalized, "purpose": "Search without hamza/alif/ya variants."},
        ]
        if context.key_terms:
            variants.append(
                {
                    "kind": "technical_terms",
                    "query": " ".join(context.key_terms),
                    "purpose": "Search by extracted concept terms.",
                }
            )
        if any(term in normalized for term in ["الامكان", "ممكن", "زيد موجود", "صدق زيد"]):
            variants.extend(
                [
                    {
                        "kind": "semantic_arabic",
                        "query": "زيد موجود بالامكان الخاص القضية الممكنة المنطق",
                        "purpose": "Modal-logic paraphrase around special possibility.",
                    },
                    {
                        "kind": "metadata_english",
                        "query": "Arabic logic special possibility Zayd exists modal proposition",
                        "purpose": "English/transliteration metadata search.",
                    },
                ]
            )
        if any(term in normalized for term in ["العالم", "قديم"]):
            variants.extend(
                [
                    {
                        "kind": "semantic_arabic",
                        "query": "قدم العالم لا اول لوجوده الفلاسفة",
                        "purpose": "Conceptual paraphrase for eternity of the world.",
                    },
                    {
                        "kind": "metadata_english",
                        "query": "eternity of the world falasifa no beginning Arabic source",
                        "purpose": "English metadata search.",
                    },
                ]
            )
        for variant in (academic_intelligence or {}).get("phrase_variants", []):
            if isinstance(variant, dict) and variant.get("query"):
                variants.append(
                    {
                        "kind": variant.get("kind", "model_generated"),
                        "query": str(variant["query"]),
                        "purpose": variant.get("purpose", "Generated by the academic discovery sub-agent."),
                    }
                )
        deduped = {normalize_arabic(item["query"]): item for item in variants if item.get("query")}
        return list(deduped.values())[:12]

    def _author_candidates(
        self,
        payload: RunCreate,
        context: DetectedContext,
        hypotheses: list[Hypothesis],
        phrase_variants: list[dict],
        web_hits: list[dict],
        academic_intelligence: dict | None = None,
    ) -> list[dict]:
        normalized = normalize_arabic(" ".join([payload.passage, payload.context or ""]))
        candidates = {author["author_id"]: self._seed_author_candidate(author) for author in AUTHORS}

        for hypothesis in hypotheses:
            matched = False
            for author in AUTHORS:
                haystack = normalize_arabic(" ".join([author["name"], author["name_ar"], *author["aliases"]]))
                if normalize_arabic(hypothesis.author) and normalize_arabic(hypothesis.author) in haystack:
                    candidates[author["author_id"]]["hypothesis_fit"] = 0.9
                    candidates[author["author_id"]]["relationship_reason"] = hypothesis.reason
                    matched = True
            if not matched and "Unknown" not in hypothesis.author:
                author_id = f"external-{sha1(hypothesis.author.encode()).hexdigest()[:10]}"
                candidates[author_id] = {
                    "author_id": author_id,
                    "name": hypothesis.author,
                    "name_ar": "",
                    "aliases": [],
                    "tradition": "model_suggested",
                    "period": context.period_hint,
                    "death_year": None,
                    "hypothesis_fit": 0.65,
                    "relationship_reason": hypothesis.reason,
                    "metadata_status": "requires_backend_verification",
                }

        for academic_author in (academic_intelligence or {}).get("author_candidates", []):
            if not isinstance(academic_author, dict) or not academic_author.get("name"):
                continue
            author_id = self._academic_author_id(academic_author)
            candidate = {
                "author_id": author_id,
                "name": academic_author.get("name", author_id),
                "name_ar": academic_author.get("name_ar", ""),
                "aliases": [],
                "tradition": academic_author.get("source_role", "model_academic_candidate"),
                "period": academic_author.get("period") or context.period_hint,
                "death_year": None,
                "source_role": academic_author.get("source_role", "model_academic_candidate"),
                "hypothesis_fit": 0.72,
                "relationship_reason": academic_author.get("reason", "Academic discovery sub-agent proposed this candidate."),
                "metadata_status": "model_academic_candidate_requires_verification",
                "model_uncertainty": academic_author.get("uncertainty"),
                "academic_scores": {
                    "chronology_fit": academic_author.get("chronology_fit"),
                    "domain_fit": academic_author.get("domain_fit"),
                    "relationship_fit": academic_author.get("relationship_fit"),
                },
            }
            existing = candidates.get(author_id)
            if existing:
                existing.update({key: value for key, value in candidate.items() if value not in (None, "", [])})
            else:
                candidates[author_id] = candidate

        extra = self._logic_author_templates() if any(
            term in normalized for term in ["الامكان", "ممكن", "زيد موجود", "صدق زيد", "القضيه"]
        ) or self._is_shamsiyya_commentary_context(payload) else []
        extra += self.protocol.author_templates(payload)
        extra += self._falsafa_kalam_templates() if any(
            term in normalized for term in ["العالم", "قديم", "الفلاسفه", "الاول"]
        ) else []
        for author in extra:
            existing = candidates.get(author["author_id"])
            if existing:
                if author.get("source_role"):
                    existing["source_role"] = author["source_role"]
                    existing["relationship_reason"] = author.get("relationship_reason", existing["relationship_reason"])
                    existing["hypothesis_fit"] = max(existing.get("hypothesis_fit", 0.0), author.get("hypothesis_fit", 0.0))
                    existing["tradition"] = author.get("tradition", existing.get("tradition"))
                    existing["metadata_status"] = author.get("metadata_status", existing.get("metadata_status"))
            else:
                candidates[author["author_id"]] = author

        for item in candidates.values():
            item["domain_fit"] = self._domain_fit(item, context, normalized)
            item["chronology_fit"] = self._chronology_fit(item, payload)
            item["containing_relation_fit"] = self._containing_relation_fit(item, payload)
            if item.get("academic_scores"):
                scores = item["academic_scores"]
                item["domain_fit"] = max(item["domain_fit"], self._numeric_score(scores.get("domain_fit"), item["domain_fit"]))
                item["chronology_fit"] = max(item["chronology_fit"], self._numeric_score(scores.get("chronology_fit"), item["chronology_fit"]))
                item["containing_relation_fit"] = max(
                    item["containing_relation_fit"],
                    self._numeric_score(scores.get("relationship_fit"), item["containing_relation_fit"]),
                )
            item["phrase_variant_hit_strength"] = self._web_hit_strength(item, phrase_variants, web_hits)
            item["metadata_reliability"] = 0.75 if item.get("metadata_status") != "requires_backend_verification" else 0.35
            item["source_availability"] = self._source_availability_hint(item)
            item["score"] = round(
                0.2 * item["domain_fit"]
                + 0.1 * item["chronology_fit"]
                + 0.25 * item["containing_relation_fit"]
                + 0.25 * item["phrase_variant_hit_strength"]
                + 0.05 * item["metadata_reliability"]
                + 0.15 * item["source_availability"],
                3,
            )
            item["score_breakdown"] = {
                "domain_fit": item["domain_fit"],
                "chronology_fit": item["chronology_fit"],
                "containing_relation_fit": item["containing_relation_fit"],
                "phrase_variant_hit_strength": item["phrase_variant_hit_strength"],
                "metadata_reliability": item["metadata_reliability"],
                "source_availability": item["source_availability"],
            }
            item["model_used"] = self.settings.gemini_research_model
            item["selection_policy"] = "pre-OCR candidate only; not final textual evidence"

        return sorted(candidates.values(), key=lambda value: value["score"], reverse=True)[:20]

    def _work_candidates(
        self,
        author_candidates: list[dict],
        payload: RunCreate,
        context: DetectedContext,
        web_hits: list[dict],
        academic_intelligence: dict | None = None,
    ) -> list[dict]:
        author_scores = {item["author_id"]: item["score"] for item in author_candidates}
        candidates: list[dict] = []
        for work in WORKS:
            if work["author_id"] in author_scores:
                candidates.append(self._work_candidate(work, author_scores[work["author_id"]], "curated_seed_metadata"))

        normalized = normalize_arabic(payload.passage)
        if any(term in normalized for term in ["الامكان", "ممكن", "زيد موجود", "صدق زيد"]):
            candidates.extend(
                [
                    self._external_work_candidate("katibi-shamsiyya", "al-Risala al-Shamsiyya", "الرسالة الشمسية", "najm-al-din-al-katibi", 0.82),
                    self._external_work_candidate(
                        "qutb-razi-tahrir-shamsiyya",
                        "Tahrir al-qawaid al-mantiqiyya fi sharh al-Risala al-Shamsiyya",
                        "تحرير القواعد المنطقية في شرح الرسالة الشمسية",
                        "qutb-al-din-al-razi",
                        0.88,
                    ),
                    self._external_work_candidate("tusi-tajrid", "Tajrid al-i'tiqad", "تجريد الاعتقاد", "nasir-al-din-al-tusi", 0.74),
                    self._external_work_candidate("razi-sharh-isharat", "Sharh al-Isharat", "شرح الإشارات", "fakhr-al-din-al-razi", 0.68),
                    self._external_work_candidate("jurjani-sharh-mawaqif", "Sharh al-Mawaqif", "شرح المواقف", "al-jurjani", 0.62),
                ]
            )
        for work in self.protocol.work_templates(payload):
            candidates.append(
                self._external_work_candidate(
                    work["work_id"],
                    work["title"],
                    work["title_ar"],
                    work["author_id"],
                    float(work["score"]),
                    relationship_reason=work.get("relationship_reason"),
                    source_role=work.get("source_role"),
                )
            )
        containing_work = self._containing_layer_work_candidate(payload)
        if containing_work:
            candidates.insert(0, containing_work)
        for academic_work in (academic_intelligence or {}).get("work_candidates", []):
            if not isinstance(academic_work, dict) or not academic_work.get("title"):
                continue
            author_id = self._match_academic_work_author(str(academic_work.get("author_name", "")), author_candidates)
            priority = self._numeric_score(academic_work.get("priority"), 0.62)
            candidates.append(
                self._external_work_candidate(
                    f"academic-{sha1(str(academic_work.get('title')).encode()).hexdigest()[:10]}",
                    str(academic_work.get("title")),
                    str(academic_work.get("title_ar") or ""),
                    author_id,
                    min(0.92, max(0.45, priority)),
                    relationship_reason=academic_work.get("reason", "Academic discovery sub-agent proposed this work."),
                    source_role=academic_work.get("source_role", "model_academic_work_candidate"),
                )
                | {
                    "model_search_queries": academic_work.get("search_queries", []),
                    "metadata_status": "model_academic_candidate_requires_verification",
                }
            )
        for index, hit in enumerate(web_hits[:4]):
            title = hit.get("title") or hit.get("url") or f"web result {index + 1}"
            candidates.append(
                {
                    "work_id": f"web-work-{sha1(title.encode()).hexdigest()[:10]}",
                    "title": title,
                    "title_ar": "",
                    "author_id": "web-discovered-author",
                    "domain": context.domain,
                    "language": "unknown",
                    "score": 0.42,
                    "score_breakdown": {"source_availability": 0.5, "metadata_reliability": 0.35},
                    "relationship_reason": "Web result mentions the phrase or generated variant; requires metadata verification.",
                    "metadata_status": "web_result_unverified",
                    "source_url": hit.get("url"),
                }
            )
        deduped = {item["work_id"]: item for item in candidates}
        return sorted(deduped.values(), key=lambda value: value.get("score", 0), reverse=True)[:20]

    def _containing_layer_work_candidate(self, payload: RunCreate) -> dict | None:
        if not (payload.containing_work or payload.containing_author):
            return None
        label = str(payload.containing_work or payload.containing_author or "").strip()
        author = str(payload.containing_author or "").strip()
        if not label:
            return None
        author_id = self._slug_id("containing-author", author or label)
        return {
            "work_id": self._slug_id("containing-work", f"{author} {label}"),
            "title": label,
            "title_ar": label if any("\u0600" <= char <= "\u06ff" for char in label) else "",
            "author_id": author_id,
            "author_name": author or None,
            "domain": "classical commentary layer",
            "language": "ar",
            "score": 0.94,
            "score_breakdown": {"user_supplied_containing_layer": 1.0, "source_availability": 0.66},
            "relationship_reason": (
                "User supplied this containing work/author; Open Discovery must search this layer for where the phrase may be found."
            ),
            "source_role": "containing_layer",
            "source_role_group": "where_phrase_may_be_found",
            "metadata_status": "user_supplied_containing_layer_requires_source_resolution",
        }

    def _relationship_graph(
        self,
        payload: RunCreate,
        author_candidates: list[dict],
        work_candidates: list[dict],
        academic_intelligence: dict | None = None,
    ) -> list[dict]:
        edges: list[dict] = []
        for work in work_candidates:
            edges.append(
                {
                    "from_type": "author",
                    "from_id": work.get("author_id"),
                    "to_type": "work",
                    "to_id": work.get("work_id"),
                    "relation": "candidate_author_work",
                    "confidence": min(0.9, max(0.35, float(work.get("score", 0.4)))),
                    "provenance": work.get("metadata_status", "candidate_reasoning"),
                    "reason": work.get("relationship_reason", "Candidate work attached to scored author."),
                }
            )
        if payload.containing_author or payload.containing_work:
            for author in author_candidates[:8]:
                edges.append(
                    {
                        "from_type": "containing_text",
                        "from_id": payload.containing_work or payload.containing_author,
                        "to_type": "author",
                        "to_id": author["author_id"],
                        "relation": "possible_citation_or_polemical_target",
                        "confidence": author.get("containing_relation_fit", 0.4),
                        "provenance": "relationship_reasoning",
                        "reason": author.get("relationship_reason", "Candidate inferred from containing-text context."),
                    }
                )
        for question in (academic_intelligence or {}).get("relationship_questions", [])[:8]:
            if isinstance(question, dict):
                edges.append(
                    {
                        "from_type": "research_question",
                        "from_id": payload.containing_work or payload.containing_author or "input_passage",
                        "to_type": "candidate_field",
                        "to_id": question.get("question", "relationship_question"),
                        "relation": "model_generated_relationship_question",
                        "confidence": 0.7 if question.get("search_priority") == "high" else 0.55,
                        "provenance": "gemini_academic_discovery_chain",
                        "reason": question.get("why_it_matters", "Model-generated scholarly relationship question."),
                    }
                )
        return edges[:30]

    def _candidate_web_searches(
        self,
        author_candidates: list[dict],
        work_candidates: list[dict],
        phrase_variants: list[dict],
        web_hits: list[dict],
        academic_intelligence: dict | None = None,
    ) -> list[dict]:
        searches: list[dict] = []
        for item in (academic_intelligence or {}).get("candidate_search_plan", [])[:12]:
            if isinstance(item, dict) and item.get("query"):
                searches.append(
                    {
                        "query": item["query"],
                        "candidate_author": item.get("candidate"),
                        "candidate_work": item.get("candidate"),
                        "provider": f"{self.settings.gemini_research_model} academic_discovery_chain",
                        "target": item.get("target", "web"),
                        "expected_signal": item.get("expected_signal"),
                        "hits": self._related_hits(str(item["query"]), str(item.get("candidate", "")), web_hits),
                        "hit_count": 0,
                        "resolver_results": [],
                        "resolver_result_count": 0,
                        "executed_targets": [],
                        "execution_status": "planned",
                        "decision": "model_planned_candidate_specific_search",
                    }
                )
        top_authors = author_candidates[:6]
        top_works = work_candidates[:6]
        for author in top_authors:
            for variant in phrase_variants[:3]:
                query = f"{variant['query']} {author['name']} PDF"
                related_hits = self._related_hits(query, author["name"], web_hits)
                searches.append(
                    {
                        "query": query,
                        "candidate_author_id": author["author_id"],
                        "candidate_author": author["name"],
                        "source_role": "citation_chain",
                        "variant_kind": variant["kind"],
                        "provider": "Gemini Google Search grounding" if web_hits else "planned_backend_resolver",
                        "hits": related_hits,
                        "hit_count": len(related_hits),
                        "resolver_results": [],
                        "resolver_result_count": 0,
                        "executed_targets": [],
                        "execution_status": "planned",
                        "decision": "supports_candidate" if related_hits else "no_direct_hit_yet",
                    }
                )
        for work in top_works:
            query = f"{work['title']} {phrase_variants[0]['query']} archive.org OpenITI"
            related_hits = self._related_hits(query, str(work["title"]), web_hits)
            searches.append(
                {
                    "query": query,
                    "candidate_work_id": work["work_id"],
                    "candidate_work": work["title"],
                    "source_role": work.get("source_role") if work.get("source_role") in {"containing_layer", "citation_chain", "parallel_witness"} else "citation_chain",
                    "provider": "Gemini Google Search grounding" if web_hits else "planned_backend_resolver",
                    "hits": related_hits,
                    "hit_count": len(related_hits),
                    "resolver_results": [],
                    "resolver_result_count": 0,
                    "executed_targets": [],
                    "execution_status": "planned",
                    "decision": "supports_work_candidate" if related_hits else "metadata_search_required",
                }
            )
        for search in searches:
            if search.get("hits"):
                search["hit_count"] = len(search["hits"])
        return searches[:36]

    def _source_candidates(
        self,
        work_candidates: list[dict],
        candidate_web_searches: list[dict],
        payload: RunCreate,
        resolved_sources: list[dict],
    ) -> list[dict]:
        candidates: list[dict] = []
        candidates.extend(resolved_sources)
        for work in work_candidates[:8]:
            source_url = work.get("source_url")
            if source_url:
                candidates.append(self._web_source_candidate(work, source_url))
            else:
                candidates.append(self._metadata_source_candidate(work))
        for search in candidate_web_searches:
            for hit in search.get("hits", []):
                candidates.append(
                    {
                        "source_id": f"web-{sha1(str(hit.get('url')).encode()).hexdigest()[:10]}",
                        "work_id": search.get("candidate_work_id") or "web-discovered-work",
                        "provider": "Candidate Web Search",
                        "title": hit.get("title") or search.get("candidate_work") or search.get("candidate_author"),
                        "url": hit.get("url"),
                        "source_page_url": hit.get("source_page_url") or hit.get("url"),
                        "download_url": hit.get("download_url"),
                        "file_type": hit.get("file_type", "html"),
                        "download_policy": "admin_approval_required",
                        "ingestion_status": "web_discovered",
                        "lifecycle_status": "download_candidate" if hit.get("download_url") else "requires_human_review",
                        "relationship_reason": "Candidate-specific phrase/work/author search produced this source lead.",
                        "provenance": hit.get("provenance", "candidate_web_search"),
                        "verification_status": "metadata_only",
                        "license_status": hit.get("license_status", "needs_review"),
                        "grounding_metadata": {"query": search.get("query"), "decision": search.get("decision")},
                        "candidate_score": 0.58 if hit.get("download_url") else 0.4,
                        "source_role": search.get("source_role") or "citation_chain",
                        "source_role_group": self._source_role_group(str(search.get("source_role") or "citation_chain")),
                        "resolution_queries": [str(search.get("query"))] if search.get("query") else [],
                        "source_resolution_query": search.get("query"),
                    }
                )
        for selected in self._selected_source_candidates(work_candidates, payload):
            candidates.append(selected)
        deduped = {item["source_id"]: item for item in candidates}
        candidate_limit = max(payload.max_source_candidates, payload.max_pdf_downloads * 3, 12)
        return sorted(
            deduped.values(),
            key=lambda item: (item.get("relevance_score", item.get("candidate_score", 0)), item.get("candidate_score", 0)),
            reverse=True,
        )[:candidate_limit]

    def _selected_source_candidates(self, work_candidates: list[dict], payload: RunCreate) -> list[dict]:
        candidates: list[dict] = []
        work_by_title = {
            normalize_arabic(" ".join([str(work.get("title", "")), str(work.get("title_ar", ""))])): work
            for work in work_candidates
        }
        # Stored on the instance for this reasoning pass by _source_candidates caller.
        selected_sources = getattr(self, "_active_selected_sources", [])
        for index, selected in enumerate(selected_sources[: payload.max_source_candidates], start=1):
            if not isinstance(selected, dict):
                continue
            label = str(selected.get("work_or_source") or selected.get("candidate") or f"model-selected-source-{index}")
            label_norm = normalize_arabic(label)
            matched_work = next((work for key, work in work_by_title.items() if label_norm and (label_norm in key or key in label_norm)), None)
            query = quote_plus(f"{label} PDF archive.org OpenITI")
            candidates.append(
                {
                    "source_id": f"model-source-{sha1(label.encode()).hexdigest()[:10]}",
                    "work_id": matched_work.get("work_id") if matched_work else "model-selected-work",
                    "provider": "Gemini Source Selection Judge",
                    "title": label,
                    "url": f"https://www.google.com/search?q={query}",
                    "source_page_url": f"https://www.google.com/search?q={query}",
                    "download_url": None,
                    "file_type": "metadata_search",
                    "download_policy": "human_review_required",
                    "ingestion_status": "metadata_only",
                    "lifecycle_status": "requires_human_review",
                    "relationship_reason": selected.get("selection_reason", "Selected by the source selection sub-agent."),
                    "provenance": "gemini_source_selection_judge",
                    "verification_status": "metadata_only",
                    "license_status": "needs_review",
                    "grounding_metadata": {
                        "candidate": selected.get("candidate"),
                        "required_verification": selected.get("required_verification"),
                    },
                    "candidate_score": self._numeric_score(selected.get("download_priority"), 0.62),
                    "relevance_score": self._numeric_score(selected.get("download_priority"), 0.62),
                    "relevance_breakdown": {
                        "model_selected": True,
                        "counts_as_evidence_now": selected.get("counts_as_evidence_now", False),
                    },
                }
            )
        return candidates

    def _resolve_open_source_candidates(
        self,
        work_candidates: list[dict],
        phrase_variants: list[dict],
        payload: RunCreate,
        candidate_web_searches: list[dict] | None = None,
    ) -> list[dict]:
        if not payload.allow_source_download_suggestions:
            return []
        resolved: list[dict] = []
        seen: set[str] = set()
        download_limit = max(payload.max_pdf_downloads or 0, payload.max_containing_source_downloads + payload.max_citation_source_downloads, 3)
        with httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": "HermeneutHackathon/0.3"}) as client:
            for item in self._candidate_specific_resolver_candidates(client, candidate_web_searches or [], work_candidates):
                source_id = item["source_id"]
                if source_id in seen:
                    continue
                seen.add(source_id)
                resolved.append(item)
                if len([source for source in resolved if source.get("download_url")]) >= download_limit:
                    return resolved
            for work in work_candidates[:8]:
                for query in self._source_resolution_queries(work, phrase_variants)[:5]:
                    for item in self._internet_archive_candidates(client, query, work):
                        self._apply_role_metadata(item, work, query, self._role_for_search({"query": query}, work))
                        source_id = item["source_id"]
                        if source_id in seen:
                            continue
                        seen.add(source_id)
                        resolved.append(item)
                        if len([source for source in resolved if source.get("download_url")]) >= download_limit:
                            return resolved
        return resolved

    def _candidate_specific_resolver_candidates(
        self,
        client: httpx.Client,
        candidate_web_searches: list[dict],
        work_candidates: list[dict],
    ) -> list[dict]:
        """Execute model-planned candidate queries against concrete backend resolvers."""
        work_lookup = self._work_lookup(work_candidates)
        candidates: list[dict] = []
        for search in candidate_web_searches[:18]:
            query = str(search.get("query", "")).strip()
            if not query:
                search["execution_status"] = "skipped_empty_query"
                continue
            if not self._should_execute_candidate_search(search):
                search["execution_status"] = "planned_not_executed_no_target"
                search["executed_targets"] = []
                search["resolver_results"] = []
                search["resolver_result_count"] = 0
                continue
            targets = self._resolver_targets(search)
            search["executed_targets"] = targets
            resolver_results: list[dict] = []
            work = self._work_for_search(search, work_candidates, work_lookup)
            role = self._role_for_search(search, work)
            if "internet_archive" in targets:
                for item in self._internet_archive_candidates(client, query, work):
                    item["provenance"] = "candidate_specific_internet_archive_resolver"
                    self._apply_role_metadata(item, work, query, role)
                    item["relationship_reason"] = (
                        f"Candidate-specific search query '{query}' found this Internet Archive source for "
                        f"{search.get('candidate_work') or search.get('candidate_author') or work.get('title')}."
                    )
                    item.setdefault("grounding_metadata", {})
                    item["grounding_metadata"].update(
                        {
                            "candidate_search_query": query,
                            "candidate_search_target": search.get("target"),
                            "expected_signal": search.get("expected_signal"),
                        }
                    )
                    candidates.append(item)
                    resolver_results.append(self._resolver_result_summary(item))
            if "wikidata" in targets:
                for item in self._wikidata_candidates(client, query, search, work):
                    self._apply_role_metadata(item, work, query, role)
                    candidates.append(item)
                    resolver_results.append(self._resolver_result_summary(item))
            if "openiti" in targets:
                item = self._openiti_metadata_candidate(query, search, work)
                self._apply_role_metadata(item, work, query, role)
                candidates.append(item)
                resolver_results.append(self._resolver_result_summary(item))
            if "web_metadata" in targets:
                item = self._web_metadata_search_candidate(query, search, work)
                self._apply_role_metadata(item, work, query, role)
                candidates.append(item)
                resolver_results.append(self._resolver_result_summary(item))
            search["resolver_results"] = resolver_results[:8]
            search["resolver_result_count"] = len(resolver_results)
            search["execution_status"] = "executed" if targets else "no_backend_target"
            if resolver_results and not search.get("hits"):
                search["decision"] = "backend_resolver_found_source_leads"
            elif not resolver_results and search.get("decision") in {"model_planned_candidate_specific_search", "metadata_search_required"}:
                search["decision"] = "backend_resolver_no_source_lead"
        return candidates

    def _should_execute_candidate_search(self, search: dict) -> bool:
        provider = str(search.get("provider", ""))
        return bool(search.get("target") or search.get("expected_signal") or "academic_discovery_chain" in provider)

    def _resolver_targets(self, search: dict) -> list[str]:
        target_text = " ".join(
            str(value or "")
            for value in [search.get("target"), search.get("query"), search.get("expected_signal")]
        ).lower()
        targets: list[str] = []
        if any(token in target_text for token in ["internet archive", "archive.org", "pdf", "ocr text", "download"]):
            targets.append("internet_archive")
        if "wikidata" in target_text:
            targets.append("wikidata")
        if "openiti" in target_text:
            targets.append("openiti")
        if any(token in target_text for token in ["google", "web", "catalog", "library_catalog"]):
            targets.append("web_metadata")
        if not targets:
            targets.extend(["internet_archive", "wikidata", "openiti"])
        return list(dict.fromkeys(targets))[:4]

    def _work_lookup(self, work_candidates: list[dict]) -> dict[str, dict]:
        lookup: dict[str, dict] = {}
        for work in work_candidates:
            for key in [work.get("work_id"), work.get("title"), work.get("title_ar")]:
                if key:
                    lookup[normalize_arabic(str(key))] = work
        return lookup

    def _work_for_search(self, search: dict, work_candidates: list[dict], work_lookup: dict[str, dict]) -> dict:
        for key in [search.get("candidate_work_id"), search.get("candidate_work"), search.get("candidate")]:
            normalized = normalize_arabic(str(key or ""))
            if normalized and normalized in work_lookup:
                return work_lookup[normalized]
        label = normalize_arabic(" ".join(str(search.get(key, "")) for key in ["candidate_work", "candidate"]))
        for normalized, work in work_lookup.items():
            if label and (label in normalized or normalized in label):
                return work
        if work_candidates:
            return work_candidates[0]
        title = str(search.get("candidate_work") or search.get("candidate") or "candidate work")
        return {
            "work_id": f"candidate-work-{sha1(title.encode()).hexdigest()[:10]}",
            "title": title,
            "title_ar": "",
            "author_id": "candidate-author",
            "score": 0.45,
            "relationship_reason": "Ad hoc work container for a model-planned candidate search.",
        }

    def _role_for_search(self, search: dict, work: dict) -> str:
        role = str(search.get("source_role") or work.get("source_role") or "")
        if role in {"containing_layer", "citation_chain", "parallel_witness"}:
            return role
        text = " ".join(str(search.get(key, "")) for key in ["query", "candidate_work", "candidate_author", "target"]).lower()
        if any(token in text for token in ["containing", "hashiya", "hāshiya", "gloss", "supercommentary"]):
            return "containing_layer"
        if any(token in text for token in ["base text", "citation", "source chain", "commentary chain"]):
            return "citation_chain"
        return "citation_chain"

    def _wikidata_candidates(self, client: httpx.Client, query: str, search: dict, work: dict) -> list[dict]:
        sparql = f"""
        SELECT ?item ?itemLabel WHERE {{
          ?item rdfs:label ?itemLabel.
          FILTER(LANG(?itemLabel) = "en" || LANG(?itemLabel) = "ar")
          FILTER(CONTAINS(LCASE(STR(?itemLabel)), LCASE("{self._sparql_literal(query[:80])}")))
        }}
        LIMIT 3
        """
        try:
            response = client.get(
                "https://query.wikidata.org/sparql",
                params={"query": sparql, "format": "json"},
                headers={"User-Agent": "HermeneutHackathon/0.3"},
            )
            response.raise_for_status()
            bindings = response.json().get("results", {}).get("bindings", [])
        except Exception:
            return []
        candidates: list[dict] = []
        for binding in bindings:
            item_url = binding.get("item", {}).get("value")
            label = binding.get("itemLabel", {}).get("value")
            if not item_url or not label:
                continue
            qid = item_url.rsplit("/", 1)[-1]
            candidates.append(
                {
                    "source_id": f"wikidata-{qid}",
                    "work_id": work.get("work_id", "wikidata-metadata"),
                    "provider": "Wikidata",
                    "title": label,
                    "url": item_url,
                    "source_page_url": item_url,
                    "download_url": None,
                    "file_type": "metadata",
                    "download_policy": "metadata_only",
                    "ingestion_status": "metadata_only",
                    "lifecycle_status": "requires_human_review",
                    "relationship_reason": "Candidate-specific Wikidata resolver returned an entity for graph enrichment.",
                    "provenance": "candidate_specific_wikidata_resolver",
                    "verification_status": "metadata_only",
                    "license_status": "metadata_only",
                    "grounding_metadata": {
                        "candidate_search_query": query,
                        "candidate_search_target": search.get("target"),
                        "wikidata_qid": qid,
                    },
                    "candidate_score": 0.52,
                    "relevance_score": 0.52,
                    "relevance_breakdown": {"metadata_entity": True, "direct_download": False},
                }
            )
        return candidates

    def _openiti_metadata_candidate(self, query: str, search: dict, work: dict) -> dict:
        url = f"https://github.com/OpenITI?tab=repositories&q={quote_plus(query)}"
        return {
            "source_id": f"openiti-search-{sha1(query.encode()).hexdigest()[:10]}",
            "work_id": work.get("work_id", "openiti-metadata-search"),
            "provider": "OpenITI metadata search",
            "title": f"OpenITI metadata search: {query[:80]}",
            "url": url,
            "source_page_url": url,
            "download_url": None,
            "file_type": "metadata_search",
            "download_policy": "human_review_required",
            "ingestion_status": "metadata_only",
            "lifecycle_status": "requires_human_review",
            "relationship_reason": "Candidate-specific OpenITI resolver created a metadata search lead; not textual evidence yet.",
            "provenance": "candidate_specific_openiti_resolver",
            "verification_status": "metadata_only",
            "license_status": "metadata_only",
            "grounding_metadata": {
                "candidate_search_query": query,
                "candidate_search_target": search.get("target"),
                "expected_signal": search.get("expected_signal"),
            },
            "candidate_score": 0.5,
            "relevance_score": 0.5,
            "relevance_breakdown": {"metadata_search": True, "direct_download": False},
        }

    def _web_metadata_search_candidate(self, query: str, search: dict, work: dict) -> dict:
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        return {
            "source_id": f"web-search-{sha1(query.encode()).hexdigest()[:10]}",
            "work_id": work.get("work_id", "web-metadata-search"),
            "provider": "Candidate web metadata search",
            "title": f"Web metadata search: {query[:80]}",
            "url": url,
            "source_page_url": url,
            "download_url": None,
            "file_type": "metadata_search",
            "download_policy": "human_review_required",
            "ingestion_status": "metadata_only",
            "lifecycle_status": "requires_human_review",
            "relationship_reason": "Candidate-specific search requires human review or a grounded web hit before download.",
            "provenance": "candidate_specific_web_metadata_resolver",
            "verification_status": "metadata_only",
            "license_status": "needs_review",
            "grounding_metadata": {
                "candidate_search_query": query,
                "candidate_search_target": search.get("target"),
                "expected_signal": search.get("expected_signal"),
            },
            "candidate_score": 0.42,
            "relevance_score": 0.42,
            "relevance_breakdown": {"metadata_search": True, "direct_download": False},
        }

    def _resolver_result_summary(self, item: dict) -> dict:
        return {
            "source_id": item.get("source_id"),
            "provider": item.get("provider"),
            "title": item.get("title"),
            "download_url": item.get("download_url"),
            "lifecycle_status": item.get("lifecycle_status"),
            "provenance": item.get("provenance"),
            "relevance_score": item.get("relevance_score", item.get("candidate_score")),
        }

    def _sparql_literal(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _source_resolution_queries(self, work: dict, phrase_variants: list[dict]) -> list[str]:
        title = str(work.get("title", ""))
        title_ar = str(work.get("title_ar", ""))
        author_id = str(work.get("author_id", "")).replace("-", " ")
        author_name = str(work.get("author_name") or "").strip()
        role = str(work.get("source_role") or "citation_chain")
        phrase_bits = [str(variant.get("query", "")) for variant in phrase_variants[:3] if variant.get("query")]
        commentary_terms = [
            "hashiya",
            "sharh",
            "taliqat",
            "ta'liqat",
            "ala",
            "commentary",
            "gloss",
            "حاشية",
            "شرح",
            "تعليقات",
            "على",
        ]
        queries = [
            f'{title} {author_id}',
            f'{title_ar} {author_id}',
            f'{title} {author_name}',
            f'{title_ar} {author_name}',
            f'{title} Arabic',
            f'{title_ar}',
        ]
        if role == "containing_layer":
            queries.extend(
                [
                    f"{title} {author_name} {' '.join(commentary_terms[:6])} PDF archive.org OpenITI",
                    f"{title_ar} {author_name} حاشية شرح على PDF archive.org",
                ]
            )
            for phrase in phrase_bits[:2]:
                queries.append(f"{title} {author_name} {phrase} archive.org OCR text")
        else:
            queries.extend(
                [
                    f"{title} {author_name} base text commentary PDF archive.org OpenITI",
                    f"{title_ar} {author_name} شرح متن PDF archive.org",
                ]
            )
        queries.extend(self._known_title_variants(str(work.get("work_id", ""))))
        for variant in phrase_variants[:2]:
            queries.append(f"{title} {variant.get('query')}")
        return [query.strip() for query in dict.fromkeys(queries) if query.strip()]

    def _known_title_variants(self, work_id: str) -> list[str]:
        variants = {
            "katibi-shamsiyya": [
                "katibi shamsiyya",
                "shamsiyya logic arabic",
                "الرسالة الشمسية الكاتبي",
                "Risala Shamsiyya Katibi",
            ],
            "tusi-tajrid": ["tusi tajrid", "tajrid al itiqad tusi", "تجريد الاعتقاد الطوسي"],
            "razi-sharh-isharat": ["razi sharh isharat", "شرح الاشارات الرازي"],
            "jurjani-sharh-mawaqif": ["jurjani sharh mawaqif", "شرح المواقف الجرجاني"],
            "qutb-razi-tahrir-shamsiyya": [
                "تحرير القواعد المنطقية في شرح الرسالة الشمسية",
                "قطب الدين الرازي تحرير القواعد المنطقية",
                "Qutb al-Din al-Razi Tahrir al-qawaid al-mantiqiyya",
                "شرح الرسالة الشمسية للرازي",
            ],
            "taftazani-sharh-shamsiyya": [
                "شرح الشمسية التفتازاني",
                "التفتازاني على الشمسية",
                "Taftazani Sharh Shamsiyya",
            ],
            "jurjani-hashiya-shamsiyya": [
                "حاشية الجرجاني على الشمسية",
                "Hashiya Jurjani Shamsiyya",
            ],
        }
        return [*variants.get(work_id, []), *self.protocol.title_variants(work_id)]

    def _internet_archive_candidates(self, client: httpx.Client, query: str, work: dict) -> list[dict]:
        docs = self._internet_archive_search(client, query)
        candidates: list[dict] = []
        for doc in docs[:3]:
            identifier = doc.get("identifier")
            if not identifier:
                continue
            expected_text = " ".join(
                str(value or "")
                for value in [
                    doc.get("title"),
                    work.get("title"),
                    work.get("author"),
                    query,
                ]
            )
            download_url, file_type, file_name, file_size = self._internet_archive_download_file(client, identifier, expected_text)
            item_url = f"https://archive.org/details/{identifier}"
            candidates.append(
                self._with_role_metadata(
                    {
                    "source_id": f"ia-{identifier}",
                    "work_id": work["work_id"],
                    "provider": "Internet Archive",
                    "title": doc.get("title") or work.get("title") or identifier,
                    "url": item_url,
                    "source_page_url": item_url,
                    "download_url": download_url,
                    "file_type": file_type,
                    "download_policy": "admin_approval_required",
                    "ingestion_status": "web_discovered",
                    "lifecycle_status": "download_candidate" if download_url else "requires_human_review",
                    "relationship_reason": (
                        f"Internet Archive resolver matched query '{query}' for candidate work {work.get('title')}."
                    ),
                    "provenance": "internet_archive_resolver",
                    "verification_status": "metadata_only",
                    "license_status": "needs_review",
                    "grounding_metadata": {
                        "query": query,
                        "identifier": identifier,
                        "file_name": file_name,
                        "file_size": file_size,
                    },
                        "candidate_score": min(0.92, float(work.get("score", 0.5)) + (0.16 if download_url else 0.02)),
                        "relevance_score": self._source_relevance(doc, work, query, bool(download_url)),
                        "relevance_breakdown": self._source_relevance_breakdown(doc, work, query, bool(download_url)),
                    },
                    work,
                    query,
                    self._role_for_search({"query": query}, work),
                )
            )
        return candidates

    def _internet_archive_search(self, client: httpx.Client, query: str) -> list[dict]:
        query_variants = [
            f'({query}) AND mediatype:texts',
            f'title:("{query}") AND mediatype:texts',
            f'creator:("{query}") AND mediatype:texts',
            f'description:("{query}") AND mediatype:texts',
        ]
        seen: set[str] = set()
        docs: list[dict] = []
        for q in query_variants:
            try:
                response = client.get(
                    "https://archive.org/advancedsearch.php",
                    params={"q": q, "fl[]": ["identifier", "title"], "rows": 5, "output": "json"},
                )
                response.raise_for_status()
                rows = response.json().get("response", {}).get("docs", [])
            except Exception:
                continue
            for row in rows:
                identifier = row.get("identifier")
                if identifier and identifier not in seen:
                    seen.add(identifier)
                    docs.append(row)
        return docs[:8]

    def _internet_archive_download_file(
        self,
        client: httpx.Client,
        identifier: str,
        expected_text: str = "",
    ) -> tuple[str | None, str, str | None, int | None]:
        try:
            response = client.get(f"https://archive.org/metadata/{identifier}")
            response.raise_for_status()
            files = response.json().get("files", [])
        except Exception:
            return None, "metadata", None, None
        downloadable = [
            file
            for file in files
            if self._is_downloadable_text_or_pdf(str(file.get("name", "")))
        ]
        preferred = sorted(
            [
                file
                for file in downloadable
                if self._file_matches_expected(str(file.get("name", "")), expected_text)
            ],
            key=self._ia_file_rank,
        )
        if not preferred:
            preferred = sorted(downloadable, key=self._ia_file_rank)
        if not preferred:
            return None, "metadata", None, None
        file_name = str(preferred[0].get("name"))
        file_type = "pdf" if file_name.lower().endswith(".pdf") else "text"
        file_size = self._file_size(preferred[0])
        return f"https://archive.org/download/{quote(identifier)}/{quote(file_name)}", file_type, file_name, file_size

    def _ia_file_rank(self, file: dict) -> tuple[int, int, int]:
        name = str(file.get("name", ""))
        lowered = name.lower()
        size = self._file_size(file) or 0
        max_bytes = self.settings.source_download_max_bytes
        if lowered.endswith(("_text.txt", "_djvu.txt")):
            kind_rank = 0
        elif lowered.endswith(".pdf") and (not size or size <= max_bytes):
            kind_rank = 1
        elif lowered.endswith(".pdf"):
            kind_rank = 2
        else:
            kind_rank = 3
        return (kind_rank, size or max_bytes + 1, len(name))

    def _file_size(self, file: dict) -> int | None:
        try:
            return int(file.get("size") or 0) or None
        except (TypeError, ValueError):
            return None

    def _is_downloadable_text_or_pdf(self, file_name: str) -> bool:
        lowered = file_name.lower()
        if lowered.endswith(".pdf"):
            return True
        if lowered.endswith(("_meta.txt", "_files.xml", "_meta.xml")):
            return False
        return lowered.endswith(("_djvu.txt", "_text.txt"))

    def _file_matches_expected(self, file_name: str, expected_text: str) -> bool:
        file_tokens = self._relevance_tokens(file_name)
        expected_tokens = self._relevance_tokens(expected_text)
        if not file_tokens or not expected_tokens:
            return True
        overlap = file_tokens & expected_tokens
        return len(overlap) >= 3

    def _relevance_tokens(self, text: str) -> set[str]:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
        return {token for token in normalized.split() if len(token) > 2}

    def _top_pdf_targets(self, source_candidates: list[dict], payload: RunCreate) -> list[dict]:
        targets = [
            {
                **source,
                "source_role": self._normalized_source_role(source),
                "source_role_group": self._source_role_group(self._normalized_source_role(source)),
                "selection_reason": (
                    "Direct PDF/text candidate selected for OCR queue."
                    if source.get("download_url")
                    else "Metadata lead retained for review; not automatically OCR-ready."
                ),
                "counts_as_evidence_before_ocr": False,
            }
            for source in source_candidates
            if source.get("download_url") and source.get("lifecycle_status") == "download_candidate"
        ]
        sorted_targets = sorted(
            targets,
            key=lambda item: (item.get("relevance_score", item.get("candidate_score", 0)), item.get("candidate_score", 0)),
            reverse=True,
        )
        containing_limit = max(0, payload.max_containing_source_downloads)
        citation_limit = max(0, payload.max_citation_source_downloads)
        total_limit = max(payload.max_pdf_downloads or 0, containing_limit + citation_limit, 1)
        selected: list[dict] = []
        missing_roles: list[str] = []
        for role, limit, bucket in [
            ("containing_layer", containing_limit, "where_phrase_may_be_found"),
            ("citation_chain", citation_limit, "possible_citation_source_chain"),
        ]:
            role_items = [item for item in sorted_targets if item["source_role"] == role]
            if not role_items:
                missing_roles.append(role)
                continue
            for rank, item in enumerate(role_items[:limit], start=1):
                selected.append(
                    item
                    | {
                        "selection_bucket": bucket,
                        "quota_reason": f"Selected under {role} quota {rank}/{limit}.",
                    }
                )
        if len(selected) < total_limit:
            selected_ids = {item["source_id"] for item in selected}
            for item in sorted_targets:
                if item["source_id"] in selected_ids:
                    continue
                selected.append(
                    item
                    | {
                        "selection_bucket": self._source_role_group(item["source_role"]),
                        "quota_reason": "Selected by transferred unused role quota.",
                        "source_role_missing_reason": ", ".join(missing_roles) if missing_roles else None,
                    }
                )
                if len(selected) >= total_limit:
                    break
        return [item | {"source_candidate_rank": index} for index, item in enumerate(selected[:total_limit], start=1)]

    def _normalized_source_role(self, source: dict) -> str:
        role = str(source.get("source_role") or source.get("source_role_group") or "")
        if role in {"containing_layer", "citation_chain", "parallel_witness"}:
            return role
        return "citation_chain"

    def _source_role_group(self, role: str) -> str:
        if role == "containing_layer":
            return "where_phrase_may_be_found"
        if role == "parallel_witness":
            return "parallel_witness"
        return "possible_citation_source_chain"

    def _with_role_metadata(self, item: dict, work: dict, query: str | None, role: str) -> dict:
        item["source_role"] = role
        item["source_role_group"] = self._source_role_group(role)
        item["resolution_queries"] = [query] if query else []
        item["source_resolution_query"] = query
        item["work_title"] = item.get("work_title") or work.get("title")
        item["author_id"] = item.get("author_id") or work.get("author_id")
        item["author_name"] = item.get("author_name") or work.get("author_name") or self._author_name_for_id(str(work.get("author_id") or ""))
        return item

    def _apply_role_metadata(self, item: dict, work: dict, query: str | None, role: str) -> None:
        item.update(self._with_role_metadata(item, work, query, role))

    def _slug_id(self, prefix: str, value: str) -> str:
        normalized = normalize_arabic(value).strip() or value.strip() or prefix
        return f"{prefix}-{sha1(normalized.encode()).hexdigest()[:10]}"

    def _author_name_for_id(self, author_id: str) -> str | None:
        for author in AUTHORS:
            if author["author_id"] == author_id:
                return author["name"]
        known = {
            "qutb-al-din-al-razi": "Qutb al-Din al-Razi",
            "najm-al-din-al-katibi": "Najm al-Din al-Katibi",
            "al-taftazani": "al-Taftazani",
            "al-jurjani": "al-Jurjani",
        }
        return known.get(author_id)

    def _rejected_candidates(
        self,
        author_candidates: list[dict],
        work_candidates: list[dict],
        source_candidates: list[dict],
        payload: RunCreate,
    ) -> list[dict]:
        rejected: list[dict] = []
        for author in author_candidates[8:]:
            rejected.append(
                {
                    "type": "author",
                    "id": author["author_id"],
                    "label": author["name"],
                    "score": author.get("score"),
                    "rejection_reason": "Below top candidate threshold for this Open Discovery pass.",
                }
            )
        for work in work_candidates[8:]:
            rejected.append(
                {
                    "type": "work",
                    "id": work["work_id"],
                    "label": work["title"],
                    "score": work.get("score"),
                    "rejection_reason": "Lower-ranked work candidate; not selected for top-3 PDF/OCR targeting.",
                }
            )
        selected_ids = {source["source_id"] for source in source_candidates[: payload.max_pdf_downloads]}
        for source in source_candidates:
            if source["source_id"] not in selected_ids and not source.get("download_url"):
                rejected.append(
                    {
                        "type": "source",
                        "id": source["source_id"],
                        "label": source.get("title"),
                        "score": source.get("candidate_score"),
                        "rejection_reason": "No direct PDF/text download URL; kept as metadata-only or human-review lead.",
                    }
                )
        return rejected[:20]

    def _seed_author_candidate(self, author: dict) -> dict:
        return {
            "author_id": author["author_id"],
            "name": author["name"],
            "name_ar": author["name_ar"],
            "aliases": author["aliases"],
            "death_year": author["death_year"],
            "period": author["period"],
            "tradition": author["tradition"],
            "hypothesis_fit": 0.45,
            "relationship_reason": "Curated Hermeneut author metadata retained for candidate scoring.",
            "metadata_status": "curated_seed_metadata",
        }

    def _logic_author_templates(self) -> list[dict]:
        return [
            self._external_author("nasir-al-din-al-tusi", "Nasir al-Din al-Tusi", "نصير الدين الطوسي", "7th/13th century", "logic/philosophy", 1274),
            self._external_author("najm-al-din-al-katibi", "Najm al-Din al-Katibi", "نجم الدين الكاتبي", "7th/13th century", "logic", 1277),
            self._external_author("qutb-al-din-al-razi", "Qutb al-Din al-Razi", "قطب الدين الرازي", "8th/14th century", "logic commentary / Shamsiyya commentary", 1365),
            self._external_author("athir-al-din-al-abhari", "Athir al-Din al-Abhari", "أثير الدين الأبهري", "7th/13th century", "logic/philosophy", 1265),
            self._external_author("al-jurjani", "al-Jurjani", "الجرجاني", "8th/14th century", "kalam/logic commentary", 1413),
            self._external_author("al-taftazani", "al-Taftazani", "التفتازاني", "8th/14th century", "kalam/logic commentary", 1390),
        ]

    def _shamsiyya_author_templates(self) -> list[dict]:
        return [
            self._external_author(
                "qutb-al-din-al-razi",
                "Qutb al-Din al-Razi",
                "قطب الدين الرازي",
                "8th/14th century",
                "logic commentary / Shamsiyya commentary",
                1365,
            ),
            self._external_author(
                "najm-al-din-al-katibi",
                "Najm al-Din al-Katibi",
                "نجم الدين الكاتبي",
                "7th/13th century",
                "logic / author of al-Risala al-Shamsiyya",
                1277,
            ),
            self._external_author(
                "al-taftazani",
                "al-Taftazani",
                "التفتازاني",
                "8th/14th century",
                "logic commentary / Shamsiyya commentary",
                1390,
            ),
            self._external_author(
                "al-jurjani",
                "al-Jurjani",
                "الجرجاني",
                "8th/14th century",
                "logic commentary / hashiya tradition",
                1413,
            ),
        ]

    def _falsafa_kalam_templates(self) -> list[dict]:
        return [
            self._external_author("ibn-rushd", "Ibn Rushd", "ابن رشد", "6th/12th century", "falsafa", 1198),
            self._external_author("al-amidi", "al-Amidi", "الآمدي", "7th/13th century", "kalam/philosophy", 1233),
            self._external_author("al-baqillani", "al-Baqillani", "الباقلاني", "5th/11th century", "kalam", 1013),
        ]

    def _external_author(self, author_id: str, name: str, name_ar: str, period: str, tradition: str, death_year: int) -> dict:
        return {
            "author_id": author_id,
            "name": name,
            "name_ar": name_ar,
            "aliases": [],
            "death_year": death_year,
            "period": period,
            "tradition": tradition,
            "hypothesis_fit": 0.55,
            "relationship_reason": "Expanded candidate from classical Arabic logic/kalam/falsafa reference network.",
            "metadata_status": "requires_backend_verification",
        }

    def _domain_fit(self, item: dict, context: DetectedContext, normalized: str) -> float:
        tradition = normalize_arabic(str(item.get("tradition", "")))
        domain = normalize_arabic(context.domain)
        if "shamsiyya" in item.get("tradition", "").lower():
            return 0.96
        if "logic" in item.get("tradition", "") and any(term in normalized for term in ["الامكان", "صدق", "القضيه"]):
            return 0.9
        if "falsafa" in tradition or "philosophy" in item.get("tradition", ""):
            return 0.82 if "philosophy" in context.domain else 0.65
        if "kalam" in tradition:
            return 0.8 if "kalam" in domain or "philosophy" in domain else 0.55
        return 0.45

    def _source_availability_hint(self, item: dict) -> float:
        if item.get("author_id") in {
            "qutb-al-din-al-razi",
            "najm-al-din-al-katibi",
            "nasir-al-din-al-tusi",
            "ibn-sina",
            "fakhr-al-din-al-razi",
        }:
            return 0.72
        if item.get("metadata_status") == "curated_seed_metadata":
            return 0.62
        return 0.48

    def _chronology_fit(self, item: dict, payload: RunCreate) -> float:
        if not payload.period_hint:
            return 0.65
        hint = payload.period_hint.lower()
        period = str(item.get("period", "")).lower()
        return 0.85 if any(token in period for token in hint.replace("/", " ").split()[:2]) else 0.55

    def _containing_relation_fit(self, item: dict, payload: RunCreate) -> float:
        if not (payload.containing_author or payload.containing_work):
            return 0.5
        text = " ".join([payload.containing_author or "", payload.containing_work or ""]).lower()
        protocol_fit = self.protocol.relationship_fit(str(item.get("author_id", "")), payload)
        if protocol_fit:
            score, reason = protocol_fit
            item["relationship_reason"] = reason
            item["source_role"] = item.get("source_role") or "commentary_chain_candidate"
            return score
        normalized = self._containing_text(payload)
        author_id = str(item.get("author_id", ""))
        if "شمسي" in normalized:
            if author_id == "qutb-al-din-al-razi":
                item["relationship_reason"] = (
                    "The containing work is a hashiya on al-Shamsiyya; Qutb al-Din al-Razi's Tahrir/Sharh "
                    "is the central intermediary commentary that later hashiyas commonly cite."
                )
                return 0.98
            if author_id == "najm-al-din-al-katibi":
                item["relationship_reason"] = "The containing work is on al-Shamsiyya, whose base text is by al-Katibi."
                return 0.92
            if author_id in {"al-taftazani", "al-jurjani"}:
                item["relationship_reason"] = "Later Shamsiyya commentary/hashiya tradition candidate."
                return 0.84
        tradition = str(item.get("tradition", "")).lower()
        if any(token in text for token in ["ghazali", "razi", "tahafut", "muhassal"]) and (
            "falsafa" in tradition or "logic" in tradition
        ):
            return 0.82
        if "commentary" in tradition or "logic" in tradition:
            return 0.74
        return 0.58

    def _containing_text(self, payload: RunCreate) -> str:
        return normalize_arabic(" ".join([payload.containing_author or "", payload.containing_work or ""]))

    def _is_shamsiyya_commentary_context(self, payload: RunCreate) -> bool:
        text = self._containing_text(payload)
        library_hint = normalize_arabic(payload.library_id or "")
        has_shamsiyya = any(marker in text or marker in library_hint for marker in ["شمسي", "shamsiyya", "shamsiya"])
        has_commentary = any(
            marker in text
            for marker in ["حاشيه", "حاشية", "شرح", "تعليق", "علي", "على", "hashiya", "hashiyah", "sharh", "commentary", "ala"]
        )
        return has_shamsiyya and (has_commentary or "shamsiyya" in library_hint or "shamsiya" in library_hint)

    def _web_hit_strength(self, item: dict, phrase_variants: list[dict], web_hits: list[dict]) -> float:
        haystack = normalize_arabic(" ".join([item.get("name", ""), item.get("name_ar", ""), *item.get("aliases", [])]))
        score = 0.25
        for hit in web_hits:
            text = normalize_arabic(" ".join([str(hit.get("title", "")), str(hit.get("snippet", "")), str(hit.get("url", ""))]))
            if any(part and part in text for part in haystack.split()):
                score = max(score, 0.72)
            for variant in phrase_variants:
                query = normalize_arabic(variant["query"])
                if query and query[:20] in text:
                    score = max(score, 0.82)
        return score

    def _academic_author_id(self, candidate: dict) -> str:
        name = normalize_arabic(" ".join([str(candidate.get("name", "")), str(candidate.get("name_ar", ""))]))
        for author in AUTHORS:
            haystack = normalize_arabic(" ".join([author["name"], author["name_ar"], *author["aliases"]]))
            if name and any(part and part in haystack for part in name.split()):
                return author["author_id"]
        raw = str(candidate.get("name_ar") or candidate.get("name") or "academic-author")
        return f"academic-author-{sha1(raw.encode()).hexdigest()[:10]}"

    def _match_academic_work_author(self, author_name: str, author_candidates: list[dict]) -> str:
        normalized = normalize_arabic(author_name)
        if normalized:
            for author in author_candidates:
                haystack = normalize_arabic(" ".join([str(author.get("name", "")), str(author.get("name_ar", ""))]))
                if normalized in haystack or any(part and part in haystack for part in normalized.split()):
                    return str(author["author_id"])
        return "model-academic-author"

    def _numeric_score(self, value: object, default: float) -> float:
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        if numeric > 1:
            numeric = numeric / 100
        return max(0.0, min(1.0, numeric))

    def _work_candidate(self, work: dict, author_score: float, metadata_status: str) -> dict:
        return {
            "work_id": work["work_id"],
            "title": work["title"],
            "title_ar": work["title_ar"],
            "author_id": work["author_id"],
            "domain": work["domain"],
            "language": work["language"],
            "score": round(min(0.95, author_score + 0.06), 3),
            "score_breakdown": {"author_score": author_score, "metadata_reliability": 0.75},
            "relationship_reason": "Work belongs to a scored author candidate.",
            "metadata_status": metadata_status,
        }

    def _external_work_candidate(
        self,
        work_id: str,
        title: str,
        title_ar: str,
        author_id: str,
        score: float,
        relationship_reason: str | None = None,
        source_role: str | None = None,
    ) -> dict:
        return {
            "work_id": work_id,
            "title": title,
            "title_ar": title_ar,
            "author_id": author_id,
            "domain": "logic/philosophy",
            "language": "ar",
            "score": score,
            "score_breakdown": {"domain_fit": 0.88, "metadata_reliability": 0.45, "source_availability": 0.5},
            "relationship_reason": relationship_reason
            or "Expanded work candidate for modal logic and post-Avicennan commentary traditions.",
            "source_role": source_role or "expanded_candidate",
            "metadata_status": "requires_backend_verification",
        }

    def _related_hits(self, query: str, label: str, web_hits: list[dict]) -> list[dict]:
        label_norm = normalize_arabic(label)
        related = []
        for hit in web_hits:
            hit_text = normalize_arabic(" ".join([str(hit.get("title", "")), str(hit.get("snippet", "")), str(hit.get("url", ""))]))
            if any(part and part in hit_text for part in label_norm.split()) or hit.get("is_pdf"):
                related.append(hit)
        return related[:3]

    def _metadata_source_candidate(self, work: dict) -> dict:
        query = quote_plus(f"{work['title']} {work.get('title_ar', '')} PDF archive.org OpenITI")
        return self._with_role_metadata({
            "source_id": f"metadata-{work['work_id']}",
            "work_id": work["work_id"],
            "provider": "Bibliographic Reasoning",
            "title": work["title"],
            "url": f"https://www.google.com/search?q={query}",
            "source_page_url": f"https://www.google.com/search?q={query}",
            "download_url": None,
            "file_type": "metadata_search",
            "download_policy": "human_review_required",
            "ingestion_status": "metadata_only",
            "lifecycle_status": "requires_human_review",
            "relationship_reason": work.get("relationship_reason"),
            "provenance": work.get("metadata_status", "candidate_reasoning"),
            "verification_status": "metadata_only",
            "license_status": "needs_review",
            "candidate_score": work.get("score", 0.4),
            "relevance_score": work.get("score", 0.4),
            "relevance_breakdown": {"metadata_only": True, "direct_download": False},
        }, work, f"{work['title']} {work.get('title_ar', '')} PDF archive.org OpenITI", self._normalized_source_role(work))

    def _web_source_candidate(self, work: dict, source_url: str) -> dict:
        is_pdf = source_url.lower().split("?")[0].endswith(".pdf")
        return self._with_role_metadata({
            "source_id": f"web-{sha1(source_url.encode()).hexdigest()[:10]}",
            "work_id": work["work_id"],
            "provider": "Bibliographic Web Hit",
            "title": work["title"],
            "url": source_url,
            "source_page_url": source_url,
            "download_url": source_url if is_pdf else None,
            "file_type": "pdf" if is_pdf else "html",
            "download_policy": "admin_approval_required",
            "ingestion_status": "web_discovered",
            "lifecycle_status": "download_candidate" if is_pdf else "requires_human_review",
            "relationship_reason": "Web hit promoted through candidate-specific bibliographic reasoning.",
            "provenance": work.get("metadata_status", "web_result_unverified"),
            "verification_status": "metadata_only",
            "license_status": "needs_review",
            "candidate_score": work.get("score", 0.4),
            "relevance_score": work.get("score", 0.4) + (0.12 if is_pdf else 0.0),
            "relevance_breakdown": {"web_hit": True, "direct_download": is_pdf},
        }, work, source_url, self._normalized_source_role(work))

    def _source_relevance(self, doc: dict, work: dict, query: str, has_download: bool) -> float:
        breakdown = self._source_relevance_breakdown(doc, work, query, has_download)
        return round(sum(breakdown.values()), 3)

    def _source_relevance_breakdown(self, doc: dict, work: dict, query: str, has_download: bool) -> dict[str, float]:
        title = normalize_arabic(str(doc.get("title", "")))
        work_title = normalize_arabic(str(work.get("title", "")))
        work_title_ar = normalize_arabic(str(work.get("title_ar", "")))
        query_norm = normalize_arabic(query)
        title_match = 0.3 if (work_title and work_title in title) or (work_title_ar and work_title_ar in title) else 0.0
        query_match = 0.18 if any(part and part in title for part in query_norm.split()[:4]) else 0.0
        work_score = min(0.2, float(work.get("score", 0.0)) * 0.2)
        download_score = 0.22 if has_download else 0.0
        archive_score = 0.08
        return {
            "title_match": title_match,
            "query_match": query_match,
            "work_candidate_score": round(work_score, 3),
            "direct_download": download_score,
            "trusted_provider": archive_score,
        }
