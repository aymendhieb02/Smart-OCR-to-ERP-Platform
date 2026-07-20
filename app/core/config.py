from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Invoice OCR ERP"
    output_dir: Path = Path("outputs")
    max_upload_size_mb: int = 25
    low_confidence_threshold: float = 0.60
    ocr_languages: str = "fr,en"
    ocr_languages_list: list[str] = ["fr", "en", "ar"]
    enable_tesseract_fallback: bool = True
    ocr_mode: str = "balanced"
    ocr_profile: str = "optimized_mobile_v4"
    enable_ocr_disk_cache: bool = True
    ocr_cache_dir: Path = Path(".cache/ocr")
    paddle_enable_mkldnn: bool = False
    paddle_cpu_threads: int | None = 4
    paddle_use_gpu: bool = False
    paddle_ocr_version: str | None = None
    paddle_text_detection_model_name: str | None = "PP-OCRv4_mobile_det"
    paddle_text_recognition_model_name: str | None = "en_PP-OCRv4_mobile_rec"
    paddle_text_det_limit_side_len: int | None = None
    paddle_text_recognition_batch_size: int | None = None
    ocr_preprocessing_profile: str = "current"
    ocr_input_max_side: int | None = 1600
    table_reconstruction_profile: str = "p3_stable"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="INVOICE_OCR_")


settings = Settings()
