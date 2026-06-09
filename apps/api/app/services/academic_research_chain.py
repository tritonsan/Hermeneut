from __future__ import annotations

import json
import re
from typing import Any

import google.auth
import httpx
from google.auth.transport.requests import Request

from app.models import DetectedContext, Hypothesis, RunCreate
from app.settings import Settings

GLOBAL_ACADEMIC_RULES = """
You are one specialist sub-agent inside Hermeneut, a research agent for ambiguous references in classical texts.
Work at professor-level bibliographic caution.

Rules:
- Do not guess from memory; generate research intelligence that must later be verified.
- Treat containing_author/containing_work as the place where the ambiguous phrase was found, not as the target source.
- Do not privilege commentary/hashiya unless the input context actually indicates it.
- Consider non-commentary genres: polemic, refutation, doxography, legal/theological report, philosophical argument,
  school formula, mystical saying, transmitted maxim, anthology, and parallel formulation.
- Every candidate or decision needs a scholarly reason and an uncertainty note.
- Do not become paralyzed by uncertainty. Produce a usable research result tier: confirmed, probable,
  strong_lead, weak_lead, or no_result. A tier is not final attribution unless OCR/indexed Elastic evidence later confirms it.
- Web hits, model reasoning, and catalog metadata are candidate intelligence only. Final attribution requires OCR/indexed
  Elastic passage evidence.
- Return strict JSON only. No markdown.
"""


