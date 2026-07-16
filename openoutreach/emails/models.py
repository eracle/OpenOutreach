# openoutreach/emails/models.py
"""Mailbox: one SMTP sending inbox, imported from the provider's creds export."""
from __future__ import annotations

from django.db import models
from django.utils import timezone

from openoutreach.core.conf import DEFAULT_EMAIL_DAILY_LIMIT


class MailboxManager(models.Manager):
    """Pool-level send pacing — the daily-cap accounting the task and planner share."""

    def remaining_today(self) -> int:
        """Total sends left across the pool today (Σ per-box headroom).

        0 when no boxes exist or every box is at its cap.
        """
        return sum(box.headroom_today() for box in self.all())

    def least_loaded_under_cap(self):
        """The under-cap box with the most headroom today, or None if all are capped."""
        ranked = [(box, sent) for box in self.all()
                  if (sent := box.sent_today()) < box.daily_limit]
        if not ranked:
            return None
        return min(ranked, key=lambda pair: pair[1])[0]

    def create_verified(
        self,
        *,
        from_address: str,
        password: str,
        host: str,
        port: int,
        imap_host: str,
        imap_port: int,
        daily_limit: int = DEFAULT_EMAIL_DAILY_LIMIT,
    ) -> tuple["Mailbox | None", str]:
        """Auth-check a mailbox over SMTP, then store it — the connect gate.

        The provider has no health API, so the SMTP login *is* the gate: nothing
        is stored unless auth succeeds, so a row always means working credentials.
        The SMTP username is the address itself (a mailbox you own logs in as
        itself; a distinct login is a relay case we don't support). Returns
        ``(mailbox, "")`` on success or ``(None, reason)`` when auth is rejected.
        Re-entering an address repairs that box in place (``update_or_create``).
        """
        from openoutreach.emails.smtp import verify_auth

        ok, reason = verify_auth(host, port, from_address, password)
        if not ok:
            return None, reason
        box, _ = self.update_or_create(
            username=from_address,
            defaults={
                "password": password,
                "from_address": from_address,
                "host": host,
                "port": port,
                "imap_host": imap_host,
                "imap_port": imap_port,
                "daily_limit": daily_limit,
            },
        )
        return box, ""


class Mailbox(models.Model):
    """One SMTP inbox, connected field-by-field at onboarding.

    A row exists only once its credentials pass the SMTP auth-check
    (``objects.create_verified``) — the provider has no health API, so the login
    is the gate. Send-time failures are not swallowed: a bad send fails its task
    and is retried, the box is left untouched (re-enter fixed credentials to
    repair it). host/port/imap default to IceMail's Google Workspace boxes; any
    other provider overrides them at entry.
    """

    host = models.CharField(max_length=255, default="smtp.gmail.com")
    port = models.PositiveIntegerField(default=587)
    # IMAP read side, for the agentic follow-up loop's reply-reader
    # (emails/inbox.py); authenticates with the same address + app password as SMTP.
    imap_host = models.CharField(max_length=255, default="imap.gmail.com")
    imap_port = models.PositiveIntegerField(default=993)
    # The SMTP login — always the address itself (a mailbox you own logs in as
    # itself), kept as its own column for the unique constraint.
    username = models.CharField(max_length=320, unique=True)
    password = models.CharField(max_length=255)
    from_address = models.EmailField(max_length=320)
    # Sign-off appended verbatim to every email sent from this box (opener and
    # follow-ups alike). Per box, not global: the signature is part of the sending
    # identity, and a second box is usually a second identity. The email agent is
    # told never to sign (prompts/_outreach_base.j2), so this is the only sign-off.
    # NULL and "" are distinct: NULL means never asked (the onboarding signature
    # step backfills those), "" means the operator declined one and must stick —
    # collapsing them would re-ask a declining operator on every startup.
    signature = models.TextField(blank=True, null=True, default=None)
    # Warm-safe sends/day for this box, set at email onboarding. Enforced at
    # send time, per box (counts this box's outgoing messages since midnight).
    daily_limit = models.PositiveIntegerField(default=DEFAULT_EMAIL_DAILY_LIMIT)

    objects = MailboxManager()

    class Meta:
        verbose_name_plural = "Mailboxes"

    def __str__(self):
        return self.from_address or self.username

    def sent_today(self) -> int:
        """Emails this box has sent since local midnight (the per-box cap ledger).

        Counts **outgoing ChatMessages** on this box's deals, not deals: the
        agentic loop sends many emails per deal (opener + every follow-up reply),
        and each outbound email is one row, so the message count is the true send
        volume. LinkedIn ChatMessages never carry a mailbox (``deal.mailbox`` is
        null), so they are naturally excluded.
        """
        from openoutreach.chat.models import ChatMessage

        midnight = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return ChatMessage.objects.filter(
            deal__mailbox=self, is_outgoing=True, creation_date__gte=midnight,
        ).count()

    def headroom_today(self) -> int:
        """Sends this box has left today before hitting ``daily_limit``."""
        return max(0, self.daily_limit - self.sent_today())


def has_mailbox() -> bool:
    """True when ≥1 mailbox is configured — i.e. email is a viable channel to
    send from. Gates the find-email leg: with no mailbox there's nothing to send,
    so resolving an address (and spending a credit) is pointless — the leg stays
    idle until a mailbox is connected."""
    return Mailbox.objects.exists()
