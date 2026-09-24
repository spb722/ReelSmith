"""Per-vendor concurrency caps (Story 4.3).

The paid tool handlers run their blocking bodies in worker threads
(`asyncio.to_thread`), so the caps are `threading` semaphores, acquired around
the vendor call inside that offloaded code. They are process-wide: every shot
shares them, whichever agent query the call came from.
"""

from __future__ import annotations

import threading

from orchestrator.settings import (
    DEFAULT_CODEX_IMAGE_CONCURRENCY,
    DEFAULT_GEMINI_IMAGE_CONCURRENCY,
    DEFAULT_VEO_CONCURRENCY,
)

VEO = "veo"
GEMINI_IMAGE = "gemini_image"
CODEX_IMAGE = "codex_image"

_limits: dict[str, int] = {}
_semaphores: dict[str, threading.BoundedSemaphore] = {}


def configure_vendor_limits(*, veo: int, gemini_image: int, codex_image: int) -> None:
    """Set the caps. Call before production starts, never with calls in flight."""

    limits = {VEO: veo, GEMINI_IMAGE: gemini_image, CODEX_IMAGE: codex_image}
    for vendor, limit in limits.items():
        if limit < 1:
            raise ValueError(f"{vendor} concurrency must be at least 1, got {limit}")
    for vendor, limit in limits.items():
        if _limits.get(vendor) != limit:
            _semaphores[vendor] = threading.BoundedSemaphore(limit)
            _limits[vendor] = limit


def vendor_limit(vendor: str) -> int:
    return _limits[vendor]


def vendor_slot(vendor: str) -> threading.BoundedSemaphore:
    """The semaphore to hold (`with vendor_slot(...)`) around one vendor call."""

    return _semaphores[vendor]


configure_vendor_limits(
    veo=DEFAULT_VEO_CONCURRENCY,
    gemini_image=DEFAULT_GEMINI_IMAGE_CONCURRENCY,
    codex_image=DEFAULT_CODEX_IMAGE_CONCURRENCY,
)