SUBAGENT_SPECS: dict[str, dict[str, Any]] = {
    "context_scholar": {
        "title": "Context Scholar",
        "task": (
            "Analyze the passage itself. Identify discipline, technical terms, argumentative problem, likely genre, "
            "period clues, and the kind of ambiguous attribution involved."
        ),
        "schema": {
            "discipline": "string",
            "technical_terms": ["string"],
            "genre_hypothesis": "string",
            "argumentative_problem": "string",
            "period_hypothesis": "string",
            "attribution_type": "anonymous_report|paraphrase|polemical_summary|technical_formula|quotation|unknown",
            "non_commentary_possibilities": ["string"],
            "uncertainty_notes": ["string"],
        },
    },
    "relationship_scholar": {
        "title": "Relationship Scholar",
        "task": (
            "Use the containing author/work only as citation context. Infer what earlier authors, schools, opponents, "
            "authorities, or source traditions this containing text may cite, summarize, refute, inherit, or debate."
        ),
        "schema": {
            "containing_context_interpretation": "string",
            "relationship_questions": [
                {"question": "string", "why_it_matters": "string", "search_priority": "high|medium|low"}
            ],
            "relationship_hypotheses": [
                {
                    "relation": "cites|summarizes|refutes|inherits|debates|reports_school_position|parallel_tradition",
                    "target_kind": "author|work|school|concept|tradition",
                    "target": "string",
                    "reason": "string",
                    "uncertainty": "string",
                }
            ],
        },
    },
    "candidate_scholar": {
        "title": "Candidate Scholar",
        "task": (
            "Build a broad candidate field. Include direct sources, base authorities, polemical opponents, school "
            "authorities, doxographers, commentators only when relevant, transmitters, and parallel-formulation authors."
        ),
        "schema": {
            "author_candidates": [
                {
                    "name": "string",
                    "name_ar": "string",
                    "period": "string",
                    "source_role": (
                        "direct_source|base_authority|polemical_target|school_authority|commentator|doxographer|"
                        "transmitter|parallel_formulation|later_reception"
                    ),
                    "reason": "string",
                    "chronology_fit": 0.0,
                    "domain_fit": 0.0,
                    "relationship_fit": 0.0,
                    "uncertainty": "string",
                }
            ],
            "work_candidates": [
                {
                    "title": "string",
                    "title_ar": "string",
                    "author_name": "string",
                    "source_role": "string",
                    "reason": "string",
                    "priority": 0.0,
                    "uncertainty": "string",
                }
            ],
            "candidate_dossiers": [
                {
                    "candidate": "string",
                    "role": "string",
                    "why_candidate": "string",
                    "relationship_type": "string",
                    "what_would_confirm": "string",
                    "what_would_disconfirm": "string",
                    "uncertainty": "string",
                }
            ],
        },
    },
    "search_strategist": {
        "title": "Search Strategist",
        "task": (
            "Generate candidate-specific searches. Include exact phrase, normalized Arabic, semantic Arabic, title "
            "variants, transliteration, English metadata, repository, catalog, PDF, OCR text, and source-edition queries."
        ),
        "schema": {
            "phrase_variants": [
                {"kind": "exact|normalized|semantic|technical_terms|transliteration|english_metadata", "query": "string", "purpose": "string"}
            ],
            "candidate_search_plan": [
                {
                    "candidate": "string",
                    "query": "string",
                    "target": "Google grounding|Internet Archive|OpenITI|Wikidata|library_catalog|PDF search|OCR text",
                    "expected_signal": "string",
                    "priority": "high|medium|low",
                }
            ],
        },
    },
    "web_evidence_critic": {
        "title": "Web Evidence Critic",
        "task": (
            "Evaluate web hits and grounded results. Decide whether each hit supports a candidate, is metadata-only, "
            "is irrelevant, or needs human review. Do not treat any web hit as final textual evidence."
        ),
        "schema": {
            "web_hit_assessments": [
                {
                    "url": "string",
                    "decision": "supports_candidate|metadata_only|irrelevant|needs_human_review",
                    "reason": "string",
                    "related_candidate": "string",
                    "source_quality": "high|medium|low|unknown",
                    "quoted_signal": "short phrase or snippet that caused the decision; empty if none",
                    "what_it_can_support": "candidate_author|candidate_work|source_availability|nothing_final",
                }
            ],
            "rejection_rules": ["string"],
        },
    },
    "source_selection_judge": {
        "title": "Source Selection Judge",
        "task": (
            "Select the strongest sources for PDF/text download and OCR. Use candidate role, metadata match, title match, "
            "author match, source quality, file availability, license/review status, and whether the source can become "
            "searchable Elastic evidence."
        ),
        "schema": {
            "selected_sources": [
                {
                    "candidate": "string",
                    "work_or_source": "string",
                    "selection_reason": "string",
                    "download_priority": 0.0,
                    "required_verification": "string",
                    "counts_as_evidence_now": False,
                }
            ],
            "rejected_or_deferred_sources": [
                {"candidate": "string", "reason": "string", "next_action": "string"}
            ],
        },
    },
    "decision_calibrator": {
        "title": "Decision Calibrator",
        "task": (
            "Calibrate the current pre-evidence result tier. Do not make final attribution from web intelligence, "
            "but do not hide useful scholarly leads. Explain whether the current state is confirmed, probable, "
            "strong_lead, weak_lead, or no_result, and what evidence would move it up or down."
        ),
        "schema": {
            "decision_tier": "confirmed|probable|strong_lead|weak_lead|no_result",
            "tier_reason": "string",
            "claim_language": "string",
            "strongest_leads": [
                {
                    "candidate": "string",
                    "lead_strength": "confirmed|probable|strong_lead|weak_lead|no_result",
                    "why": "string",
                    "required_next_evidence": "string",
                }
            ],
            "disqualifying_gaps": ["string"],
        },
    },
}


