from dataclasses import dataclass

from app.models import EvidenceItem
from app.services.ocr_quality import WEAK_OCR, ocr_text_is_readable


@dataclass(frozen=True)
class EvidenceDecision:
    tier: str
    evidence_strength: str
    claimworthy: bool
    reason: str
    best_evidence: EvidenceItem | None = None


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def confidence_score(
    lexical_score: float,
    semantic_score: float,
    metadata_score: float,
    citation_context_score: float,
    source_quality_score: float,
    relationship_fit_score: float | None = None,
) -> float:
    if relationship_fit_score is None:
        score = (
            clamp(lexical_score) * 0.25
            + clamp(semantic_score) * 0.30
            + clamp(metadata_score) * 0.20
            + clamp(citation_context_score) * 0.15
            + clamp(source_quality_score) * 0.10
        )
    else:
        score = (
            clamp(lexical_score) * 0.20
            + clamp(semantic_score) * 0.28
            + clamp(metadata_score) * 0.18
            + clamp(citation_context_score) * 0.12
            + clamp(source_quality_score) * 0.10
            + clamp(relationship_fit_score) * 0.12
        )
    return round(score, 3)


def best_evidence_confidence(items: list[EvidenceItem]) -> float:
    if not items:
        return 0.0
    return max(item.confidence for item in items)


def evidence_strength(item: EvidenceItem) -> str:
    """Classify evidence so contextual hits cannot masquerade as attribution proof."""
    if not _quote_quality_allows_claim(item):
        if item.confidence >= 0.45 and item.lexical_score >= 0.22:
            return "weak"
        return "contextual"
    if item.confidence >= 0.78 and item.lexical_score >= 0.3:
        return "strong"
    if item.confidence >= 0.62 and (
        item.lexical_score >= 0.18 or (item.semantic_score >= 0.82 and item.metadata_score >= 0.6)
    ):
        return "moderate"
    if item.confidence >= 0.45:
        return "weak"
    return "contextual"


def _quote_quality_allows_claim(item: EvidenceItem) -> bool:
    quote = (item.quote or "").strip()
    if not quote:
        return False
    if item.ocr_quality_status == WEAK_OCR:
        return False
    if item.ocr_confidence is not None and item.ocr_confidence < 0.55:
        return False
    return ocr_text_is_readable(quote)


def is_claimworthy_evidence(item: EvidenceItem) -> bool:
    return evidence_strength(item) in {"strong", "moderate"}


def claimworthy_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    return [item for item in items if is_claimworthy_evidence(item)]


def decision_tier(items: list[EvidenceItem], has_candidate_leads: bool = False) -> str:
    """Return a scholar-facing result tier instead of a binary claim/abstain label."""
    if not items:
        return "weak_lead" if has_candidate_leads else "no_result"
    strongest = max((evidence_strength(item) for item in items), key=_strength_rank)
    best = max(items, key=lambda item: item.confidence)
    if strongest == "strong" and best.metadata_score >= 0.65 and best.relationship_fit_score >= 0.45:
        return "confirmed"
    if strongest in {"strong", "moderate"}:
        return "probable"
    if strongest == "weak":
        return "strong_lead" if has_candidate_leads or best.relationship_fit_score >= 0.45 else "weak_lead"
    return "weak_lead" if has_candidate_leads else "no_result"


def evidence_decision(items: list[EvidenceItem], has_candidate_leads: bool = False) -> EvidenceDecision:
    if not items:
        tier = "weak_lead" if has_candidate_leads else "no_result"
        return EvidenceDecision(
            tier=tier,
            evidence_strength="none",
            claimworthy=False,
            reason="candidate_leads_without_indexed_evidence" if has_candidate_leads else "no_indexed_evidence",
        )
    best = max(items, key=lambda item: item.confidence)
    strength = evidence_strength(best)
    tier = decision_tier(items, has_candidate_leads)
    return EvidenceDecision(
        tier=tier,
        evidence_strength=strength,
        claimworthy=is_claimworthy_evidence(best),
        reason=_decision_reason(tier, strength, has_candidate_leads, best),
        best_evidence=best,
    )


def _decision_reason(tier: str, strength: str, has_candidate_leads: bool, best: EvidenceItem) -> str:
    if tier in {"confirmed", "probable"}:
        return f"{strength}_elastic_evidence"
    if tier == "strong_lead":
        return "weak_evidence_with_candidate_or_relationship_support"
    if has_candidate_leads:
        return "candidate_leads_need_stronger_elastic_evidence"
    return f"{strength}_evidence_below_claim_threshold"


def _strength_rank(strength: str) -> int:
    return {"contextual": 0, "weak": 1, "moderate": 2, "strong": 3}.get(strength, 0)
