# tests/emails/test_smtp.py
"""Auth-only SMTP check — mock smtplib at the boundary."""
import smtplib
from unittest.mock import patch

from openoutreach.emails.smtp import verify_auth


def test_auth_ok_starttls_on_587():
    with patch("smtplib.SMTP") as smtp_cls:
        conn = smtp_cls.return_value.__enter__.return_value
        conn.has_extn.return_value = True
        ok, message = verify_auth("smtp.gmail.com", 587, "u", "p")
    assert ok and message == "ok"
    conn.starttls.assert_called_once()
    conn.login.assert_called_once_with("u", "p")


def test_auth_ok_implicit_ssl_on_465():
    """A 465-only box connects over SMTP_SSL and never calls STARTTLS.

    This is the case the old hard-coded ``starttls()`` could never pass, which
    made onboarding reject a working mailbox and re-ask forever.
    """
    with patch("smtplib.SMTP_SSL") as ssl_cls, patch("smtplib.SMTP") as plain_cls:
        conn = ssl_cls.return_value.__enter__.return_value
        ok, message = verify_auth("smtp.fastmail.com", 465, "u", "p")
    assert ok and message == "ok"
    plain_cls.assert_not_called()
    conn.starttls.assert_not_called()
    conn.login.assert_called_once_with("u", "p")


def test_login_password_rejection_surfaces_app_password_hint():
    error = smtplib.SMTPAuthenticationError(534, b"application-specific password required")
    with patch("smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value.login.side_effect = error
        ok, message = verify_auth("smtp.gmail.com", 587, "u", "p")
    assert not ok and "app password" in message and "534" in message


def test_connection_failure_is_reported_not_raised():
    with patch("smtplib.SMTP", side_effect=OSError("no route to host")):
        ok, message = verify_auth("smtp.gmail.com", 587, "u", "p")
    assert not ok and "connection failed" in message
