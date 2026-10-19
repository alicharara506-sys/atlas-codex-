"""Strict non-voting AI review contracts for ATLAS V4 Phase 6."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from app.v4.contracts import FrozenModel
from app.v4.ml_contracts import MLTarget, PrimaryCandidate


class AIReviewerRole(str, Enum):
    CONTEXT_SUMMARIZER = "CONTEXT_SUMMARIZER"
    MACRO_REVIEWER = "MACRO_REVIEWER"
    MARKET_REVIEWER = "MARKET_REVIEWER"
    ADVERSARIAL_CRITIC = "ADVERSARIAL_CRITIC"


class AIReviewStatus(str, Enum):
    SUPPORTS = "SUPPORTS"
    CAUTION = "CAUTION"
    OPPOSES = "OPPOSES"
    UNAVAILABLE = "UNAVAILABLE"


class AIReviewBundleStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    SKIPPED = "SKIPPED"


ScalarEvidence = str | float | int | bool | None


class AIReviewEvidence(FrozenModel):
    """Bounded structured evidence; free-form provider prompts are prohibited."""

    snapshot_id: str | None = Field(default=None, min_length=64, max_length=64)
    feature_set_id: str | None = Field(default=None, min_length=64, max_length=64)
    context_set_id: str | None = Field(default=None, min_length=64, max_length=64)
    macro: dict[str, ScalarEvidence] = Field(default_factory=dict, max_length=64)
    market: dict[str, ScalarEvidence] = Field(default_factory=dict, max_length=128)
    events: tuple[str, ...] = Field(default=(), max_length=32)
    news_context: tuple[str, ...] = Field(default=(), max_length=32)
    missing_data: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_evidence(self) -> AIReviewEvidence:
        if any(not key.strip() or len(key) > 120 for key in (*self.macro, *self.market)):
            raise ValueError("AI evidence keys must contain 1-120 visible characters")
        text_values = (*self.events, *self.news_context, *self.missing_data)
        if any(not value.strip() or len(value) > 1_000 for value in text_values):
            raise ValueError("AI evidence text must contain 1-1000 visible characters")
        return self


class SpecialistReviewPayload(FrozenModel):
    candidate: MLTarget
    supports: tuple[str, ...] = Field(default=(), max_length=16)
    contradictions: tuple[str, ...] = Field(default=(), max_length=16)
    event_risks: tuple[str, ...] = Field(default=(), max_length=16)
    missing_data: tuple[str, ...] = Field(default=(), max_length=16)
    invalidations: tuple[str, ...] = Field(default=(), max_length=16)
    review_status: AIReviewStatus

    @model_validator(mode="after")
    def validate_payload(self) -> SpecialistReviewPayload:
        values = (
            *self.supports,
            *self.contradictions,
            *self.event_risks,
            *self.missing_data,
            *self.invalidations,
        )
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("AI review statements must contain 1-500 visible characters")
        return self


class SpecialistReviewRecord(FrozenModel):
    review_id: str = Field(min_length=64, max_length=64)
    role: AIReviewerRole
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    payload: SpecialistReviewPayload
    latency_ms: float = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    non_voting: Literal[True] = True
    probability_authority: Literal[False] = False
    decision_authority: Literal[False] = False


class AIReviewRequest(FrozenModel):
    candidate_id: str = Field(min_length=64, max_length=64)
    evidence: AIReviewEvidence


class AIReviewBundle(FrozenModel):
    bundle_id: str = Field(min_length=64, max_length=64)
    candidate: PrimaryCandidate
    evidence_hash: str = Field(min_length=64, max_length=64)
    status: AIReviewBundleStatus
    skip_reason: str | None = Field(default=None, max_length=500)
    reviews: tuple[SpecialistReviewRecord, ...] = Field(default=(), max_length=4)
    mandatory_adversarial_review_present: bool
    all_reviews_non_voting: Literal[True] = True
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_latency_ms: float = Field(ge=0)
    external_api_cost_usd: float = Field(default=0.0, ge=0, le=0)
    atlas_probability: None = None
    publication_eligible: Literal[False] = False
    model_promotion_authority: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_bundle(self) -> AIReviewBundle:
        roles = tuple(review.role for review in self.reviews)
        if len(set(roles)) != len(roles):
            raise ValueError("an AI review bundle cannot repeat a specialist role")
        has_critic = AIReviewerRole.ADVERSARIAL_CRITIC in roles
        if self.mandatory_adversarial_review_present != has_critic:
            raise ValueError("adversarial-review presence must match bundle contents")
        if self.status is AIReviewBundleStatus.COMPLETE:
            if set(roles) != set(AIReviewerRole):
                raise ValueError("a complete bundle requires every specialist role")
            if any(review.payload.review_status is AIReviewStatus.UNAVAILABLE for review in self.reviews):
                raise ValueError("a complete bundle cannot contain unavailable reviews")
        if self.status is AIReviewBundleStatus.SKIPPED and self.reviews:
            raise ValueError("a skipped bundle cannot contain provider reviews")
        return self


class AIReviewResponse(FrozenModel):
    bundle: AIReviewBundle
    reused_existing: bool


__all__ = [
    "AIReviewBundle",
    "AIReviewBundleStatus",
    "AIReviewEvidence",
    "AIReviewRequest",
    "AIReviewResponse",
    "AIReviewStatus",
    "AIReviewerRole",
    "SpecialistReviewPayload",
    "SpecialistReviewRecord",
]
