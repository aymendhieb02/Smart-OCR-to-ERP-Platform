from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.llm_correction_applier import build_hybrid_candidate, clone_response_with_hybrid_candidate
from app.services.llm_correction_gate import review_llm_corrections
from app.services.llm_evidence_builder import build_evidence_package, fingerprint_evidence, infer_trigger_reasons
from app.services.llm_metrics import LLMMetrics, LLMMetricTimer
from app.services.llm_prompt_builder import build_llm_payload
from app.services.llm_resolver import LLMResolverError, OllamaClient, resolve_with_llm
from app.services.llm_response_cache import load_cached_llm_response, save_cached_llm_response
from app.services.llm_response_parser import LLMResolution, parse_llm_response


@dataclass
class LLMRouteResult:
    invoked: bool
    skipped_reason: str | None
    payload: dict[str, Any] | None
    resolution: LLMResolution | None
    metrics: LLMMetrics
    error: str | None = None
    evidence_package: dict[str, Any] | None = None
    trigger_reasons: list[str] | None = None
    accepted_corrections: list[dict[str, Any]] | None = None
    rejected_corrections: list[dict[str, Any]] | None = None
    hybrid_candidate_result: dict[str, Any] | None = None
    final_response: Any | None = None
    final_source: str = "deterministic"
    fallback_reason: str | None = None
    cache_source: str = "none"
    fingerprint: str | None = None
    deterministic_validation: dict[str, Any] | None = None
    deterministic_erp_readiness: dict[str, Any] | None = None

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(settings.enable_llm_resolver),
            "invoked": self.invoked,
            "skipped_reason": self.skipped_reason,
            "trigger_reasons": self.trigger_reasons or [],
            "model": settings.llm_resolver_model,
            "latency_ms": int((self.metrics.duration_seconds or 0) * 1000),
            "document_decision": self.resolution.document_decision if self.resolution else None,
            "proposals": [proposal.model_dump() for proposal in self.resolution.proposals] if self.resolution else [],
            "accepted_corrections": self.accepted_corrections or [],
            "rejected_corrections": self.rejected_corrections or [],
            "deterministic_validation": self.deterministic_validation,
            "hybrid_validation": (self.hybrid_candidate_result or {}).get("validation") if self.hybrid_candidate_result else None,
            "deterministic_erp_readiness": self.deterministic_erp_readiness,
            "hybrid_erp_readiness": (self.hybrid_candidate_result or {}).get("erp_readiness") if self.hybrid_candidate_result else None,
            "final_source": self.final_source,
            "fallback_reason": self.fallback_reason,
            "evidence_package": self.evidence_package,
            "cache_source": self.cache_source,
            "fingerprint": self.fingerprint,
            "payload": self.payload,
            "resolution": asdict(self.resolution) if self.resolution else None,
            "metrics": self.metrics.model_dump(),
            "error": self.error,
        }


