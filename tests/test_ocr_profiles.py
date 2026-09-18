from __future__ import annotations

import os

import pytest

from app.core.config import settings
from app.services import ocr_profiles
from app.services.ocr_engine import _paddle_fingerprint


@pytest.fixture(autouse=True)
def restore_profile(monkeypatch):
    original = settings.ocr_profile
    keys = [
        "INVOICE_OCR_PADDLE_TEXT_DETECTION_MODEL_NAME",
        "INVOICE_OCR_PADDLE_TEXT_RECOGNITION_MODEL_NAME",
        "INVOICE_OCR_PADDLE_CPU_THREADS",
        "INVOICE_OCR_OCR_INPUT_MAX_SIDE",
        "INVOICE_OCR_PADDLE_ENABLE_MKLDNN",
        "INVOICE_OCR_PADDLE_USE_GPU",
        "INVOICE_OCR_OCR_PREPROCESSING_PROFILE",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield
    settings.ocr_profile = original


def test_optimized_profile_is_default() -> None:
    settings.ocr_profile = "optimized_mobile_v4"

    config = ocr_profiles.effective_ocr_config()

    assert config["ocr_profile"] == "optimized_mobile_v4"
    assert config["detector"] == "PP-OCRv4_mobile_det"
    assert config["recognizer"] == "en_PP-OCRv4_mobile_rec"
    assert config["cpu_threads"] == 4
    assert config["input_max_side"] == 1600


def test_optimized_mobile_v5_profile_is_cpu_mobile_configuration() -> None:
    settings.ocr_profile = "optimized_mobile_v5"

    config = ocr_profiles.effective_ocr_config()

    assert config == {
        "ocr_profile": "optimized_mobile_v5",
        "detector": "PP-OCRv5_mobile_det",
        "recognizer": "latin_PP-OCRv5_mobile_rec",
        "cpu_threads": 4,
        "input_max_side": 1600,
        "enable_mkldnn": False,
        "use_gpu": False,
        "preprocessing_profile": "current",
    }


def test_v4_profile_remains_unchanged_after_v5_addition() -> None:
    profile = ocr_profiles.PROFILES["optimized_mobile_v4"]

    assert profile == ocr_profiles.OCRProfile(
        name="optimized_mobile_v4",
        detector="PP-OCRv4_mobile_det",
        recognizer="en_PP-OCRv4_mobile_rec",
        cpu_threads=4,
        input_max_side=1600,
        enable_mkldnn=False,
        use_gpu=False,
        preprocessing_profile="current",
    )


def test_legacy_profile_is_explicit_rollback() -> None:
    settings.ocr_profile = "legacy_v6_medium"

    config = ocr_profiles.effective_ocr_config()

    assert config["detector"] == "PP-OCRv6_medium_det"
    assert config["recognizer"] == "PP-OCRv6_medium_rec"
    assert config["input_max_side"] is None


def test_low_level_environment_overrides_profile(monkeypatch) -> None:
    settings.ocr_profile = "optimized_mobile_v4"
    monkeypatch.setenv("INVOICE_OCR_PADDLE_CPU_THREADS", "2")
    monkeypatch.setenv("INVOICE_OCR_OCR_INPUT_MAX_SIDE", "0")
    monkeypatch.setenv("INVOICE_OCR_PADDLE_TEXT_DETECTION_MODEL_NAME", "custom_det")

    config = ocr_profiles.effective_ocr_config()

    assert config["cpu_threads"] == 2
    assert config["input_max_side"] is None
    assert config["detector"] == "custom_det"


def test_invalid_profile_fails_clearly() -> None:
    settings.ocr_profile = "does_not_exist"

    with pytest.raises(ValueError, match="Invalid OCR profile"):
        ocr_profiles.selected_profile()


def test_ocr_fingerprint_changes_between_profiles() -> None:
    settings.ocr_profile = "optimized_mobile_v4"
    optimized = _paddle_fingerprint()
    settings.ocr_profile = "legacy_v6_medium"
    legacy = _paddle_fingerprint()

    assert optimized != legacy
    assert "PP-OCRv4_mobile_det" in optimized
    assert "PP-OCRv6_medium_det" in legacy


def test_v4_and_v5_cache_fingerprints_are_distinct() -> None:
    settings.ocr_profile = "optimized_mobile_v4"
    v4 = _paddle_fingerprint()
    settings.ocr_profile = "optimized_mobile_v5"
    v5 = _paddle_fingerprint()

    assert v4 != v5
    assert '"ocr_profile": "optimized_mobile_v4"' in v4
    assert '"ocr_profile": "optimized_mobile_v5"' in v5
    assert "latin_PP-OCRv5_mobile_rec" in v5
