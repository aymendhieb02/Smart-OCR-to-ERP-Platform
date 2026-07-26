from app.core.config import settings
from app.services.layout_model.layout_model_loader import LayoutModelLoader
from app.services.layout_model.layout_model_router import detect_layout_blocks_with_model


def test_layout_model_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "enable_layout_model", False)

    blocks, debug = detect_layout_blocks_with_model([], [])

    assert blocks == []
    assert debug["enabled"] is False


def test_layout_model_loader_returns_unavailable_without_weights(monkeypatch, tmp_path):
    LayoutModelLoader.reset_singleton()
    monkeypatch.setattr(settings, "layout_model_path", tmp_path / "missing")

    result = LayoutModelLoader().load(download=False)

    assert result.available is False
    assert result.error
