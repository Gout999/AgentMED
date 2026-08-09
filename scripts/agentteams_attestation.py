"""Cryptographic trust boundary for AgentTeams/Matrix evidence exports."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class AgentTeamsAttestationError(ValueError):
    pass


def canonical_receipt_bytes(receipt: dict[str, Any]) -> bytes:
    """Canonical bytes signed by the independently credentialed exporter."""

    unsigned = {key: value for key, value in receipt.items() if key != "attestation"}
    try:
        return json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AgentTeamsAttestationError("AgentTeams receipt is not canonical JSON") from exc


def public_key_id(public_key_b64: str) -> str:
    raw = _decode_public_key(public_key_b64)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _decode_public_key(public_key_b64: str) -> bytes:
    if not isinstance(public_key_b64, str) or not public_key_b64:
        raise AgentTeamsAttestationError("AgentTeams attestation public key is missing")
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AgentTeamsAttestationError(
            "AgentTeams attestation public key is not valid base64"
        ) from exc
    if len(raw) != 32:
        raise AgentTeamsAttestationError(
            "AgentTeams Ed25519 attestation public key must be exactly 32 bytes"
        )
    return raw


def verify_receipt(receipt: dict[str, Any], public_key_b64: str) -> str:
    """Verify one signed receipt and return its externally anchored key id."""

    if not isinstance(receipt, dict):
        raise AgentTeamsAttestationError("AgentTeams receipt is not an object")
    attestation = receipt.get("attestation")
    if not isinstance(attestation, dict) or set(attestation) != {
        "algorithm",
        "key_id",
        "signature",
    }:
        raise AgentTeamsAttestationError("AgentTeams receipt attestation schema is invalid")
    raw_public_key = _decode_public_key(public_key_b64)
    expected_key_id = "sha256:" + hashlib.sha256(raw_public_key).hexdigest()
    if (
        attestation.get("algorithm") != "ed25519"
        or attestation.get("key_id") != expected_key_id
    ):
        raise AgentTeamsAttestationError(
            "AgentTeams receipt is signed by an unexpected attestation key"
        )
    try:
        signature = base64.b64decode(str(attestation.get("signature") or ""), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AgentTeamsAttestationError(
            "AgentTeams receipt signature is not valid base64"
        ) from exc
    if len(signature) != 64:
        raise AgentTeamsAttestationError(
            "AgentTeams Ed25519 receipt signature must be exactly 64 bytes"
        )
    try:
        Ed25519PublicKey.from_public_bytes(raw_public_key).verify(
            signature, canonical_receipt_bytes(receipt)
        )
    except InvalidSignature as exc:
        raise AgentTeamsAttestationError("AgentTeams receipt signature is invalid") from exc
    return expected_key_id
