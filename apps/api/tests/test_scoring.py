from app.models import EvidenceItem
from app.services.scoring import (
    confidence_score,
    decision_tier,
    evidence_strength,
    is_claimworthy_evidence,
)


def test_confidence_score_uses_weighted_components():
    assert confidence_score(1, 1, 1, 1, 1) == 1
    assert confidence_score(0, 0, 0, 0, 0) == 0
    assert confidence_score(0.5, 0.5, 0.5, 0.5, 0.5) == 0.5


def test_weak_contextual_evidence_is_not_claimworthy():
    item = EvidenceItem(
        evidence_id="e1",
        passage_id="p1",
        work_id="w1",
        match_type="semantic",
        quote="contextual overlap only",
        lexical_score=0.02,
        semantic_score=0.7,
        metadata_score=0.5,
        citation_context_score=0.5,
        source_quality_score=0.8,
        confidence=0.5,
        explanation="Weak contextual lead.",
    )

    assert evidence_strength(item) == "weak"
    assert not is_claimworthy_evidence(item)


def test_moderate_textual_evidence_is_claimworthy():
    item = EvidenceItem(
        evidence_id="e2",
        passage_id="p2",
        work_id="w2",
        match_type="hybrid",
        quote="close phrase overlap",
        lexical_score=0.22,
        semantic_score=0.78,
        metadata_score=0.7,
        citation_context_score=0.7,
        source_quality_score=0.8,
        confidence=0.66,
        explanation="Moderate textual lead.",
    )

    assert evidence_strength(item) == "moderate"
    assert is_claimworthy_evidence(item)


def test_decision_tier_keeps_useful_leads_without_overclaiming():
    assert decision_tier([], has_candidate_leads=False) == "no_result"
    assert decision_tier([], has_candidate_leads=True) == "weak_lead"

    weak_item = EvidenceItem(
        evidence_id="e3",
        passage_id="p3",
        work_id="w3",
        match_type="semantic",
        quote="weak but relevant source-layer overlap",
        lexical_score=0.08,
        semantic_score=0.7,
        metadata_score=0.55,
        citation_context_score=0.55,
        source_quality_score=0.8,
        relationship_fit_score=0.7,
        confidence=0.52,
        explanation="Weak evidence with meaningful relationship support.",
    )

    assert decision_tier([weak_item], has_candidate_leads=True) == "strong_lead"


def test_weak_ocr_gibberish_cannot_be_claimworthy():
    item = EvidenceItem(
        evidence_id="e4",
        passage_id="p4",
        work_id="w4",
        match_type="hybrid",
        quote="abc@@@### " * 8,
        lexical_score=0.8,
        semantic_score=0.9,
        metadata_score=0.8,
        citation_context_score=0.8,
        source_quality_score=0.8,
        confidence=0.92,
        ocr_confidence=0.42,
        ocr_quality_status="weak_ocr_needs_manual_review",
        explanation="Noisy OCR should not promote a source lead.",
    )

    assert evidence_strength(item) == "weak"
    assert not is_claimworthy_evidence(item)


def test_high_confidence_noisy_quote_cannot_be_claimworthy_even_without_label():
    item = EvidenceItem(
        evidence_id="e5",
        passage_id="p5",
        work_id="w5",
        match_type="hybrid",
        quote="سسسسسسسسس @@@@ #### xxxxx%%%%% yyyyy!!!!! " * 6,
        lexical_score=0.8,
        semantic_score=0.9,
        metadata_score=0.8,
        citation_context_score=0.8,
        source_quality_score=0.8,
        confidence=0.92,
        ocr_confidence=0.9,
        explanation="Noisy OCR should fail readability even with high OCR confidence.",
    )

    assert evidence_strength(item) == "weak"
    assert decision_tier([item], has_candidate_leads=True) == "strong_lead"
    assert not is_claimworthy_evidence(item)
