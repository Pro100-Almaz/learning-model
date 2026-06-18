"""Project-wide DRF exception handling.

Wraps DRF's default exception handler and flattens the response body into a
consistent ``{"detail": str, "code": str}`` envelope so every API endpoint
emits the same error contract.
"""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.views import exception_handler

# Mapping from DRF exception classes to our slug codes. The first match wins,
# so order is important — more specific classes should come before generic ones.
_CODE_MAP: tuple[tuple[type[Exception], str], ...] = (
    (ValidationError, "validation_error"),
    (NotFound, "not_found"),
    (PermissionDenied, "forbidden"),
    (NotAuthenticated, "unauthenticated"),
    (AuthenticationFailed, "auth_failed"),
    (Throttled, "throttled"),
)


def _code_for(exc: Exception) -> str:
    """Resolve the project-wide error code slug for a given exception."""

    for exc_cls, code in _CODE_MAP:
        if isinstance(exc, exc_cls):
            return code
    return "error"


def _best_effort_detail(data: Any, exc: Exception) -> str:
    """Flatten DRF's structured ``response.data`` into a single string.

    DRF returns errors in many shapes — strings, lists, dicts of lists, nested
    dicts for ValidationError, etc. We do a best-effort flatten so the client
    always receives a human-readable ``detail`` string.
    """

    if data is None:
        return str(exc) if str(exc) else "Error"

    if isinstance(data, str):
        return data

    if isinstance(data, dict):
        # Common case: {"detail": "..."} — use it directly.
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail

        # Otherwise flatten field errors like {"field": ["msg1", "msg2"]}.
        parts: list[str] = []
        for key, value in data.items():
            flattened = _best_effort_detail(value, exc)
            if key == "detail" or key == "non_field_errors":
                parts.append(flattened)
            else:
                parts.append(f"{key}: {flattened}")
        if parts:
            return "; ".join(parts)

    if isinstance(data, (list, tuple)):
        parts = [_best_effort_detail(item, exc) for item in data]
        parts = [p for p in parts if p]
        if parts:
            return "; ".join(parts)

    return str(data)


def handler(exc, context):
    """Project-wide DRF exception handler.

    Delegates to DRF's default handler. If DRF can't handle the exception
    (returns ``None``), we let Django's 500 handling take over. Otherwise we
    replace the response body with our standard ``{detail, code}`` envelope
    while preserving the original ``status_code``.
    """

    response = exception_handler(exc, context)
    if response is None:
        return None

    detail = _best_effort_detail(response.data, exc)
    code = _code_for(exc)

    response.data = {"detail": detail, "code": code}
    return response
