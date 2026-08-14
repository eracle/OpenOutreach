from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

from openoutreach.emails.sender import _deliver
from openoutreach.emails.sync import _connect
from openoutreach.emails.warmth import read_sent_history


class _FakeSmtp:
    def __init__(self):
        self.starttls_calls = []
        self.accepted_response = (250, b"queued")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self, **kwargs):
        self.starttls_calls.append(kwargs)

    def login(self, username, password):
        return None

    def send_message(self, message):
        return None


class _FakeImap:
    def login(self, username, password):
        return None


def _mailbox():
    return SimpleNamespace(
        host="smtp.example.com",
        port=587,
        imap_host="imap.example.com",
        imap_port=993,
        username="user@example.com",
        password="pw",
        from_address="user@example.com",
    )


def test_deliver_uses_verified_starttls_context():
    smtp = _FakeSmtp()

    with patch("openoutreach.emails.sender._SMTP", return_value=smtp), \
         patch("openoutreach.emails.delivery_policy.record_acceptance"), \
         patch("openoutreach.emails.delivery_policy.record_failure"):
        _deliver(_mailbox(), SimpleNamespace(), SimpleNamespace())

    assert len(smtp.starttls_calls) == 1
    context = smtp.starttls_calls[0]["context"]
    assert context.verify_mode.name == "CERT_REQUIRED"
    assert context.check_hostname is True


def test_connect_uses_verified_imap_context():
    mailbox = _mailbox()
    imap = _FakeImap()

    with patch("openoutreach.emails.sync.IMAPClient", return_value=imap) as client_cls:
        _connect(mailbox)

    context = client_cls.call_args.kwargs["ssl_context"]
    assert context.verify_mode.name == "CERT_REQUIRED"
    assert context.check_hostname is True


def test_read_sent_history_uses_verified_imap_context():
    mailbox = _mailbox()
    imap = _FakeImap()

    with patch("openoutreach.emails.warmth.imaplib.IMAP4_SSL", return_value=imap) as imap_ssl, \
         patch("openoutreach.emails.warmth._sent_folder", return_value="Sent"), \
         patch("openoutreach.emails.warmth._count_by_day", return_value=Counter()), \
         patch("openoutreach.emails.warmth._logout"):
        read_sent_history(mailbox)

    context = imap_ssl.call_args.kwargs["ssl_context"]
    assert context.verify_mode.name == "CERT_REQUIRED"
    assert context.check_hostname is True
