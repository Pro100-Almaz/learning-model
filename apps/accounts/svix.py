"""Svix webhook signature verification, as Clerk sends it.

Implemented here rather than pulled in as a dependency: the scheme is a single
HMAC and forty lines, and the `svix` package would be a new install in every
image for exactly this. The scheme is fixed and public, so there is no vendor
drift to track.

    signed = f"{id}.{timestamp}.{body}"
    signature = base64(HMAC_SHA256(secret, signed))

The `svix-signature` header carries a SPACE-SEPARATED LIST of `v1,<sig>` pairs,
not one value -- Svix sends several during a secret rotation, and a receiver
that reads only the first rejects half the traffic mid-rotation.

Two things here are security-critical and easy to get subtly wrong:

    * the signature covers the RAW REQUEST BODY. Verifying a re-serialized dict
      compares a payload the sender never signed, and any parser difference --
      key order, unicode escaping, float formatting -- silently breaks it or,
      worse, lets a forged body through a lenient re-encode;
    * comparisons use hmac.compare_digest. A plain `==` leaks how much of the
      signature matched through timing, which is enough to forge one.
"""

from __future__ import annotations

import base64
import hmac
import time
from hashlib import sha256

# Svix's own tolerance. Narrow enough that a captured request cannot be replayed
# tomorrow, wide enough to survive ordinary clock skew between hosts.
TOLERANCE_SECONDS = 5 * 60

# Clerk sends the `svix-` names; the vendor-neutral Standard Webhooks spec uses
# `webhook-`. Accepting both costs nothing and means a proxy that normalises
# them does not silently break delivery.
_HEADER_SETS = (
    ("svix-id", "svix-timestamp", "svix-signature"),
    ("webhook-id", "webhook-timestamp", "webhook-signature"),
)


class SignatureError(Exception):
    """The request did not come from the sender holding the secret."""


def _decode_secret(secret: str) -> bytes:
    """Svix secrets are base64, conventionally prefixed `whsec_`."""
    raw = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as error:  # a mistyped secret must not read as a bad request
        raise SignatureError(f"webhook secret is not valid base64: {error}") from error


def _headers(getter) -> tuple[str, str, str]:
    for id_name, timestamp_name, signature_name in _HEADER_SETS:
        message_id = getter(id_name)
        timestamp = getter(timestamp_name)
        signature = getter(signature_name)
        if message_id and timestamp and signature:
            return message_id, timestamp, signature
    raise SignatureError("missing svix-id, svix-timestamp or svix-signature header")


def _check_timestamp(timestamp: str, now: float | None = None) -> None:
    try:
        sent = int(timestamp)
    except (TypeError, ValueError) as error:
        raise SignatureError(f"timestamp {timestamp!r} is not an integer") from error

    drift = (time.time() if now is None else now) - sent
    if drift > TOLERANCE_SECONDS:
        raise SignatureError(f"timestamp is {int(drift)}s old; replay refused")
    if drift < -TOLERANCE_SECONDS:
        raise SignatureError(f"timestamp is {int(-drift)}s in the future")


def verify(secret: str, headers, body: bytes, now: float | None = None) -> None:
    """Raise SignatureError unless `body` was signed with `secret`.

    `headers` is anything with a case-insensitive ``.get`` -- ``request.headers``
    or a plain dict. `body` must be the raw bytes off the wire.
    """
    if not secret:
        raise SignatureError("no webhook secret configured")

    message_id, timestamp, signature_header = _headers(headers.get)
    _check_timestamp(timestamp, now=now)

    signed = b".".join(
        (message_id.encode(), timestamp.encode(), body if body else b"")
    )
    expected = base64.b64encode(
        hmac.new(_decode_secret(secret), signed, sha256).digest()
    ).decode()

    for part in signature_header.split():
        version, _, candidate = part.partition(",")
        if version != "v1":
            # Unknown scheme version: skip rather than reject, so a future v2
            # sent alongside v1 does not break a receiver that understands v1.
            continue
        if hmac.compare_digest(candidate, expected):
            return

    raise SignatureError("no v1 signature in the header matched")


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """The header a sender would produce. Exists for tests, and for debugging."""
    signed = b".".join((message_id.encode(), str(timestamp).encode(), body))
    digest = hmac.new(_decode_secret(secret), signed, sha256).digest()
    return f"v1,{base64.b64encode(digest).decode()}"
