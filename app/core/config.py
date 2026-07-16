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
    enable_ocr_disk_cache: bool = True
    ocr_cache_dir: Path = Path(".cache/ocr")

    model_config = SettingsConfigDict(env_file=".env", env_prefix="INVOICE_OCR_")


settings = Settings()
