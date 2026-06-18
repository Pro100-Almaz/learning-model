"""Tests for the project-wide DRF exception handler."""

from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    Throttled,
    ValidationError,
)

from apps.common.exceptions import handler
from apps.common.responses import error_response


@pytest.fixture
def context():
    """Minimal context dict — DRF's default handler tolerates an empty one."""

    return {"view": None, "args": (), "kwargs": {}, "request": None}


def test_handler_returns_none_for_non_drf_exceptions(context):
    """Non-DRF exceptions fall through to Django's default 500 handling."""

    assert handler(RuntimeError("boom"), context) is None


def test_handler_flattens_validation_error_dict(context):
    exc = ValidationError({"email": ["This field is required."]})
    response = handler(exc, context)

    assert response is not None
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.data.keys()) == {"detail", "code"}
    assert response.data["code"] == "validation_error"
    assert "email" in response.data["detail"]
    assert "This field is required." in response.data["detail"]


def test_handler_flattens_validation_error_list(context):
    exc = ValidationError(["bad input", "also bad"])
    response = handler(exc, context)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "validation_error"
    assert "bad input" in response.data["detail"]
    assert "also bad" in response.data["detail"]


def test_handler_flattens_validation_error_string(context):
    exc = ValidationError("just a string")
    response = handler(exc, context)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "validation_error"
    assert "just a string" in response.data["detail"]


def test_handler_maps_permission_denied(context):
    response = handler(PermissionDenied("nope"), context)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["code"] == "forbidden"
    assert response.data["detail"]


def test_handler_maps_not_found(context):
    response = handler(NotFound("missing"), context)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.data["code"] == "not_found"


def test_handler_maps_not_authenticated(context):
    response = handler(NotAuthenticated(), context)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.data["code"] == "unauthenticated"


def test_handler_maps_authentication_failed(context):
    response = handler(AuthenticationFailed("bad token"), context)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.data["code"] == "auth_failed"


def test_handler_maps_throttled(context):
    response = handler(Throttled(wait=1), context)

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert response.data["code"] == "throttled"


def test_handler_preserves_status_code(context):
    """The wrapper must not silently rewrite DRF's chosen status code."""

    exc = ValidationError("bad")
    response = handler(exc, context)

    assert response.status_code == exc.status_code


def test_error_response_helper_shape():
    response = error_response("something broke", "custom_code", status=418)

    assert response.status_code == 418
    assert response.data == {"detail": "something broke", "code": "custom_code"}


def test_error_response_defaults_to_400():
    response = error_response("bad", "validation_error")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data == {"detail": "bad", "code": "validation_error"}
