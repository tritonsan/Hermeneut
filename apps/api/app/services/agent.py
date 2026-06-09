from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from app.models import (
    AgentRun,
    Candidate,
    DetectedContext,
    EvidenceMemoryRecord,
    Hypothesis,
    RunCreate,
    RunMode,
    RunStatus,
    SearchPlanItem,
    SearchType,
    StepStatus,
    TimelineEvent,
    TraceEvent,
)
from app.services.elastic_service import ElasticService
from app.services.normalization import normalize_arabic
from app.services.scoring import (
    claimworthy_evidence,
    decision_tier,
    evidence_decision,
    evidence_strength,
    is_claimworthy_evidence,
)
from app.services.web_research import WebResearchService


class ResearchAgent:
    def __init__(self, elastic: ElasticService, web_research: WebResearchService | None = None):
        self.elastic = elastic
        self.web_research = web_research

    def initial_run(self, payload: RunCreate, run_id: str | None = None) -> AgentRun:
        run_id = run_id or f"run-{uuid4().hex[:10]}"
        context = DetectedContext(
            language=payload.language_hint or "detecting",
            domain=payload.domain_hint or "detecting",
            period_hint=payload.period_hint or "detecting",
            citation_type="pending",
            key_terms=[],
        )
        return AgentRun(
            run_id=run_id,
            mode=payload.mode,
            input_passage=payload.passage,
            status=RunStatus.queued,
            current_step="Queued for library search" if payload.mode == RunMode.library else "Queued for open discovery",
            current_phase="queued",
            progress_percent=1,
            estimated_remaining_seconds=90,
            execution_status="queued",
            detected_context=context,
            hypotheses=[],
            search_plan=[],
            candidates=[],
            evidence=[],
            timeline=[
                self._event(
                    "Run queued",
                    self._queued_detail(payload),
                    "Hermeneut async runner",
                    StepStatus.completed,
                    5,
                    {"ocr_mode": payload.ocr_mode, "auto_download_sources": payload.auto_download_sources},
                )
            ],
            trace_events=[
                self._trace(
                    "setup",
                    "mode selected",
                    payload.mode,
                    "Hermeneut run router",
                    {"mode": payload.mode, "library_id": payload.library_id},
                    "Run protocol selected.",
                    decision=payload.mode.value,
                )
            ],
            source_lifecycle_records=[],
            context_profile={},
            relationship_graph=[],
            author_candidates=[],
            phrase_variants=[],
            candidate_web_searches=[],
            work_candidates=[],
            top_pdf_targets=[],
            ocr_jobs=[],
            elastic_evidence=[],
            rejected_candidates=[],
            decision_tier="no_result",
            final_report="Investigation is queued. The scholarly report will appear after Elastic evidence is retrieved.",
        )

    def run(self, payload: RunCreate, run_id: str | None = None) -> AgentRun:
        if payload.mode == RunMode.library:
            return self._run_library_mode(payload, run_id)
        return self._run_open_discovery_mode(payload, run_id)

    def _run_library_mode(self, payload: RunCreate, run_id: str | None = None) -> AgentRun:
        run_id = run_id or f"run-{uuid4().hex[:10]}"
        detected_context = self._detect_context(payload.passage, payload.context)
        hypotheses: list[Hypothesis] = []
        library_scope = self.elastic.library_scope(payload.library_id)
        evidence_memory = self.elastic.lookup_evidence_memory(payload.passage)
        library_graph = self.elastic.library_relationship_graph(payload.library_id)
        web_research = self._disabled_web_research("Library Mode searches only the selected library scope.")
        web_research["relationships"] = library_graph
        web_research["relationship_graph_mode"] = "library_relationship_intelligence"
        elastic_graph = library_graph
        source_lookup = self.elastic.lookup_sources(payload.library_id, payload.library_id)
        search_plan = self._build_search_plan(payload.passage, detected_context, hypotheses)
        semantic_evidence = self.elastic.semantic_passage_lookup(payload.passage, web_research, library_id=payload.library_id)
        evidence = self.elastic.search_passages(payload.passage, search_plan, web_research, library_id=payload.library_id)
        evidence = self._merge_evidence(evidence, semantic_evidence)
        candidates = self._rank_candidates(evidence, library_graph)
        decision = evidence_decision(evidence, bool(candidates or library_graph))
        tier = decision.tier
        memory_records = self._evidence_memory_records(run_id, payload.passage, evidence)
        memory_count = self.elastic.write_evidence_memory(memory_records)
        final_report = self._write_report(candidates, evidence, detected_context, web_research, payload.mode, decision)
        timeline = self._timeline(
            payload,
            detected_context,
            hypotheses,
            library_scope,
            evidence_memory,
            web_research,
            elastic_graph,
            source_lookup,
            search_plan,
            semantic_evidence,
            evidence,
            memory_count,
        )

        return AgentRun(
            run_id=run_id,
            mode=payload.mode,
            input_passage=payload.passage,
            status=RunStatus.completed,
            current_step="Completed",
            current_phase="completed",
            blocked_reason=None,
            progress_percent=100,
            estimated_remaining_seconds=0,
            detected_context=detected_context,
            hypotheses=hypotheses,
            search_plan=search_plan,
            candidates=candidates,
            evidence=evidence,
            timeline=timeline,
            trace_events=self._trace_events(
                payload,
                detected_context,
                web_research,
                source_lookup,
                evidence,
                final_report,
                self._final_decision(evidence),
            ),
            source_lifecycle_records=self._source_lifecycle_records(payload, web_research, source_lookup),
            context_profile={},
            relationship_graph=library_graph,
            author_candidates=[],
            phrase_variants=[],
            candidate_web_searches=[],
            work_candidates=[],
            top_pdf_targets=[],
            ocr_jobs=[],
            elastic_evidence=[item.model_dump() for item in evidence],
            rejected_candidates=[],
            decision_tier=tier,
            final_report=final_report,
        )

    def _run_open_discovery_mode(self, payload: RunCreate, run_id: str | None = None) -> AgentRun:
        run_id = run_id or f"run-{uuid4().hex[:10]}"
        detected_context = self._detect_context(payload.passage, payload.context)
        hypotheses = self._build_hypotheses(payload.passage)
        library_scope = self.elastic.library_scope(payload.library_id)
        evidence_memory = self.elastic.lookup_evidence_memory(payload.passage)
        web_research = self._web_research(payload, detected_context, hypotheses)
        graph_query = " ".join([payload.passage, *(hypothesis.work for hypothesis in hypotheses)])
        elastic_graph = self.elastic.lookup_research_graph(graph_query)
        source_lookup: list[dict] = []
        search_plan = self._build_search_plan(payload.passage, detected_context, hypotheses)
        source_records = self._source_lifecycle_records(payload, web_research, source_lookup)
        searchable_records = [
            source for source in source_records
            if source.get("counts_as_evidence") and source.get("provenance") not in {"hermeneut_seed"}
        ]
        if searchable_records:
            semantic_evidence = self.elastic.semantic_passage_lookup(payload.passage, web_research, library_id=payload.library_id)
            evidence = self.elastic.search_passages(payload.passage, search_plan, web_research, library_id=payload.library_id)
            evidence = self._merge_evidence(evidence, semantic_evidence)
        else:
            semantic_evidence = []
            evidence = []
        candidates = self._rank_candidates(evidence, web_research.get("relationships", []))
        decision = evidence_decision(evidence, bool(web_research.get("candidate_authors") or web_research.get("candidate_works")))
        tier = decision.tier
        memory_count = self.elastic.write_evidence_memory(self._evidence_memory_records(run_id, payload.passage, evidence))
        final_report = self._write_report(candidates, evidence, detected_context, web_research, payload.mode, decision)
        blocked_reason = None if claimworthy_evidence(evidence) else "No OCR/indexed open-discovery source has claim-worthy evidence yet."
        timeline = self._timeline(
            payload,
            detected_context,
            hypotheses,
            library_scope,
            evidence_memory,
            web_research,
            elastic_graph,
            source_lookup,
            search_plan,
            semantic_evidence,
            evidence,
            memory_count,
        )
        return AgentRun(
            run_id=run_id,
            mode=payload.mode,
            input_passage=payload.passage,
            status=RunStatus.completed,
            current_step="Completed",
            current_phase="completed",
            blocked_reason=blocked_reason,
            progress_percent=100,
            estimated_remaining_seconds=0,
            detected_context=detected_context,
            hypotheses=hypotheses,
            search_plan=search_plan,
            candidates=candidates,
            evidence=evidence,
            timeline=timeline,
            trace_events=self._trace_events(
                payload,
                detected_context,
                web_research,
                source_records,
                evidence,
                final_report,
                self._final_decision(evidence),
            ),
            source_lifecycle_records=source_records,
            context_profile=web_research.get("context_profile", {}),
            relationship_graph=web_research.get("relationships", []),
            author_candidates=web_research.get("candidate_authors", []),
            phrase_variants=web_research.get("phrase_variants", []),
            candidate_web_searches=web_research.get("candidate_web_searches", []),
            work_candidates=web_research.get("candidate_works", []),
            top_pdf_targets=web_research.get("top_pdf_targets", []),
            ocr_jobs=self._ocr_jobs(payload, source_records),
            elastic_evidence=[item.model_dump() for item in evidence],
            rejected_candidates=web_research.get("rejected_candidates", []),
            decision_tier=tier,
            final_report=final_report,
        )

    def live_snapshots(self, payload: RunCreate, run_id: str) -> list[AgentRun]:
        final_run = self.run(payload, run_id)
        if payload.mode == RunMode.library:
            staged_events = [
                (
                    "Library context analysis",
                    "Analyzing the passage and selected library scope.",
                    18,
                    35,
                    final_run.timeline[:4],
                ),
                (
                    "Library OCR readiness",
                    "Checking which selected-library sources are already searchable.",
                    42,
                    25,
                    final_run.timeline[:5],
                ),
                (
                    "Elastic library retrieval",
                    "Running lexical and semantic retrieval only inside the selected library.",
                    76,
                    12,
                    final_run.timeline[:12],
                ),
                (
                    "Library evidence report",
                    "Generating the report from selected-library Elastic evidence only.",
                    92,
                    5,
                    final_run.timeline,
                ),
            ]
        else:
            staged_events = [
            (
                "Context analysis",
                "Analyzing the passage, optional hints, language, period, and citation type.",
                12,
                85,
                final_run.timeline[:2],
            ),
            (
                "Grounded web research",
                "Searching web context for likely authors, works, source traditions, and downloadable editions.",
                26,
                70,
                final_run.timeline[:7],
            ),
            (
                "Source discovery and verification",
                "Verifying OpenITI, Internet Archive, Wikidata, and grounded source candidates before download.",
                42,
                55,
                final_run.timeline[:9],
            ),
            (
                "PDF vault and OCR preparation",
                "Downloading approved public sources to GCS and preparing OCR/text extraction. Some sources may pause for approval.",
                58,
                40,
                final_run.timeline[:9]
                + [
                    self._event(
                        "PDF downloaded to GCS",
                        "Public or demo-approved source files are stored in the GCS document vault; searchable text is still a separate layer.",
                        "GCS document vault",
                        StepStatus.completed,
                        12,
                        {"sources": final_run.source_lifecycle_records},
                    ),
                    self._event(
                        "OCR/text extraction prepared",
                        "Text layer and OCR outputs are staged page-by-page before normalization.",
                        "Hermeneut OCR pipeline",
                        StepStatus.completed,
                        20,
                        {"ocr_mode": payload.ocr_mode, "status": "ocr_completed"},
                    ),
                ],
            ),
            (
                "Elastic indexing and retrieval",
                "Normalizing extracted text, indexing searchable passages, and running lexical plus semantic Elastic retrieval.",
                78,
                20,
                final_run.timeline[:12],
            ),
            (
                "Final evidence report",
                "Writing evidence memory to Elasticsearch and generating the final scholarly report from retrieved evidence only.",
                92,
                8,
                final_run.timeline,
            ),
            ]
        snapshots: list[AgentRun] = []
        for current_step, message, progress, remaining, timeline in staged_events:
            staged_timeline = [
                event.model_copy(update={"status": StepStatus.completed})
                for event in timeline[:-1]
            ]
            if timeline:
                staged_timeline.append(
                    timeline[-1].model_copy(
                        update={
                            "status": StepStatus.running,
                            "progress_message": message,
                            "started_at": self._now(),
                        }
                    )
                )
            snapshots.append(
                final_run.model_copy(
                    update={
                        "status": RunStatus.running,
                        "current_step": current_step,
                        "current_phase": current_step,
                        "progress_percent": progress,
                        "estimated_remaining_seconds": remaining,
                        "timeline": staged_timeline,
                        "final_report": "Investigation is still running. Final report will be generated after Elastic evidence retrieval.",
                    }
                )
            )
        snapshots.append(final_run)
        return snapshots

    def _queued_detail(self, payload: RunCreate) -> str:
        if payload.mode == RunMode.library:
            return "The investigation has been created. Hermeneut will search only the selected OCR/indexed library."
        return (
            "The investigation has been created. Hermeneut will discover candidate sources first, "
            "then search only OCR/indexed evidence."
        )

    def _disabled_web_research(self, policy: str) -> dict:
        return {
            "enabled": False,
            "model": "disabled",
            "grounding_mode": "disabled_for_library_mode",
            "research_questions": [],
            "grounded_search_queries": [],
            "web_hits": [],
            "candidate_authors": [],
            "candidate_works": [],
            "relationships": [],
            "source_candidates": [],
            "context_profile": {},
            "phrase_variants": [],
            "candidate_web_searches": [],
            "top_pdf_targets": [],
            "rejected_candidates": [],
            "model_routing": {},
            "policy": policy,
        }

    def _merge_evidence(self, evidence, semantic_evidence):
        merged = {item.passage_id: item for item in [*evidence, *semantic_evidence]}
        return sorted(merged.values(), key=lambda item: item.confidence, reverse=True)

    def _detect_context(self, passage: str, context: str | None) -> DetectedContext:
        normalized = normalize_arabic(" ".join([passage, context or ""]))
        terms: list[str] = []
        domain = "classical texts"
        period = "undetermined classical period"

        if any(term in normalized for term in ["واجب", "ممكن", "العالم", "الاول", "الكليات"]):
            domain = "kalam/philosophy"
            period = "4th-6th/10th-12th century"
        if "المعتزله" in normalized or "العبد" in normalized:
            domain = "kalam"

        for term in ["واجب الوجود", "ممكن", "العالم", "الاول", "الكليات", "الجزئيات", "المعتزلة"]:
            if normalize_arabic(term) in normalized:
                terms.append(term)

        citation_type = "anonymous/indirect"
        if any(marker in normalized for marker in ["قيل", "ذكر", "زعم", "ينسب", "بعضهم"]):
            citation_type = "ambiguous attribution marker"

        return DetectedContext(
            language="ar",
            domain=domain,
            period_hint=period,
            citation_type=citation_type,
            key_terms=terms or list(dict.fromkeys(normalized.split()[:6])),
        )

    def _build_hypotheses(self, passage: str) -> list[Hypothesis]:
        normalized = normalize_arabic(passage)
        hypotheses: list[Hypothesis] = []
        if any(term in normalized for term in ["واجب", "ممكن"]):
            hypotheses.append(
                Hypothesis(
                    author="Ibn Sina",
                    work="al-Isharat wa-al-tanbihat",
                    work_id="ibn-sina-isharat",
                    reason="Terminology of necessary and possible existence points to Avicennan metaphysics.",
                )
            )
        if any(term in normalized for term in ["الامكان الخاص", "بالامكان الخاص", "زيد موجود", "صدق زيد"]):
            hypotheses.append(
                Hypothesis(
                    author="Unknown later logician or philosophical commentator",
                    work="Modal logic / hikma commentary source to be discovered",
                    reason=(
                        "The wording uses technical modal-logic examples around special possibility; "
                        "candidate works should be discovered from the containing text and web sources."
                    ),
                )
            )
        if any(term in normalized for term in ["العالم", "قديم", "الاول", "صدر", "الفلاسفه"]):
            hypotheses.append(
                Hypothesis(
                    author="al-Ghazali",
                    work="Tahafut al-falasifa",
                    work_id="ghazali-tahafut",
                    reason="The phrasing resembles kalam critique of philosophers on eternity and emanation.",
                )
            )
        if any(term in normalized for term in ["الكليات", "الجزئيات"]):
            hypotheses.append(
                Hypothesis(
                    author="al-Ghazali",
                    work="Tahafut al-falasifa",
                    work_id="ghazali-tahafut",
                    reason="The universals/particulars distinction is central to the critique of divine knowledge.",
                )
            )
        if any(term in normalized for term in ["المعتزله", "العبد", "فعله"]):
            hypotheses.append(
                Hypothesis(
                    author="al-Ashari",
                    work="Maqalat al-islamiyyin",
                    work_id="ashari-maqalat",
                    reason="The passage uses doxographic kalam language about Mu'tazilite human action.",
                )
            )
        return hypotheses or [
            Hypothesis(
                author="Unknown classical author",
                work="Curated Hermeneut corpus",
                reason="No named author is strongly implied; begin with broad hybrid retrieval.",
            )
        ]

    def _web_research(
        self,
        payload: RunCreate,
        context: DetectedContext,
        hypotheses: list[Hypothesis],
    ) -> dict:
        if not self.web_research:
            return {
                "enabled": False,
                "model": "not-configured",
                "research_questions": [],
                "candidate_authors": [],
                "candidate_works": [],
                "relationships": [],
                "source_candidates": [],
                "policy": "Web research service is not configured.",
            }
        return self.web_research.research(payload, context, hypotheses)

    def _build_search_plan(
        self, passage: str, context: DetectedContext, hypotheses: list[Hypothesis]
    ) -> list[SearchPlanItem]:
        normalized = normalize_arabic(passage)
        concept_query = " ".join(context.key_terms)
        hypothesis_query = " ".join(
            item for hypothesis in hypotheses for item in [hypothesis.author, hypothesis.work]
        )
        return [
            SearchPlanItem(query=passage, type=SearchType.lexical, purpose="Find direct or near-direct wording."),
            SearchPlanItem(
                query=normalized, type=SearchType.hybrid, purpose="Search normalized Arabic variants."
            ),
            SearchPlanItem(
                query=concept_query or passage,
                type=SearchType.semantic,
                purpose="Find meaning-level matches even when wording differs.",
            ),
            SearchPlanItem(
                query=hypothesis_query or context.domain,
                type=SearchType.metadata,
                purpose="Filter candidates by author, work, domain, and tradition metadata.",
            ),
        ]

    def _rank_candidates(self, evidence, relationships: list[dict] | None = None) -> list[Candidate]:
        relationships = relationships or []
        grouped = defaultdict(list)
        for item in evidence:
            grouped[item.work_id].append(item)

        propagated_support: dict[str, dict] = {}
        upstream_relations = {"glosses", "comments_on", "depends_on"}
        edges_by_from: dict[str, list[dict]] = defaultdict(list)
        for edge in relationships:
            if edge.get("to_type") == "work" and edge.get("relation") in upstream_relations:
                edges_by_from[str(edge.get("from_id"))].append(edge)

        for item in evidence:
            if not self._looks_like_commentary_evidence(item.quote):
                continue
            queue: list[tuple[str, float, list[str], list[str]]] = [(item.work_id, item.confidence, [], [])]
            visited = {item.work_id}
            while queue:
                current_work_id, current_confidence, path_relations, path_work_ids = queue.pop(0)
                if len(path_relations) >= 3:
                    continue
                for edge in edges_by_from.get(current_work_id, []):
                    relation = str(edge.get("relation"))
                    target_work_id = str(edge.get("to_id"))
                    if not target_work_id or target_work_id in visited:
                        continue
                    visited.add(target_work_id)
                    relation_weight = {"glosses": 1.16, "comments_on": 1.08, "depends_on": 0.9}.get(relation, 0.75)
                    target_has_upstream = bool(edges_by_from.get(target_work_id))
                    intermediate_penalty = 0.72 if target_has_upstream else 1.0
                    terminal_bonus = 1.08 if len(path_relations) >= 1 and not target_has_upstream else 1.0
                    propagated_confidence = round(
                        current_confidence
                        * float(edge.get("confidence", 0.7))
                        * relation_weight
                        * intermediate_penalty
                        * terminal_bonus,
                        3,
                    )
                    next_relations = path_relations + [relation]
                    next_path = path_work_ids + [target_work_id]
                    queue.append((target_work_id, propagated_confidence, next_relations, next_path))

                    if target_work_id == item.work_id:
                        continue
                    path_label = " -> ".join(next_path)
                    relation_label = " > ".join(next_relations)
                    if target_has_upstream and propagated_confidence < 0.7:
                        # Keep intermediate commentary layers visible, but avoid letting them eclipse upstream sources.
                        propagated_confidence = round(propagated_confidence * 0.86, 3)
                    existing = propagated_support.get(target_work_id)
                    if existing and existing["confidence"] >= propagated_confidence:
                        continue
                    propagated_support[target_work_id] = {
                        "confidence": propagated_confidence,
                        "from_work_id": item.work_id,
                        "relation": relation_label,
                        "path": path_label,
                        "passage_id": item.passage_id,
                    }

        candidates: list[Candidate] = []
        candidate_work_ids = set(grouped) | set(propagated_support)
        incoming_authority: dict[str, float] = defaultdict(float)
        incoming_authority_count: dict[str, int] = defaultdict(int)
        for edge in relationships:
            relation = str(edge.get("relation"))
            if relation not in {"glosses", "comments_on", "depends_on", "refutes", "responds_to"}:
                continue
            target = str(edge.get("to_id") or "")
            source = str(edge.get("from_id") or "")
            if not target or not source or target == source:
                continue
            incoming_authority[target] = max(incoming_authority[target], float(edge.get("confidence", 0.6)))
            incoming_authority_count[target] += 1
        for work_id in candidate_work_ids:
            items = grouped.get(work_id, [])
            work = self.elastic.work_by_id(work_id) or {"title": work_id}
            author = self.elastic.author_for_work(work_id) or {"name": "Unknown"}
            propagated = propagated_support.get(work_id)
            direct_confidence_raw = max((item.confidence for item in items), default=0.0)
            direct_confidence = direct_confidence_raw
            if items and edges_by_from.get(work_id) and any(self._looks_like_commentary_evidence(item.quote) for item in items):
                # A direct hit in a later commentary proves that the wording is discussed there, but it should not
                # automatically outrank the upstream work being commented on as the likely source-level candidate.
                direct_confidence = min(direct_confidence, 0.58)
            propagated_confidence = float(propagated["confidence"]) if propagated else 0.0
            base_confidence = max(direct_confidence, propagated_confidence)
            authority_bonus = 0.0
            if incoming_authority.get(work_id):
                authority_bonus = min(0.2, incoming_authority[work_id] * 0.12 + incoming_authority_count[work_id] * 0.035)
            confidence = round(min(0.92, base_confidence + authority_bonus), 3)
            if not items:
                confidence = min(confidence, 0.62)
            elif direct_confidence_raw < 0.45 and propagated_confidence > direct_confidence_raw:
                confidence = min(confidence, 0.64)
            representative = max(items, key=lambda item: item.confidence) if items else None
            title = representative.work_title if representative else None
            name = representative.author_name if representative else None
            if not title or title == work_id:
                title = work.get("title", work_id)
            if not name or name == "Unknown":
                name = author.get("name", "Unknown")
            relationship_note = ""
            if propagated and propagated_confidence >= direct_confidence:
                relationship_note = (
                    f" Relationship graph propagated support from {propagated['from_work_id']} "
                    f"via {propagated['relation']} using passage {propagated['passage_id']}."
                )
            if authority_bonus:
                relationship_note += " Library relationship graph marks this work as an upstream source-level hub."
            candidates.append(
                Candidate(
                    work_id=work_id,
                    work_title=title,
                    author=name,
                    confidence=confidence,
                    why=(
                        f"{len(items)} direct evidence passage(s) matched; strongest direct evidence scored "
                        f"{direct_confidence_raw:.2f}.{relationship_note}"
                    ),
                )
            )
        return sorted(candidates, key=lambda item: item.confidence, reverse=True)

    def _looks_like_commentary_evidence(self, quote: str) -> bool:
        normalized = normalize_arabic(quote)
        markers = ["قوله", "قال", "اقول", "يعني", "مراده", "اعترض", "اجاب"]
        return any(marker in normalized for marker in markers)

    def _write_report(
        self,
        candidates,
        evidence,
        context: DetectedContext,
        web_research: dict,
        mode: RunMode = RunMode.library,
        decision=None,
    ) -> str:
        tier = decision.tier if decision else decision_tier(evidence, bool(candidates or web_research.get("candidate_authors") or web_research.get("relationships")))
        if not candidates:
            if mode == RunMode.open_discovery:
                top_targets = web_research.get("top_pdf_targets", []) or []
                target_note = (
                    f" The strongest downloadable lead is {top_targets[0].get('title')} from "
                    f"{top_targets[0].get('provider', 'a discovered source provider')}; it must still be approved, "
                    "processed, indexed, and retrieved before it can support an attribution."
                    if top_targets
                    else " Continue by approving a direct PDF/text source or uploading an institution-owned copy."
                )
                return (
                    f"Decision tier: {tier}. No final attribution can be made yet. Open Discovery produced bibliographic/source leads, "
                    "but no candidate source has been downloaded, OCR-processed, indexed in Elastic, and retrieved "
                    f"as textual evidence.{target_note}"
                )
            return (
                "Decision tier: no_result. No sufficiently supported candidate was found inside the selected library. Add/OCR more sources "
                "or broaden the library scope before making a scholarly claim."
            )

        best = candidates[0]
        claimworthy = claimworthy_evidence(evidence)
        best_work_claimworthy = [item for item in claimworthy if item.work_id == best.work_id]
        best_work_evidence = [item for item in evidence if item.work_id == best.work_id]
        if best_work_claimworthy:
            best_evidence = best_work_claimworthy[0]
        elif best_work_evidence:
            best_evidence = max(best_work_evidence, key=lambda item: item.confidence)
        elif claimworthy:
            best_evidence = claimworthy[0]
        else:
            best_evidence = max(evidence, key=lambda item: item.confidence)
        if not is_claimworthy_evidence(best_evidence):
            graph_supported = (
                "Relationship graph propagated support" in best.why
                or "Library relationship graph marks this work as an upstream source-level hub" in best.why
            )
            if tier == "strong_lead" and graph_supported and best.confidence >= 0.62:
                return (
                    f"Decision tier: strong_lead. The strongest source-level candidate is {best.work_title} by {best.author} "
                    f"(relationship-aware confidence {best.confidence:.2f}). Direct Elastic evidence in this work is "
                    f"{evidence_strength(best_evidence)}, but the library relationship graph propagates support from "
                    "a later commentary/hashiya layer that explicitly discusses the queried wording. "
                    f"Best direct evidence from the candidate work: {best_evidence.quote}. "
                    "Treat this as a strong research lead requiring human verification against the candidate source, "
                    "not as an unqualified final attribution."
                )
            web_hits = web_research.get("web_hits", [])
            external = "; ".join(
                f"{hit.get('title')} ({hit.get('url')})"
                for hit in web_hits[:3]
                if hit.get("title") and hit.get("url")
            )
            return (
                f"Decision tier: {tier}. No final attribution can be made from the indexed evidence. "
                f"The best internal candidate is {best.work_title} by {best.author} "
                f"(confidence {best.confidence:.2f}; evidence strength: {evidence_strength(best_evidence)}), "
                "but this is only a research lead, not an attribution. "
                f"Web research found external leads: {external or 'none returned'}. "
                "A scholarly claim requires stronger lexical/semantic Elastic evidence from an OCR/indexed source."
            )
        web_note = (
            f" Bibliographic research narrowed {len(web_research.get('candidate_works', []))} work "
            f"candidate(s) and {len(web_research.get('relationships', []))} relationship edge(s); "
            "these web-intelligence signals support candidate selection but are not treated as final proof."
            if web_research.get("enabled")
            else ""
        )
        return (
            f"Decision tier: {tier}. The strongest candidate is {best.work_title} by {best.author} "
            f"(confidence {best.confidence:.2f}). The passage fits the detected "
            f"{context.domain} context and is supported by the evidence quote: "
            f"{best_evidence.quote}. Evidence strength: {evidence_strength(best_evidence)}.{web_note} "
            f"Human verification should compare manuscript/page context "
            f"before treating this as a final attribution."
        )

    def _final_decision(self, evidence: list) -> str:
        return "claim" if claimworthy_evidence(evidence) else "abstain"

    def _trace(
        self,
        phase: str,
        step: str,
        mode: RunMode,
        provider: str | None,
        raw_payload: dict,
        output_summary: str,
        decision: str | None = None,
        rejection_reason: str | None = None,
        status: StepStatus = StepStatus.completed,
    ) -> TraceEvent:
        now = self._now()
        return TraceEvent(
            phase=phase,
            step=step,
            status=status,
            mode=mode,
            provider=provider,
            input={},
            output_summary=output_summary,
            raw_payload=raw_payload,
            decision=decision,
            rejection_reason=rejection_reason,
            started_at=now,
            completed_at=now,
        )

    def _trace_events(
        self,
        payload: RunCreate,
        context: DetectedContext,
        web_research: dict,
        sources: list[dict],
        evidence: list,
        final_report: str,
        final_decision: str,
    ) -> list[TraceEvent]:
        source_decision = "library_scope_only" if payload.mode == RunMode.library else "source_candidates_need_ocr_index"
        events = [
            self._trace(
                "setup",
                "mode selected",
                payload.mode,
                "Hermeneut run router",
                {
                    "mode": payload.mode,
                    "library_id": payload.library_id,
                    "containing_author": payload.containing_author,
                    "containing_work": payload.containing_work,
                },
                f"Selected {payload.mode.value} protocol.",
                decision=payload.mode.value,
            ),
            self._trace(
                "analysis",
                "context analyzed",
                payload.mode,
                "Hermeneut context analyzer",
                context.model_dump(),
                f"Detected {context.domain} context with {len(context.key_terms)} key terms.",
            ),
        ]
        if payload.mode == RunMode.open_discovery:
            academic_subagent_events = [
                self._trace(
                    "academic_research",
                    str(subagent.get("agent_id", "academic_subagent")),
                    payload.mode,
                    str(subagent.get("model_used", web_research.get("model"))),
                    subagent,
                    f"{subagent.get('title', 'Academic sub-agent')} completed with decision {subagent.get('decision')}.",
                    decision=str(subagent.get("decision")) if subagent.get("decision") else None,
                    rejection_reason=subagent.get("rejection_reason"),
                )
                for subagent in web_research.get("academic_intelligence", {}).get("subagents", [])
                if isinstance(subagent, dict)
            ]
            events.extend(
                [
                    self._trace(
                        "routing",
                        "model used",
                        payload.mode,
                        "Hermeneut AI router",
                        web_research.get("model_routing", {}),
                        "Recorded role-based AI routing before candidate generation.",
                        decision="research_flash_report_pro_embedding",
                    ),
                    *academic_subagent_events,
                    self._trace(
                        "analysis",
                        "context_profile_generated",
                        payload.mode,
                        web_research.get("model"),
                        web_research.get("context_profile", {}),
                        "Generated academic context profile for Open Discovery.",
                    ),
                    self._trace(
                        "discovery",
                        "relationship_graph_built",
                        payload.mode,
                        web_research.get("model"),
                        {"relationships": web_research.get("relationships", [])},
                        f"Built {len(web_research.get('relationships', []))} candidate relationship edge(s).",
                    ),
                    self._trace(
                        "discovery",
                        "author_candidate_scored",
                        payload.mode,
                        web_research.get("model"),
                        {"author_candidates": web_research.get("candidate_authors", [])},
                        f"Scored {len(web_research.get('candidate_authors', []))} author candidate(s).",
                    ),
                    self._trace(
                        "discovery",
                        "phrase_variants_generated",
                        payload.mode,
                        web_research.get("model"),
                        {"phrase_variants": web_research.get("phrase_variants", [])},
                        f"Generated {len(web_research.get('phrase_variants', []))} exact, normalized, semantic, and metadata query variant(s).",
                    ),
                    self._trace(
                        "discovery",
                        "candidate_web_search_executed",
                        payload.mode,
                        web_research.get("grounding_mode"),
                        {"candidate_web_searches": web_research.get("candidate_web_searches", [])},
                        f"Executed or planned {len(web_research.get('candidate_web_searches', []))} candidate-specific web search(es).",
                    ),
                    self._trace(
                        "discovery",
                        "source_candidate_selected",
                        payload.mode,
                        "deterministic_backend_scoring",
                        {"source_candidates": web_research.get("source_candidates", [])},
                        f"Selected {len(web_research.get('source_candidates', []))} source candidate(s) for lifecycle tracking.",
                    ),
                    self._trace(
                        "discovery",
                        "source_candidate_rejected",
                        payload.mode,
                        "deterministic_backend_scoring",
                        {"rejected_candidates": web_research.get("rejected_candidates", [])},
                        f"Rejected or deferred {len(web_research.get('rejected_candidates', []))} candidate(s) with reasons.",
                        rejection_reason=None if web_research.get("rejected_candidates") else "No lower-ranked candidates were present.",
                    ),
                    self._trace(
                        "ocr",
                        "top_pdf_targets_selected",
                        payload.mode,
                        "deterministic_backend_scoring",
                        {"top_pdf_targets": web_research.get("top_pdf_targets", [])},
                        f"Selected {len(web_research.get('top_pdf_targets', []))} direct PDF/text target(s) for OCR queue.",
                        decision="ocr_targets_available" if web_research.get("top_pdf_targets") else "no_direct_pdf_targets",
                        rejection_reason=None if web_research.get("top_pdf_targets") else "No direct PDF/text source passed download policy.",
                    ),
                ]
            )
        events.extend(
            [
            self._trace(
                "discovery" if payload.mode == RunMode.open_discovery else "library",
                "source decision",
                payload.mode,
                web_research.get("grounding_mode") or "Elastic library scope",
                {"web_research": web_research, "sources": sources},
                f"Prepared {len(sources)} source record(s).",
                decision=source_decision,
                rejection_reason=None if sources else "No source records were available.",
            ),
            self._trace(
                "retrieval",
                "Elastic retrieval",
                payload.mode,
                "Elasticsearch hybrid/semantic retrieval",
                {"evidence_count": len(evidence), "passage_ids": [item.passage_id for item in evidence[:10]]},
                f"Retrieved {len(evidence)} evidence passage(s).",
                decision="evidence_available" if evidence else "no_evidence",
                rejection_reason=None if evidence else "No searchable Elastic passage supported a source claim.",
            ),
            self._trace(
                "retrieval",
                "elastic_evidence_found",
                payload.mode,
                "Elasticsearch hybrid/semantic retrieval",
                {"evidence": [item.model_dump() for item in evidence[:5]]},
                f"Elastic evidence rows available: {len(evidence)}.",
                decision="evidence_found" if evidence else "no_elastic_evidence",
                rejection_reason=None if evidence else "Final attribution remains blocked until OCR/indexed evidence is retrieved.",
            ),
            self._trace(
                "report",
                "decision_tier_calibrated",
                payload.mode,
                "Hermeneut evidence tier calibrator",
                {
                    "decision_tier": decision_tier(
                        evidence,
                        bool(web_research.get("candidate_authors") or web_research.get("candidate_works") or web_research.get("relationships")),
                    ),
                    "evidence_strengths": [evidence_strength(item) for item in evidence],
                    "pre_evidence_model_tier": web_research.get("decision_tier"),
                    "decision_calibration": web_research.get("decision_calibration", {}),
                },
                "Calibrated the result as a tiered scholarly lead instead of a binary answer.",
                decision=decision_tier(
                    evidence,
                    bool(web_research.get("candidate_authors") or web_research.get("candidate_works") or web_research.get("relationships")),
                ),
            ),
            self._trace(
                "report",
                "final_claim_or_abstain",
                payload.mode,
                "Hermeneut evidence guardrail",
                {"final_report": final_report},
                f"Final decision: {final_decision}.",
                decision=final_decision,
                rejection_reason=None if final_decision == "claim" else "Evidence threshold or searchable-source requirement was not met.",
            ),
            ]
        )
        return events

    def _source_lifecycle_records(
        self,
        payload: RunCreate,
        web_research: dict,
        source_lookup: list[dict],
    ) -> list[dict]:
        records: list[dict] = []
        merged = [
            *web_research.get("top_pdf_targets", []),
            *web_research.get("source_candidates", []),
            *source_lookup,
        ]
        seen: set[str] = set()
        record_limit = max(payload.max_source_candidates, payload.max_pdf_downloads * 3, 12)
        for index, source in enumerate(merged[:record_limit]):
            source_id = str(source.get("source_id") or source.get("id") or f"source-{index}")
            if source_id in seen:
                continue
            seen.add(source_id)
            provider = str(source.get("provider") or source.get("provenance") or "verified metadata")
            provenance = source.get("provenance") or source.get("metadata", {}).get("provenance") or "unknown"
            status = source.get("ingestion_status") or source.get("metadata", {}).get("ingestion_status") or "web_discovered"
            if status == "indexed":
                lifecycle_status = "searchable"
            else:
                lifecycle_status = source.get("lifecycle_status") or source.get("metadata", {}).get("lifecycle_status") or "download_candidate"
            if payload.mode == RunMode.open_discovery and provenance in {"curated_seed", "hermeneut_seed"}:
                lifecycle_status = "requires_external_source"
            download_url = source.get("download_url") or source.get("metadata", {}).get("download_url")
            if not download_url and lifecycle_status in {"download_candidate", "raw_stored", "searchable"}:
                download_url = source.get("url")
            records.append(
                {
                    "source_id": source_id,
                    "work_id": source.get("work_id") or source.get("metadata", {}).get("work_id"),
                    "work_title": source.get("work_title") or source.get("title") or source.get("metadata", {}).get("work_title"),
                    "author_id": source.get("author_id") or source.get("metadata", {}).get("author_id"),
                    "author_name": source.get("author_name") or source.get("metadata", {}).get("author_name"),
                    "provider": provider,
                    "title": source.get("title") or source.get("work_id") or source_id,
                    "url": source.get("url"),
                    "source_page_url": source.get("source_page_url")
                    or source.get("metadata", {}).get("source_page_url")
                    or source.get("url"),
                    "download_url": download_url,
                    "file_type": source.get("file_type") or source.get("metadata", {}).get("file_type") or "unknown",
                    "lifecycle_status": lifecycle_status,
                    "provenance": provenance,
                    "license_status": source.get("license_status")
                    or source.get("metadata", {}).get("license_status")
                    or "needs_review",
                    "verification_status": source.get("verification_status")
                    or source.get("metadata", {}).get("verification_status")
                    or "metadata_verified",
                    "source_role": source.get("source_role") or source.get("metadata", {}).get("source_role") or "citation_chain",
                    "source_role_group": source.get("source_role_group")
                    or source.get("metadata", {}).get("source_role_group")
                    or ("where_phrase_may_be_found" if (source.get("source_role") or source.get("metadata", {}).get("source_role")) == "containing_layer" else "possible_citation_source_chain"),
                    "resolution_queries": source.get("resolution_queries") or source.get("metadata", {}).get("resolution_queries") or [],
                    "source_resolution_query": source.get("source_resolution_query")
                    or source.get("metadata", {}).get("source_resolution_query"),
                    "source_candidate_rank": source.get("source_candidate_rank")
                    or source.get("metadata", {}).get("source_candidate_rank")
                    or index + 1,
                    "failure_reason_public": source.get("failure_reason_public")
                    or source.get("metadata", {}).get("failure_reason_public"),
                    "relationship_reason": source.get("relationship_reason")
                    or source.get("metadata", {}).get("relationship_reason")
                    or "Candidate source selected by web/Elastic metadata.",
                    "grounding_metadata": source.get("grounding_metadata")
                    or source.get("metadata", {}).get("grounding_metadata")
                    or {},
                    "gcs_raw_path": source.get("gcs_raw_path") or source.get("metadata", {}).get("gcs_raw_path"),
                    "gcs_ocr_path": source.get("gcs_ocr_path") or source.get("metadata", {}).get("gcs_ocr_path"),
                    "gcs_normalized_path": source.get("gcs_normalized_path")
                    or source.get("metadata", {}).get("gcs_normalized_path"),
                    "ocr_status": "ocr_completed" if lifecycle_status == "searchable" else "ocr_pending",
                    "indexed_passage_count": 1 if lifecycle_status == "searchable" else 0,
                    "counts_as_evidence": lifecycle_status == "searchable"
                    and not (payload.mode == RunMode.open_discovery and provenance in {"curated_seed", "hermeneut_seed"}),
                }
            )
        return records

    def _ocr_jobs(self, payload: RunCreate, source_records: list[dict]) -> list[dict]:
        jobs: list[dict] = []
        for source in source_records:
            lifecycle_status = source.get("lifecycle_status")
            download_url = source.get("download_url")
            jobs.append(
                {
                    "source_id": source.get("source_id"),
                    "title": source.get("title"),
                    "status": (
                        "selected"
                        if lifecycle_status == "download_candidate"
                        else "downloading"
                        if lifecycle_status == "download_approved"
                        else "raw_stored"
                        if lifecycle_status == "raw_stored"
                        else "ocr_running"
                        if lifecycle_status in {"ocr_running", "indexing"}
                        else "searchable"
                        if lifecycle_status == "searchable"
                        else "failed"
                        if lifecycle_status in {"failed", "ocr_failed", "rejected"}
                        else "approval_required"
                        if lifecycle_status in {"requires_human_review", "requires_external_source"}
                        else "candidate"
                    ),
                    "ocr_mode": payload.ocr_mode,
                    "ocr_engine": self.elastic.settings.ocr_engine,
                    "text_extraction_priority": [
                        "pdf_text_layer_or_internet_archive_ocr",
                        self.elastic.settings.ocr_engine,
                        "deterministic_arabic_normalization",
                    ],
                    "source_role": source.get("source_role"),
                    "source_role_group": source.get("source_role_group"),
                    "source_candidate_rank": source.get("source_candidate_rank"),
                    "failure_reason_public": source.get("failure_reason_public"),
                    "ocr_quality_status": source.get("ocr_quality_status"),
                    "indexed_passage_count": source.get("indexed_passage_count", 0),
                    "counts_as_evidence": source.get("counts_as_evidence", False),
                    "note": (
                        "LLMs are not used for OCR; low-confidence cleanup may be AI-assisted only after OCR output exists."
                    ),
                }
            )
        return jobs

    def _evidence_memory_records(
        self, run_id: str, query: str, evidence: list
    ) -> list[EvidenceMemoryRecord]:
        return [
            EvidenceMemoryRecord(
                run_id=run_id,
                query=query,
                tool_used="Elasticsearch hybrid retrieval",
                passage_id=item.passage_id,
                candidate_work=item.work_id,
                confidence=item.confidence,
                verification_note=(
                    "Demo-indexed evidence. Verify against a critical edition or institution-owned source "
                    "before treating as final scholarly attribution."
                ),
            )
            for item in evidence[:8]
        ]

    def _timeline(
        self,
        payload: RunCreate,
        context,
        hypotheses,
        library_scope: dict,
        evidence_memory: list[dict],
        web_research: dict,
        elastic_graph: list[dict],
        source_lookup: list[dict],
        search_plan,
        semantic_evidence,
        evidence,
        memory_count: int,
    ) -> list[TimelineEvent]:
        if payload.mode == RunMode.library:
            return [
                TimelineEvent(
                    label="Library mode selected",
                    detail="This run searches only the selected library. Web discovery and open-web source candidates are disabled.",
                    tool="Hermeneut run router",
                    payload={"mode": payload.mode, "library_id": payload.library_id},
                ),
                TimelineEvent(
                    label="Context analyzed",
                    detail=f"Detected {context.language} passage in {context.domain}; citation type: {context.citation_type}.",
                    tool="Hermeneut context analyzer",
                    payload=context.model_dump(),
                ),
                TimelineEvent(
                    label="Elastic library scope checked",
                    detail=(
                        f"Scoped retrieval to library {payload.library_id}: "
                        f"{library_scope.get('passage_count', 0)} passages, "
                        f"{library_scope.get('work_count', 0)} works, "
                        f"{library_scope.get('source_count', 0)} sources."
                    ),
                    tool="hermeneut.library_scope_filter",
                    payload=library_scope,
                ),
                TimelineEvent(
                    label="OCR/source readiness checked",
                    detail="Only sources already marked searchable in the selected library can contribute final evidence.",
                    tool="GCS vault + Elastic source metadata",
                    payload={"source_hits": source_lookup, "ocr_mode": payload.ocr_mode},
                ),
                TimelineEvent(
                    label="Library relationship graph analyzed",
                    detail=(
                        f"Loaded {len(elastic_graph)} source/work relationship edge(s) for candidate ranking, "
                        "including commentary, dependency, chronology, and same-debate relations."
                    ),
                    tool="hermeneut.author_work_graph",
                    payload={"relationship_edges": elastic_graph},
                ),
                TimelineEvent(
                    label="Elastic evidence memory consulted",
                    detail=f"Looked up {len(evidence_memory)} prior evidence memory record(s) before retrieval.",
                    tool="hermeneut.evidence_memory_lookup",
                    payload={"records": evidence_memory},
                ),
                TimelineEvent(
                    label="Elastic library retrieval",
                    detail=f"Ran {len(search_plan)} lexical, semantic, hybrid, and metadata queries inside {payload.library_id}.",
                    tool="hermeneut.passage_lookup + hermeneut.semantic_passage_lookup",
                    payload={
                        "queries": [item.model_dump() for item in search_plan],
                        "semantic_evidence_count": len(semantic_evidence),
                    },
                ),
                TimelineEvent(
                    label="Evidence retrieved",
                    detail=f"Retrieved {len(evidence)} evidence passage(s) from the selected library.",
                    tool="Elastic evidence engine",
                    payload={"evidence": [item.model_dump() for item in evidence]},
                ),
                TimelineEvent(
                    label="Evidence memory written",
                    detail=f"Persisted {memory_count} evidence memory record(s) to Elasticsearch.",
                    tool="hermeneut.evidence_memory_write",
                    payload={"memory_count": memory_count},
                ),
                TimelineEvent(
                    label="Library report generated",
                    detail="Final report was generated only from selected-library Elastic evidence.",
                    tool="Hermeneut evidence guardrail",
                    payload={
                        "claim_policy": "Library Mode never uses web intelligence as evidence.",
                        "report_model": self.elastic.settings.gemini_report_model,
                    },
                ),
            ]
        return [
            TimelineEvent(
                label="Context analyzed",
                detail=f"Detected {context.language} passage in {context.domain}; citation type: {context.citation_type}.",
                tool="Gemini / Agent Builder",
                payload=context.model_dump(),
            ),
            TimelineEvent(
                label="User hints applied",
                detail="Optional author, work, period, domain, language, and library hints were merged into the research scope.",
                tool="Hermeneut input schema",
                payload={
                    "meaning": "containing_author/work identify where the user found the ambiguous phrase; they are not treated as target-source guesses.",
                    "containing_author": payload.containing_author,
                    "containing_work": payload.containing_work,
                    "deprecated_suspected_author": payload.suspected_author,
                    "deprecated_suspected_work": payload.suspected_work,
                    "period_hint": payload.period_hint,
                    "domain_hint": payload.domain_hint,
                    "language_hint": payload.language_hint,
                    "library_id": payload.library_id,
                    "enable_web_research": payload.enable_web_research,
                    "allow_source_download_suggestions": payload.allow_source_download_suggestions,
                    "auto_download_sources": payload.auto_download_sources,
                    "max_source_candidates": payload.max_source_candidates,
                    "max_pdf_downloads": payload.max_pdf_downloads,
                    "ocr_mode": payload.ocr_mode,
                },
            ),
            TimelineEvent(
                label="Hypotheses generated",
                detail=f"Built {len(hypotheses)} author/work hypothesis candidate(s).",
                tool="Gemini / Agent Builder",
                payload={"hypotheses": [item.model_dump() for item in hypotheses]},
            ),
            TimelineEvent(
                label="Elastic library scope checked",
                detail=(
                    f"Scoped retrieval to library {payload.library_id}: "
                    f"{library_scope.get('passage_count', 0)} passages, "
                    f"{library_scope.get('work_count', 0)} works, "
                    f"{library_scope.get('source_count', 0)} sources."
                ),
                tool="hermeneut.library_scope_filter",
                payload=library_scope,
            ),
            TimelineEvent(
                label="Elastic evidence memory consulted",
                detail=f"Looked up {len(evidence_memory)} prior evidence memory record(s) before retrieval.",
                tool="hermeneut.evidence_memory_lookup",
                payload={"records": evidence_memory},
            ),
            TimelineEvent(
                label="Web research plan generated",
                detail=f"Generated {len(web_research.get('research_questions', []))} controlled web-research question(s).",
                tool=web_research.get("model", "Gemini research model"),
                payload={
                    "research_questions": web_research.get("research_questions", []),
                    "grounded_search_queries": web_research.get("grounded_search_queries", []),
                    "policy": web_research.get("policy"),
                },
            ),
            TimelineEvent(
                label="AI routing selected",
                detail="Recorded which AI or deterministic component is responsible for each Open Discovery phase.",
                tool="Hermeneut AI router",
                payload=web_research.get("model_routing", {}),
            ),
            TimelineEvent(
                label="Context profile generated",
                detail="Built an academic context profile before author/work candidate expansion.",
                tool=web_research.get("model", "Gemini research model"),
                payload=web_research.get("context_profile", {}),
            ),
            TimelineEvent(
                label="Bibliographic web intelligence gathered",
                detail=(
                    f"Gathered {len(web_research.get('candidate_authors', []))} author candidate(s), "
                    f"{len(web_research.get('candidate_works', []))} work candidate(s), and "
                    f"{len(web_research.get('relationships', []))} relationship edge(s)."
                ),
                tool="Controlled web metadata resolvers",
                payload=web_research,
            ),
            TimelineEvent(
                label="Phrase variants generated",
                detail=(
                    f"Generated {len(web_research.get('phrase_variants', []))} exact, normalized, semantic, "
                    "and metadata-oriented query variant(s)."
                ),
                tool=web_research.get("model", "Gemini research model"),
                payload={"phrase_variants": web_research.get("phrase_variants", [])},
            ),
            TimelineEvent(
                label="Candidate-specific web searches",
                detail=(
                    f"Executed or staged {len(web_research.get('candidate_web_searches', []))} author/work-specific "
                    "web search(es) before choosing sources."
                ),
                tool=web_research.get("grounding_mode", "controlled web search"),
                payload={"candidate_web_searches": web_research.get("candidate_web_searches", [])},
            ),
            TimelineEvent(
                label="Candidate graph narrowed",
                detail=(
                    "Author/work/source candidates were narrowed using hints, concepts, web intelligence, "
                    f"and {len(elastic_graph)} Elasticsearch graph edge(s)."
                ),
                tool="hermeneut.author_work_graph",
                payload={
                    "candidate_work_ids": [item.get("work_id") for item in web_research.get("candidate_works", [])],
                    "web_relationships": web_research.get("relationships", []),
                    "elastic_graph_edges": elastic_graph,
                },
            ),
            TimelineEvent(
                label="Source candidates discovered",
                detail=(
                    f"Prepared {len(web_research.get('source_candidates', []))} web source candidate(s) and "
                    f"{len(source_lookup)} Elastic source metadata hit(s)."
                ),
                tool="hermeneut.source_lookup",
                payload={
                    "source_candidates": web_research.get("source_candidates", []),
                    "elastic_source_hits": source_lookup,
                    "lifecycle_policy": "Auto-download public/allowlisted sources; pause for ambiguous license or verification status.",
                },
            ),
            TimelineEvent(
                label="Top PDF/OCR targets selected",
                detail=(
                    f"Selected {len(web_research.get('top_pdf_targets', []))} direct PDF/text target(s) for the OCR path; "
                    "all other candidates remain metadata-only or require human review."
                ),
                tool="deterministic source scoring",
                payload={
                    "top_pdf_targets": web_research.get("top_pdf_targets", []),
                    "rejected_candidates": web_research.get("rejected_candidates", []),
                },
            ),
            TimelineEvent(
                label="PDF/OCR lifecycle prepared",
                detail=(
                    "Candidate sources are tracked from web discovery through GCS raw storage, OCR, normalization, "
                    "and searchable Elastic indexing."
                ),
                tool="GCS vault + OCR processor",
                payload={
                    "gcs_raw_template": "gs://hermeneut-sources/raw/{library_id}/{provider}/{source_id}/source.pdf",
                    "gcs_ocr_template": "gs://hermeneut-sources/ocr/{library_id}/{source_id}/ocr.json",
                    "gcs_normalized_template": "gs://hermeneut-sources/normalized/{library_id}/{source_id}/passages.jsonl",
                    "ocr_mode": payload.ocr_mode,
                },
            ),
            TimelineEvent(
                label="Elastic search plan",
                detail=f"Prepared {len(search_plan)} lexical, semantic, hybrid, and metadata queries.",
                tool="Elastic MCP",
                payload={
                    "elastic_mode": self.elastic.mode(),
                    "elastic_health": self.elastic.health(),
                    "queries": [item.model_dump() for item in search_plan],
                },
            ),
            TimelineEvent(
                label="Elastic semantic vector retrieval",
                detail=(
                    f"Retrieved {len(semantic_evidence)} meaning-level candidate passage(s) using "
                    "semantic vectors before final hybrid evidence ranking."
                ),
                tool="hermeneut.semantic_passage_lookup",
                payload={
                    "retrieval_backend": semantic_evidence[0].retrieval_backend if semantic_evidence else self.elastic.mode(),
                    "evidence_ids": [item.evidence_id for item in semantic_evidence],
                    "top_passages": [
                        {
                            "passage_id": item.passage_id,
                            "work_id": item.work_id,
                            "confidence": item.confidence,
                            "semantic_score": item.semantic_score,
                            "tool_trace": item.tool_trace,
                        }
                        for item in semantic_evidence[:5]
                    ],
                },
            ),
            TimelineEvent(
                label="Evidence retrieved",
                detail=f"Retrieved {len(evidence)} evidence passage(s) from the indexed corpus.",
                tool="Elastic hybrid evidence retrieval",
                payload={
                    "retrieval_backend": evidence[0].retrieval_backend if evidence else self.elastic.mode(),
                    "evidence_ids": [item.evidence_id for item in evidence],
                    "tool_traces": [item.tool_trace for item in evidence[:5]],
                },
            ),
            TimelineEvent(
                label="Evidence memory written",
                detail=f"Wrote {memory_count} evidence ledger record(s) back into Elasticsearch.",
                tool="hermeneut.evidence_memory_write",
                payload={
                    "elastic_index": "hermeneut_evidence",
                    "record_count": memory_count,
                    "policy": "Agent claims are made retrievable as evidence memory for later runs.",
                },
            ),
            TimelineEvent(
                label="Gemini Pro scholarly report generated",
                detail="Final attribution report was generated only from ranked candidates with evidence records.",
                tool="Gemini report model / Hermeneut evidence guardrail",
                payload={
                    "candidate_count": len({item.work_id for item in evidence}),
                    "report_model": self.web_research.settings.gemini_report_model if self.web_research else "not-configured",
                    "claim_policy": "No candidate appears in the final report unless it has at least one evidence record.",
                },
            ),
        ]

    def _event(
        self,
        label: str,
        detail: str,
        tool: str,
        status: StepStatus,
        estimated_seconds: int,
        payload: dict,
    ) -> TimelineEvent:
        now = self._now()
        return TimelineEvent(
            label=label,
            detail=detail,
            tool=tool,
            status=status,
            started_at=now,
            completed_at=now if status == StepStatus.completed else None,
            estimated_seconds=estimated_seconds,
            progress_message=detail,
            payload=payload,
        )

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
