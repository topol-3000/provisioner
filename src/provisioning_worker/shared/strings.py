"""Shared text-shaping utilities (truncation, sanitisation). No I/O.

Helpers here are deliberately private (``_``-prefixed) — callers import via
``from provisioning_worker.shared.strings import _truncate`` so each call
site is grep-able.
"""


def _truncate(s: str, *, max_len: int) -> str:
    """Return ``s`` truncated to ``max_len`` characters with an ellipsis.

    The ellipsis (``"…"``) occupies the final character of the truncated
    output so the return value is exactly ``max_len`` characters when
    shortening occurs. Inputs already at or under ``max_len`` are unchanged.

    Used by the outbox relay (D-03 Phase 4 — ``event_outbox.last_error``)
    to bound exception context stored in the JSONB column.

    Args:
        s: The input string. Must not be ``None``.
        max_len: Maximum allowed length of the return value. Must be ``>= 1``.

    Returns:
        ``s`` unchanged if ``len(s) <= max_len``; otherwise
        ``s[: max_len - 1] + "…"``.
    """
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"
