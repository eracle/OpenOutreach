# openoutreach/emails/models.py
"""Mailbox: one SMTP sending inbox, imported from the provider's creds export."""
from __future__ import annotations

from django.db import models

from openoutreach.core.conf import DEFAULT_EMAIL_DAILY_LIMIT


class Mailbox(models.Model):
    """One SMTP inbox. host/port default to IceMail's Google Workspace boxes.

    A row exists only once its credentials pass the import auth-check — the
    provider has no health API, so the import is the gate. Send-time failures
    are not swallowed: a bad send fails its task and is retried, the box is
    left untouched (re-import with fixed credentials to repair it).
    """

    host = models.CharField(max_length=255, default="smtp.gmail.com")
    port = models.PositiveIntegerField(default=587)
    username = models.CharField(max_length=320, unique=True)
    password = models.CharField(max_length=255)
    from_address = models.EmailField(max_length=320)
    # Warm-safe sends/day for this box, set at email onboarding (mirrors the
    # LinkedIn connect_daily_limit). Enforced at send time, per box.
    daily_limit = models.PositiveIntegerField(default=DEFAULT_EMAIL_DAILY_LIMIT)

    class Meta:
        verbose_name_plural = "Mailboxes"

    def __str__(self):
        return self.from_address or self.username
