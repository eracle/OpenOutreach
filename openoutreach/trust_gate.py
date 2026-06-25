"""trust_gate.py -- optional, tamper-evident receipt-on-send for outreach actions.

Every automated outreach carries a platform-ToS question: was this rate-limited, human-reviewed,
and not scraped? This mints a signed receipt at send time capturing the sender's attestation of
exactly that, plus a hash of the message. It records a sender attestation and binds it to the
message, verifiable from the certificate alone -- it is NOT a proof that the platform's ToS were
actually met, only tamper-evident evidence of what the sender attested and when.

OPTIONAL by design: the cryptography lives in the open-source `openagentontology` package
(Apache-2.0), imported lazily. If the package is absent, this returns an explicit unsigned stub
(never a fake signature), so adding this module imposes no new required dependency. A broken
install (not mere absence) is surfaced, not swallowed.

    pip install "openagentontology[pq]"   # Ed25519 + ML-DSA-65 + SLH-DSA legs

Usage:
    from openoutreach.trust_gate import mint_send_receipt, verify_receipt
    receipt = mint_send_receipt(
        recipient_ref="prospect-9f2a",
        message="Hi -- saw your post on agent governance, 15 min?",
        rate_limited=True, human_reviewed=True, scraped=False,
    )
"""
from __future__ import annotations

import hashlib
import importlib.util
from typing import Any, Dict


def _oao():
    """Return the openagentontology.receipt module, or None ONLY when the package is genuinely
    absent. Absence is checked with find_spec first; if the package is present but its import
    fails (a broken install), that error propagates so it is surfaced, not swallowed as
    'unsigned'."""
    if importlib.util.find_spec("openagentontology") is None:
        return None
    from openagentontology import receipt as _r  # type: ignore  # broken install raises here
    return _r


def _hash(s: str) -> str:
    """Full SHA-256 integrity hash. Tamper-evidence, not a privacy guarantee (low-entropy
    inputs can be brute-forced); do not treat it as redaction."""
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def build_send_manifest(recipient_ref: str, message: str, *, rate_limited: bool,
                        human_reviewed: bool, scraped: bool) -> Dict[str, Any]:
    """Assemble the ASCII-safe action manifest for one outreach send."""
    recipient_ref = str(recipient_ref).strip()
    if not recipient_ref:
        raise ValueError("recipient_ref must be a non-empty pseudonymous reference")
    return {
        "operation": "outreach_send",
        "recipient_ref": recipient_ref,          # pseudonymous, minimized (may still identify)
        "message_hash": _hash(message or ""),
        "tos_attestation": {
            "rate_limited": bool(rate_limited),
            "human_reviewed": bool(human_reviewed),
            "scraped": bool(scraped),
        },
        "policy": "sender ToS self-attestation",
    }


def mint_send_receipt(recipient_ref: str, message: str, *, rate_limited: bool = False,
                      human_reviewed: bool = False, scraped: bool = False) -> Dict[str, Any]:
    """Mint a (post-quantum, when available) receipt over one outreach send.

    Returns the receipt dict. If `openagentontology` is not installed, returns an explicit
    unsigned stub flagged `signed: False` -- the attestation is still hashed and carried, but
    honestly marked as not cryptographically signed.
    """
    manifest = build_send_manifest(
        recipient_ref, message,
        rate_limited=rate_limited, human_reviewed=human_reviewed, scraped=scraped)
    oao = _oao()
    if oao is None:
        return {
            "type": "AgentGovernanceReceipt",
            "decision": "OUTREACH_SENT",
            "evidence": {"ontology": manifest},
            "signed": False,
            "unsigned_reason": "install 'openagentontology[pq]' to sign this receipt",
        }
    return oao.mint_receipt(manifest, decision="OUTREACH_SENT")


def verify_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Verify a send receipt from the cert alone. Requires openagentontology to check sigs."""
    oao = _oao()
    if oao is None:
        return {"ok": False, "reason": "install 'openagentontology' to verify signatures"}
    return oao.verify_receipt(receipt)
