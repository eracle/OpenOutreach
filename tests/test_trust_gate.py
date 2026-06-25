"""test_trust_gate.py -- optional receipt-on-send mints + verifies (or degrades honestly)."""
from __future__ import annotations

import copy

import pytest

from openoutreach.trust_gate import (
    build_send_manifest,
    mint_send_receipt,
    verify_receipt,
    _oao,
)

_HAS_OAO = _oao() is not None


def test_requires_recipient_ref():
    with pytest.raises(ValueError):
        build_send_manifest("", "hi", rate_limited=True, human_reviewed=True, scraped=False)


def test_message_is_hashed_not_stored():
    m = build_send_manifest("p-1", "secret pitch text", rate_limited=True,
                            human_reviewed=True, scraped=False)
    assert m["message_hash"].startswith("sha256:") and len(m["message_hash"]) == 71
    assert "secret pitch text" not in str(m)


def test_unsigned_stub_when_absent(monkeypatch):
    monkeypatch.setattr("openoutreach.trust_gate._oao", lambda: None)
    r = mint_send_receipt("p-1", "hi", rate_limited=True, human_reviewed=True)
    assert r["signed"] is False and "signature_b64" not in r


@pytest.mark.skipif(not _HAS_OAO, reason="openagentontology not installed")
def test_real_receipt_mints_and_verifies():
    r = mint_send_receipt("prospect-9f2a", "Hi, 15 min?", rate_limited=True, human_reviewed=True)
    assert verify_receipt(r)["ok"] is True
    assert r["evidence"]["ontology"]["tos_attestation"]["rate_limited"] is True


@pytest.mark.skipif(not _HAS_OAO, reason="openagentontology not installed")
def test_tamper_on_attestation_breaks_verification():
    r = mint_send_receipt("prospect-9f2a", "Hi, 15 min?", rate_limited=True, human_reviewed=True)
    t = copy.deepcopy(r)
    # an operator flips "scraped" to hide a violation after signing
    t["evidence"]["ontology"]["tos_attestation"]["scraped"] = True
    assert verify_receipt(t)["ok"] is False
