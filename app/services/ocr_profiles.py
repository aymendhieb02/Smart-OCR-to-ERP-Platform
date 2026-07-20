from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from app.core.config import settings


@dataclass(frozen=True)
class OCRProfile:
    name: str
    detector: str | None
    recognizer: str | None
    cpu_threads: int | None
    input_max_side: int | None
    enable_mkldnn: bool
    use_gpu: bool
    preprocessing_profile: str


PROFILES: dict[str, OCRProfile] = {
    "optimized_mobile_v4": OCRProfile(
        name="optimized_mobile_v4",
        detector="PP-OCRv4_mobile_det",
        recognizer="en_PP-OCRv4_mobile_rec",
        cpu_threads=4,
        input_max_side=1600,
        enable_mkldnn=False,
        use_gpu=False,
        preprocessing_profile="current",
    ),
    "legacy_v6_medium": OCRProfile(
        name="legacy_v6_medium",
        detector="PP-OCRv6_medium_det",
        recognizer="PP-OCRv6_medium_rec",
        cpu_threads=None,
        input_max_side=None,
        enable_mkldnn=False,
        use_gpu=False,
        preprocessing_profile="current",
    ),
    "no_resize_mobile_v4": OCRProfile(
        name="no_resize_mobile_v4",
        detector="PP-OCRv4_mobile_det",
        recognizer="en_PP-OCRv4_mobile_rec",
        cpu_threads=4,
        input_max_side=None,
        enable_mkldnn=False,
        use_gpu=False,
        preprocessing_profile="current",
    ),
}


def selected_profile_name() -> str:
    return (settings.ocr_profile or "optimized_mobile_v4").strip()


def selected_profile() -> OCRProfile:
    name = selected_profile_name()
    try:
        return PROFILES[name]
    except KeyError as exc:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Invalid OCR profile '{name}'. Valid profiles: {valid}") from exc


def effective_ocr_config() -> dict[str, Any]:
    profile = selected_profile()
    return {
        "ocr_profile": profile.name,
        "detector": _override("INVOICE_OCR_PADDLE_TEXT_DETECTION_MODEL_NAME", settings.paddle_text_detection_model_name, profile.detector),
        "recognizer": _override("INVOICE_OCR_PADDLE_TEXT_RECOGNITION_MODEL_NAME", settings.paddle_text_recognition_model_name, profile.recognizer),
        "cpu_threads": _int_override("INVOICE_OCR_PADDLE_CPU_THREADS", settings.paddle_cpu_threads, profile.cpu_threads),
        "input_max_side": _int_override("INVOICE_OCR_OCR_INPUT_MAX_SIDE", settings.ocr_input_max_side, profile.input_max_side),
        "enable_mkldnn": _bool_override("INVOICE_OCR_PADDLE_ENABLE_MKLDNN", settings.paddle_enable_mkldnn, profile.enable_mkldnn),
        "use_gpu": _bool_override("INVOICE_OCR_PADDLE_USE_GPU", settings.paddle_use_gpu, profile.use_gpu),
        "preprocessing_profile": _override("INVOICE_OCR_OCR_PREPROCESSING_PROFILE", settings.ocr_preprocessing_profile, profile.preprocessing_profile) or "current",
    }


def ocr_configuration_hash() -> str:
    import hashlib

    return hashlib.sha256(json.dumps(effective_ocr_config(), sort_keys=True).encode("utf-8")).hexdigest()


def _override(env_name: str, setting_value: Any, profile_value: Any) -> Any:
    if env_name in os.environ:
        value = os.environ.get(env_name)
        return value if value not in ("", "None", "none", "null") else None
    return profile_value


def _int_override(env_name: str, setting_value: Any, profile_value: int | None) -> int | None:
    value = _override(env_name, setting_value, profile_value)
    if value in (None, "", "0", 0):
        return None
    return int(value)


def _bool_override(env_name: str, setting_value: Any, profile_value: bool) -> bool:
    value = _override(env_name, setting_value, profile_value)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
