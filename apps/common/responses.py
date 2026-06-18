"""Reusable response helpers for emitting the project's standard envelopes."""

from __future__ import annotations

from rest_framework import status as http_status
from rest_framework.response import Response


def error_response(
    detail: str,
    code: str,
    status: int = http_status.HTTP_400_BAD_REQUEST,
) -> Response:
    """Return a DRF ``Response`` shaped like the project's error envelope.

    Mirrors the shape produced by :func:`apps.common.exceptions.handler` so
    views that raise errors manually emit the same contract clients already
    handle for DRF-raised exceptions.
    """

    return Response({"detail": detail, "code": code}, status=status)
