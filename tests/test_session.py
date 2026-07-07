# tests/test_session.py
"""The operator session helpers.

The regression this locks down: the operator ``User.email`` is the single source
of truth for who the daemon sends (and BCCs itself) as. ``reconcile_operator_email``
self-heals a legacy/blank/stale email from the connected mailbox on startup, so
the BCC self-copy and the contacts give-back never silently no-op.
"""
import pytest


def _make_operator(email):
    from django.contrib.auth.models import User

    return User.objects.create(username="op", email=email, is_staff=True, is_active=True)


@pytest.mark.django_db
def test_reconcile_binds_blank_operator_email_to_mailbox():
    from openoutreach.core.session import reconcile_operator_email
    from openoutreach.emails.models import Mailbox

    Mailbox.objects.create(username="me@box.com", from_address="me@box.com", password="p")
    user = _make_operator(email="")

    assert reconcile_operator_email() is True
    user.refresh_from_db()
    assert user.email == "me@box.com"


@pytest.mark.django_db
def test_reconcile_is_idempotent_when_already_matching():
    from openoutreach.core.session import reconcile_operator_email
    from openoutreach.emails.models import Mailbox

    Mailbox.objects.create(username="me@box.com", from_address="me@box.com", password="p")
    _make_operator(email="me@box.com")

    assert reconcile_operator_email() is False


@pytest.mark.django_db
def test_reconcile_noops_before_a_mailbox_exists():
    """A fresh install still mid-onboarding has an operator-less/mailbox-less DB;
    reconcile must return cleanly rather than crash."""
    from openoutreach.core.session import reconcile_operator_email

    _make_operator(email="")  # no Mailbox yet
    assert reconcile_operator_email() is False