def resolve_if_needed(response: Any, *, client: OllamaClient | None = None) -> LLMRouteResult:
    confidence = _overall_confidence(response)
    trigger_reasons = infer_trigger_reasons(response)
    metrics = LLMMetrics(
        model=settings.llm_resolver_model,
        confidence_before=confidence,
    )
    skip_reason = _skip_reason(response, confidence, trigger_reasons)
    if skip_reason:
        metrics.skipped_reason = skip_reason
        return LLMRouteResult(
            False,
            skip_reason,
            None,
            None,
            metrics,
            trigger_reasons=trigger_reasons,
            deterministic_validation=_dump(response.validation),
            deterministic_erp_readiness=response.erp_readiness,
        )

    evidence = build_evidence_package(response, trigger_reasons)
    if not evidence.evidence_items:
        metrics.skipped_reason = "no_relevant_evidence"
        return LLMRouteResult(
            False,
            "no_relevant_evidence",
            None,
            None,
            metrics,
            evidence_package=evidence.model_dump(),
            trigger_reasons=trigger_reasons,
            deterministic_validation=_dump(response.validation),
            deterministic_erp_readiness=response.erp_readiness,
        )
    payload = build_llm_payload(response, evidence)
    fingerprint = _llm_fingerprint(payload, evidence)
    use_cache = _use_cache(client)
    cached = load_cached_llm_response(fingerprint) if use_cache else None
    timer = LLMMetricTimer()
    metrics.invoked = True
    try:
        if cached:
            resolution = parse_llm_response(json.dumps(cached["resolution"], ensure_ascii=False))
            cache_source = "disk"
        else:
            resolution = resolve_with_llm(payload, client=client)
            cache_source = "fresh"
            if use_cache:
                save_cached_llm_response(fingerprint, {
                    "model": settings.llm_resolver_model,
                    "prompt_version": evidence.prompt_version,
                    "resolution": resolution.model_dump(),
                })
    except LLMResolverError as exc:
        metrics.duration_seconds = timer.elapsed()
        metrics.error_type = type(exc).__name__
        return LLMRouteResult(
            True,
            None,
            payload,
            None,
            metrics,
            error=str(exc),
            evidence_package=evidence.model_dump(),
            trigger_reasons=trigger_reasons,
            fallback_reason=str(exc),
            fingerprint=fingerprint,
            deterministic_validation=_dump(response.validation),
            deterministic_erp_readiness=response.erp_readiness,
        )
    metrics.duration_seconds = timer.elapsed()
    metrics.success = True
    metrics.confidence_after = resolution.confidence
    accepted, rejected = review_llm_corrections(response, resolution, evidence)
    hybrid_candidate = build_hybrid_candidate(response, accepted) if accepted else None
    final_response = None
    final_source = "deterministic"
    fallback_reason = None
    if hybrid_candidate and hybrid_candidate.get("improves_safely"):
        if _active_mode() == "validated_apply" and settings.llm_resolver_auto_apply_safe_corrections:
            final_response = clone_response_with_hybrid_candidate(response, hybrid_candidate)
            final_source = "hybrid"
        else:
            fallback_reason = "safe_corrections_available_but_auto_apply_disabled"
    elif resolution.proposals:
        fallback_reason = "no_safe_improving_corrections"
    return LLMRouteResult(
        True,
        None,
        payload,
        resolution,
        metrics,
        evidence_package=evidence.model_dump(),
        trigger_reasons=trigger_reasons,
        accepted_corrections=[item.model_dump() for item in accepted],
        rejected_corrections=[item.model_dump() for item in rejected],
        hybrid_candidate_result=_safe_candidate_debug(hybrid_candidate),
        final_response=final_response,
        final_source=final_source,
        fallback_reason=fallback_reason,
        cache_source=cache_source,
        fingerprint=fingerprint,
        deterministic_validation=_dump(response.validation),
        deterministic_erp_readiness=response.erp_readiness,
    )


def _skip_reason(response: Any, confidence: float | None, trigger_reasons: list[str]) -> str | None:
    if not settings.enable_llm_resolver or _active_mode() == "disabled":
        return "disabled"
    threshold = float(settings.llm_resolver_confidence_threshold or 0.78)
    if response.validation.status == "valid" and confidence is not None and confidence >= threshold:
        return "high_confidence_deterministic_result"
    if not trigger_reasons:
        return "no_resolvable_trigger"
    return None


def _overall_confidence(response: Any) -> float | None:
    breakdown = response.confidence_breakdown or {}
    value = breakdown.get("overall_confidence")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _active_mode() -> str:
    mode = str(getattr(settings, "llm_resolver_mode", "advisory") or "advisory").strip().lower()
    if mode not in {"disabled", "advisory", "validated_apply"}:
        return "advisory"
    return mode


def _llm_fingerprint(payload: dict[str, Any], evidence: Any) -> str:
    material = {
        "cache_schema_version": 2,
        "model": settings.llm_resolver_model,
        "prompt_version": evidence.prompt_version,
        "structured_hash": fingerprint_evidence(payload),
        "evidence_hash": evidence.fingerprint,
        "acceptance_threshold": settings.llm_resolver_acceptance_threshold,
        "mode": _active_mode(),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _safe_candidate_debug(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    return {
        "validation": candidate.get("validation"),
        "financial_reasoning": candidate.get("financial_reasoning"),
        "erp_readiness": candidate.get("erp_readiness"),
        "applied_reviews": candidate.get("applied_reviews"),
        "improves_safely": candidate.get("improves_safely"),
        "fields": candidate.get("fields").model_dump(mode="json") if hasattr(candidate.get("fields"), "model_dump") else None,
    }


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _use_cache(client: OllamaClient | None) -> bool:
    if client is None:
        return True
    return Path(settings.llm_resolver_cache_dir) != Path("outputs/cache/llm")
