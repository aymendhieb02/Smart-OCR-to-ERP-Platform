"""Short-lived in-memory evidence cache for manual semantic routing."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from app.services.pipeline_runner import DossierRoutingContext, ProcessedLogicalDocument, process_routing_review_selection


_SESSION_TTL_SECONDS = 30 * 60
_MAX_SESSIONS = 16
_LOCK = threading.RLock()


@dataclass
class _RoutingSession:
    context: DossierRoutingContext
    pending_pages: set[int]
    busy_pages: set[int]
    expires_at: float


_SESSIONS: dict[str, _RoutingSession] = {}


def register_routing_session(dossier_id: str, context: DossierRoutingContext) -> None:
    """Keep source images and OCR evidence in memory only, with a bounded TTL."""
    from app.services.dossier_segmentation import classify_pages

    pending = {
        item.page_number for item in classify_pages(context.ocr_result)
        if item.routing_status == "review_required"
    }
    if not pending:
        return
    now = time.monotonic()
    with _LOCK:
        _prune_expired(now)
        while len(_SESSIONS) >= _MAX_SESSIONS:
            oldest = min(_SESSIONS, key=lambda key: _SESSIONS[key].expires_at)
            del _SESSIONS[oldest]
        _SESSIONS[dossier_id] = _RoutingSession(
            context=context,
            pending_pages=pending,
            busy_pages=set(),
            expires_at=now + _SESSION_TTL_SECONDS,
        )


def resolve_routing_page(
    dossier_id: str,
    physical_page: int,
    semantic_group: str,
) -> ProcessedLogicalDocument:
    now = time.monotonic()
    with _LOCK:
        _prune_expired(now)
        session = _SESSIONS.get(dossier_id)
        if session is None:
            raise LookupError("Routing evidence has expired; process the document again")
        if physical_page not in session.pending_pages:
            raise ValueError("The physical page is not awaiting routing review")
        if physical_page in session.busy_pages:
            raise ValueError("Routing resolution is already in progress for this page")
        session.busy_pages.add(physical_page)

    try:
        processed = process_routing_review_selection(session.context, physical_page, semantic_group)
    except Exception:
        with _LOCK:
            session.busy_pages.discard(physical_page)
        raise

    with _LOCK:
        session.busy_pages.discard(physical_page)
        session.pending_pages.discard(physical_page)
        session.expires_at = time.monotonic() + _SESSION_TTL_SECONDS
        if not session.pending_pages:
            _SESSIONS.pop(dossier_id, None)
    return processed


def clear_routing_sessions_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()


def _prune_expired(now: float) -> None:
    for key in [key for key, session in _SESSIONS.items() if session.expires_at <= now]:
        del _SESSIONS[key]
