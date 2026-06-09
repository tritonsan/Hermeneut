from app.models import RunCreate, RunMode
from app.services.agent import ResearchAgent
from app.services.elastic_service import ElasticService
from app.services.web_research import WebResearchService
from app.settings import Settings


def test_library_agent_run_contains_scoped_evidence_and_trace():
    settings = Settings()
    agent = ResearchAgent(ElasticService(settings), WebResearchService(settings))
    run = agent.run(
        RunCreate(
            mode=RunMode.library,
            passage="قيل إن ما كان وجوده من غيره فهو ممكن",
            period_hint="5th/11th century",
            domain_hint="falsafa",
        )
    )
    assert run.run_id.startswith("run-")
    assert run.mode == RunMode.library
    assert run.detected_context.language == "ar"
    assert run.search_plan
    assert run.evidence
    assert run.candidates
    assert any(event.label == "Library mode selected" for event in run.timeline)
    assert not any(event.label == "Bibliographic web intelligence gathered" for event in run.timeline)
    assert any(event.label == "Elastic library scope checked" for event in run.timeline)
    assert any(event.label == "Elastic evidence memory consulted" for event in run.timeline)
    assert any(event.label == "Elastic library retrieval" for event in run.timeline)
    assert any(event.label == "Evidence memory written" for event in run.timeline)
    assert any(event.step == "mode selected" and event.mode == RunMode.library for event in run.trace_events)
    assert run.evidence[0].retrieval_mode in {"hybrid", "semantic_vector"}
    assert run.evidence[0].model_trace["research_model"] == "google/gemini-3.1-flash-lite"
    assert run.decision_tier in {"confirmed", "probable", "strong_lead", "weak_lead", "no_result"}
    assert any(event.step == "decision_tier_calibrated" for event in run.trace_events)
    assert "strongest candidate" in run.final_report or "No final attribution" in run.final_report


def test_open_discovery_does_not_claim_without_searchable_source():
    settings = Settings()
    agent = ResearchAgent(ElasticService(settings), WebResearchService(settings))
    run = agent.run(
        RunCreate(
            mode=RunMode.open_discovery,
            passage="قيل ان زيدا ممكن وصدق زيد موجود بالامكان الخاص",
            domain_hint="logic/philosophy",
        )
    )

    assert run.mode == RunMode.open_discovery
    assert any(event.label == "Bibliographic web intelligence gathered" for event in run.timeline)
    assert run.evidence == []
    assert run.candidates == []
    assert run.blocked_reason
    assert run.context_profile
    assert run.author_candidates
    assert run.phrase_variants
    assert run.candidate_web_searches
    assert run.work_candidates
    assert run.ocr_jobs
    assert run.decision_tier == "weak_lead"
    assert "Decision tier: weak_lead" in run.final_report
    assert any(
        event.step == "decision_tier_calibrated" and event.decision == "weak_lead"
        for event in run.trace_events
    )
    assert any(event.step == "model used" for event in run.trace_events)
    assert any(event.step == "author_candidate_scored" for event in run.trace_events)
    assert any(event.step == "phrase_variants_generated" for event in run.trace_events)
    assert any(event.step == "top_pdf_targets_selected" for event in run.trace_events)
    assert any(event.step == "final_claim_or_abstain" and event.decision == "abstain" for event in run.trace_events)