class AcademicResearchChain:
    """LLM-assisted professor-grade discovery chain; deterministic code is only a guardrail."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def run(
        self,
        payload: RunCreate,
        context: DetectedContext,
        hypotheses: list[Hypothesis],
        web_hits: list[dict],
    ) -> dict:
        shared = self._shared_input(payload, context, hypotheses, web_hits)
        subagents: list[dict[str, Any]] = []
        state: dict[str, Any] = {}
        model_assisted = False

        for agent_id in [
            "context_scholar",
            "relationship_scholar",
            "candidate_scholar",
            "search_strategist",
            "web_evidence_critic",
            "source_selection_judge",
            "decision_calibrator",
        ]:
            output = self._run_subagent(agent_id, shared, state)
            model_assisted = model_assisted or bool(output.get("model_assisted"))
            state[agent_id] = output.get("output", {})
            subagents.append(output)

        merged = self._merge_outputs(state)
        merged.update(
            {
                "model_assisted": model_assisted,
                "model_used": self.settings.gemini_research_model,
                "prompt_profile": "professor_grade_multi_agent_discovery_chain_v2",
                "prompt_excerpt": subagents[0].get("prompt_excerpt", "") if subagents else "",
                "subagents": subagents,
            }
        )
        return merged

    def _shared_input(
        self,
        payload: RunCreate,
        context: DetectedContext,
        hypotheses: list[Hypothesis],
        web_hits: list[dict],
    ) -> dict:
        return {
            "passage": payload.passage,
            "context": payload.context,
            "containing_author": payload.containing_author,
            "containing_work": payload.containing_work,
            "period_hint": payload.period_hint,
            "domain_hint": payload.domain_hint or context.domain,
            "detected_context": context.model_dump(),
            "initial_hypotheses": [item.model_dump() for item in hypotheses[:8]],
            "web_hits_to_assess": [
                {
                    "title": hit.get("title"),
                    "url": hit.get("url"),
                    "snippet": hit.get("snippet"),
                    "query": hit.get("query"),
                    "provenance": hit.get("provenance"),
                    "download_url": hit.get("download_url"),
                    "file_type": hit.get("file_type"),
                }
                for hit in web_hits[:10]
            ],
        }

    def _run_subagent(self, agent_id: str, shared: dict, state: dict) -> dict:
        spec = SUBAGENT_SPECS[agent_id]
        prompt = self._prompt(agent_id, spec, shared, state)
        parsed = self._call_gemini(prompt)
        model_assisted = parsed is not None
        output = self._normalize_output(agent_id, parsed or self._fallback_output(agent_id))
        return {
            "agent_id": agent_id,
            "title": spec["title"],
            "model_used": self.settings.gemini_research_model,
            "model_assisted": model_assisted,
            "prompt_profile": f"{agent_id}_v2",
            "prompt_excerpt": prompt[:1800],
            "output": output,
            "decision": self._decision(agent_id, output, model_assisted),
            "rejection_reason": None if model_assisted else "Gemini sub-agent unavailable; fallback output used.",
        }

    def _prompt(self, agent_id: str, spec: dict, shared: dict, state: dict) -> str:
        return (
            GLOBAL_ACADEMIC_RULES.strip()
            + f"\n\nSub-agent: {spec['title']} ({agent_id})\n"
            + f"Task: {spec['task']}\n\n"
            + "Shared input JSON:\n"
            + json.dumps(shared, ensure_ascii=False)
            + "\n\nPrior sub-agent outputs JSON:\n"
            + json.dumps(state, ensure_ascii=False)
            + "\n\nReturn JSON with this shape:\n"
            + json.dumps(spec["schema"], ensure_ascii=False)
        )

    def _call_gemini(self, prompt: str) -> dict | None:
        if not self.settings.google_cloud_project:
            return None
        model_id = self.settings.gemini_research_model.removeprefix("google/")
        location = self.settings.vertex_openai_location or "global"
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        url = (
            f"https://{host}/v1/projects/{self.settings.google_cloud_project}/locations/{location}"
            f"/publishers/google/models/{model_id}:generateContent"
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {
                "temperature": 0.12,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 256},
            },
        }
        try:
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            credentials.refresh(Request())
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {credentials.token}", "Content-Type": "application/json"},
                json=body,
                timeout=35,
            )
            response.raise_for_status()
            text = self._response_text(response.json())
            return self._parse_json(text)
        except Exception:
            return None

    def _response_text(self, data: dict) -> str:
        parts: list[str] = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if part.get("text"):
                    parts.append(part["text"])
        return "\n".join(parts)

    def _parse_json(self, text: str) -> dict | None:
        if not text.strip():
            return None
        cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, flags=re.S)
            if not match:
                return None
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None

    def _normalize_output(self, agent_id: str, output: dict) -> dict:
        schema = SUBAGENT_SPECS[agent_id]["schema"]
        normalized = dict(output)
        for key, value in schema.items():
            if key not in normalized:
                normalized[key] = [] if isinstance(value, list) else "" if isinstance(value, str) else value
        return normalized

    def _fallback_output(self, agent_id: str) -> dict:
        fallbacks = {
            "context_scholar": {
                "discipline": "requires_model_or_web_assessment",
                "technical_terms": [],
                "genre_hypothesis": "undetermined",
                "argumentative_problem": "undetermined",
                "period_hypothesis": "undetermined",
                "attribution_type": "unknown",
                "non_commentary_possibilities": [],
                "uncertainty_notes": ["Sub-agent unavailable; deterministic guardrails may only scaffold search."],
            },
            "relationship_scholar": {
                "containing_context_interpretation": "Containing context could not be model-assessed.",
                "relationship_questions": [],
                "relationship_hypotheses": [],
            },
            "candidate_scholar": {
                "author_candidates": [],
                "work_candidates": [],
                "candidate_dossiers": [],
            },
            "search_strategist": {
                "phrase_variants": [],
                "candidate_search_plan": [],
            },
            "web_evidence_critic": {
                "web_hit_assessments": [],
                "rejection_rules": [
                    "Do not make candidate claims from deterministic fallback alone.",
                    "Require model/web/metadata support plus OCR/indexed Elastic evidence.",
                ],
            },
            "source_selection_judge": {
                "selected_sources": [],
                "rejected_or_deferred_sources": [],
            },
            "decision_calibrator": {
                "decision_tier": "weak_lead",
                "tier_reason": "Fallback chain can only provide a weak research lead before OCR/indexed Elastic evidence.",
                "claim_language": "No attribution claim yet; continue source acquisition and Elastic retrieval.",
                "strongest_leads": [],
                "disqualifying_gaps": ["No model-assisted decision calibration was available."],
            },
        }
        return fallbacks[agent_id]

    def _merge_outputs(self, state: dict[str, dict]) -> dict:
        context = state.get("context_scholar", {})
        relationship = state.get("relationship_scholar", {})
        candidates = state.get("candidate_scholar", {})
        search = state.get("search_strategist", {})
        critic = state.get("web_evidence_critic", {})
        source_judge = state.get("source_selection_judge", {})
        decision = state.get("decision_calibrator", {})
        return {
            "context_profile": context,
            "relationship_questions": relationship.get("relationship_questions", []),
            "relationship_hypotheses": relationship.get("relationship_hypotheses", []),
            "author_candidates": candidates.get("author_candidates", []),
            "work_candidates": candidates.get("work_candidates", []),
            "candidate_dossiers": candidates.get("candidate_dossiers", []),
            "phrase_variants": search.get("phrase_variants", []),
            "candidate_search_plan": search.get("candidate_search_plan", []),
            "web_hit_assessments": critic.get("web_hit_assessments", []),
            "rejection_rules": critic.get("rejection_rules", []),
            "source_selection": source_judge,
            "selected_sources": source_judge.get("selected_sources", []),
            "rejected_or_deferred_sources": source_judge.get("rejected_or_deferred_sources", []),
            "decision_calibration": decision,
            "decision_tier": decision.get("decision_tier", "weak_lead"),
        }

    def _decision(self, agent_id: str, output: dict, model_assisted: bool) -> str:
        if not model_assisted:
            return "fallback_guardrail_only"
        if agent_id == "source_selection_judge":
            return "source_targets_ranked" if output.get("selected_sources") else "no_source_targets_selected"
        if agent_id == "decision_calibrator":
            return str(output.get("decision_tier") or "weak_lead")
        if agent_id == "web_evidence_critic":
            return "web_hits_assessed" if output.get("web_hit_assessments") else "no_web_hits_to_assess"
        return "academic_intelligence_generated"
