"""The Clerk webhook is authenticated by its signature or not at all.

This endpoint takes no session, no token and no permission class, and it
creates users, rewrites their email address and deactivates accounts. The Svix
signature is the only thing standing between the URL and anyone who learns it,
so these tests are about what gets REFUSED at least as much as what works.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from django.urls import reverse

from apps.accounts.svix import SignatureError, sign, verify
from apps.users.models import CustomUser

pytestmark = pytest.mark.django_db

SECRET = "whsec_" + base64.b64encode(b"a-test-signing-secret-32-bytes!!").decode()
MESSAGE_ID = "msg_2abcDEF"


def _url() -> str:
    return reverse("v1:auth-clerk-webhook")


def _body(event_type="user.created", **data) -> bytes:
    payload = {
        "type": event_type,
        "data": {
            "id": data.pop("clerk_id", "user_abc123"),
            "first_name": data.pop("first_name", "Aigerim"),
            "last_name": data.pop("last_name", "Serik"),
            "email_addresses": [{"id": "e1", "email_address": data.pop(
                "email", "aigerim@example.com")}],
            "primary_email_address_id": "e1",
            **data,
        },
    }
    return json.dumps(payload).encode()


def _headers(body: bytes, secret=SECRET, timestamp=None, message_id=MESSAGE_ID):
    timestamp = int(time.time()) if timestamp is None else timestamp
    return {
        "HTTP_SVIX_ID": message_id,
        "HTTP_SVIX_TIMESTAMP": str(timestamp),
        "HTTP_SVIX_SIGNATURE": sign(secret, message_id, timestamp, body),
    }


def _post(client, body, **extra):
    return client.post(
        _url(), data=body, content_type="application/json", **extra
    )


# --- the endpoint -----------------------------------------------------------


def test_a_correctly_signed_event_is_applied(client, settings):
    settings.CLERK_WEBHOOK_SECRET = SECRET
    body = _body()

    response = _post(client, body, **_headers(body))

    assert response.status_code == 200
    user = CustomUser.objects.get(clerk_user_id="user_abc123")
    assert user.email == "aigerim@example.com"
    assert user.first_name == "Aigerim"


def test_an_unsigned_request_is_refused(client, settings):
    """The whole point. Previously this created a user."""
    settings.CLERK_WEBHOOK_SECRET = SECRET

    response = _post(client, _body())

    assert response.status_code == 401
    assert not CustomUser.objects.exists()


def test_a_forged_signature_is_refused(client, settings):
    settings.CLERK_WEBHOOK_SECRET = SECRET
    body = _body()
    headers = _headers(body, secret="whsec_" + base64.b64encode(b"wrong" * 6).decode())

    response = _post(client, body, **headers)

    assert response.status_code == 401
    assert not CustomUser.objects.exists()


def test_a_tampered_body_is_refused(client, settings):
    """Signed one payload, sent another -- the attack the raw-body rule stops."""
    settings.CLERK_WEBHOOK_SECRET = SECRET
    headers = _headers(_body(email="aigerim@example.com"))

    response = _post(client, _body(email="attacker@example.com"), **headers)

    assert response.status_code == 401
    assert not CustomUser.objects.exists()


def test_a_replayed_request_is_refused(client, settings):
    """A captured request must not still work an hour later."""
    settings.CLERK_WEBHOOK_SECRET = SECRET
    body = _body()
    stale = int(time.time()) - 3600

    response = _post(client, body, **_headers(body, timestamp=stale))

    assert response.status_code == 401
    assert not CustomUser.objects.exists()


def test_deleting_a_user_also_requires_a_signature(client, settings):
    """The event with the worst blast radius: it deactivates an account."""
    settings.CLERK_WEBHOOK_SECRET = SECRET
    CustomUser.objects.create_user(
        email="victim@example.com", password="x", clerk_user_id="user_victim"
    )

    response = _post(client, _body("user.deleted", clerk_id="user_victim"))

    assert response.status_code == 401
    assert CustomUser.objects.get(clerk_user_id="user_victim").is_active


def test_a_signed_delete_deactivates_without_removing_the_row(client, settings):
    settings.CLERK_WEBHOOK_SECRET = SECRET
    CustomUser.objects.create_user(
        email="victim@example.com", password="x", clerk_user_id="user_victim"
    )
    body = _body("user.deleted", clerk_id="user_victim")

    response = _post(client, body, **_headers(body))

    assert response.status_code == 200
    user = CustomUser.objects.get(clerk_user_id="user_victim")
    assert user.is_active is False, "soft delete keeps attempt history intact"


def test_a_missing_secret_refuses_rather_than_accepting(client, settings):
    """Fail closed. An unconfigured verifier that waves requests through is
    worse than no verifier, because it looks configured."""
    settings.CLERK_WEBHOOK_SECRET = ""
    settings.DEBUG = False

    response = _post(client, _body())

    assert response.status_code == 503
    assert not CustomUser.objects.exists()


def test_local_development_without_a_secret_still_works(client, settings):
    settings.CLERK_WEBHOOK_SECRET = ""
    # debug_toolbar registers its `djdt` URLs only if DEBUG was on when the
    # URLconf loaded, so turning DEBUG on mid-test makes its middleware fail to
    # reverse them. Nothing to do with the webhook; drop it for this test.
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "debug_toolbar" not in m]
    settings.DEBUG = True

    assert _post(client, _body()).status_code == 200
    assert CustomUser.objects.filter(clerk_user_id="user_abc123").exists()


def test_the_reason_for_refusal_is_not_disclosed(client, settings):
    """A caller learning WHICH check failed is a caller being helped to forge."""
    settings.CLERK_WEBHOOK_SECRET = SECRET
    body = _body()

    detail = _post(client, body).json()["detail"]

    assert detail == "invalid webhook signature"
    for leak in ("timestamp", "svix-id", "header", "base64", "replay"):
        assert leak not in detail.lower()


# --- the verifier itself ----------------------------------------------------


def test_a_rotating_secret_sends_several_signatures():
    """Svix sends old and new during rotation; matching only the first breaks it."""
    body = b'{"type":"user.created"}'
    timestamp = int(time.time())
    old = "whsec_" + base64.b64encode(b"the-previous-secret-value-here!!").decode()
    header = f"{sign(old, MESSAGE_ID, timestamp, body)} {sign(SECRET, MESSAGE_ID, timestamp, body)}"

    headers = {"svix-id": MESSAGE_ID, "svix-timestamp": str(timestamp),
               "svix-signature": header}
    verify(SECRET, headers, body)  # the second entry matches; must not raise
    verify(old, headers, body)


def test_an_unknown_signature_version_is_skipped_not_fatal():
    body = b"{}"
    timestamp = int(time.time())
    header = f"v2,ZmFrZQ== {sign(SECRET, MESSAGE_ID, timestamp, body)}"

    verify(SECRET, {"svix-id": MESSAGE_ID, "svix-timestamp": str(timestamp),
                    "svix-signature": header}, body)


def test_the_standard_webhooks_header_names_are_accepted():
    """A proxy that normalises `svix-` to `webhook-` must not break delivery."""
    body = b"{}"
    timestamp = int(time.time())

    verify(SECRET, {"webhook-id": MESSAGE_ID, "webhook-timestamp": str(timestamp),
                    "webhook-signature": sign(SECRET, MESSAGE_ID, timestamp, body)}, body)


@pytest.mark.parametrize("headers", [
    {},
    {"svix-id": MESSAGE_ID},
    {"svix-id": MESSAGE_ID, "svix-timestamp": "123"},
])
def test_incomplete_headers_are_refused(headers):
    with pytest.raises(SignatureError):
        verify(SECRET, {**headers, "svix-signature": "v1,x"}, b"{}")


def test_a_future_timestamp_is_refused():
    """Clock skew is bounded in both directions, not just the past."""
    body = b"{}"
    ahead = int(time.time()) + 3600
    with pytest.raises(SignatureError):
        verify(SECRET, {"svix-id": MESSAGE_ID, "svix-timestamp": str(ahead),
                        "svix-signature": sign(SECRET, MESSAGE_ID, ahead, body)}, body)


def test_an_empty_secret_never_verifies():
    with pytest.raises(SignatureError):
        verify("", {"svix-id": "a", "svix-timestamp": "1", "svix-signature": "v1,x"}, b"")
