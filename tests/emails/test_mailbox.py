# tests/emails/test_mailbox.py
"""MailboxManager.create_verified: SMTP auth is the gate for storing a box."""
import pytest
from unittest.mock import patch

from openoutreach.emails.models import Mailbox

_FIELDS = dict(
    from_address="joe@acme.com",
    password="app-pw",
    host="smtp.acme.com",
    port=465,
    imap_host="imap.acme.com",
    imap_port=993,
)


@pytest.mark.django_db
def test_create_verified_stores_box_when_auth_succeeds():
    with patch("openoutreach.emails.smtp.verify_auth", return_value=(True, "")) as auth:
        box, reason = Mailbox.objects.create_verified(**_FIELDS)

    auth.assert_called_once_with("smtp.acme.com", 465, "joe@acme.com", "app-pw")
    assert reason == ""
    assert box is not None
    # the SMTP login is the address itself, and every field round-trips
    assert box.username == "joe@acme.com"
    assert box.from_address == "joe@acme.com"
    assert (box.host, box.port, box.imap_host, box.imap_port) == (
        "smtp.acme.com", 465, "imap.acme.com", 993,
    )
    assert Mailbox.objects.count() == 1


@pytest.mark.django_db
def test_create_verified_stores_nothing_when_auth_rejected():
    with patch("openoutreach.emails.smtp.verify_auth", return_value=(False, "auth rejected (535)")):
        box, reason = Mailbox.objects.create_verified(**_FIELDS)

    assert box is None
    assert reason == "auth rejected (535)"
    assert Mailbox.objects.count() == 0


@pytest.mark.django_db
def test_create_verified_repairs_existing_box_in_place():
    with patch("openoutreach.emails.smtp.verify_auth", return_value=(True, "")):
        Mailbox.objects.create_verified(**_FIELDS)
        box, _ = Mailbox.objects.create_verified(**{**_FIELDS, "password": "new-pw"})

    assert Mailbox.objects.count() == 1
    assert box.password == "new-pw"
