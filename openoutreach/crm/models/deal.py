from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class DealState(models.TextChoices):
    """OpenOutreach-owned funnel state for a Deal — the email-only pipeline.

    A lead is discovered and qualified without an email in hand (Lead Finder
    returns firmographics, not addresses), so the funnel first *finds* the email
    and then *talks*:

        QUALIFIED ─(GP rank gate)─▶ READY_TO_FIND_EMAIL ─(find_email task)─▶
            hit:  READY_TO_EMAIL ─(email opener)─▶ EMAILED ⟲ (agentic follow-up)
                                                   ─▶ COMPLETED / FAILED
            miss: FAILED (reason="no email", outcome blank → ML-skipped)

    - **READY_TO_FIND_EMAIL** — passed the GP confidence gate; queued for the
      *paid* BetterContact lookup (one credit per verified hit), so the gate
      rations spend to leads the model is confident about.
    - **READY_TO_EMAIL** — an address exists; queued for the opener. A cheap,
      *ungated* FIFO send-queue paced only by the per-box daily cap.
    - **EMAILED** — the opener has been sent; the agentic follow-up loop reads
      IMAP replies and decides send/wait/complete until the deal reaches a
      terminal COMPLETED / FAILED. Pacing is the agent's own ``follow_up_hours``.

    A ``find_email`` *miss* is terminal (FAILED, ``reason="no email"``, outcome
    blank so the ML labeler skips it rather than scoring it a bad fit); a
    *couldn't-run* (no key / out of credits / API down) leaves the deal at
    READY_TO_FIND_EMAIL to retry. The LinkedIn connect leg
    (READY_TO_CONNECT/PENDING/CONNECTED) was removed with the channel.
    """
    QUALIFIED = "Qualified"
    READY_TO_FIND_EMAIL = "Ready to Find Email"
    READY_TO_EMAIL = "Ready to Email"
    EMAILED = "Emailed"
    COMPLETED = "Completed"
    FAILED = "Failed"


class Outcome(models.TextChoices):
    CONVERTED = "converted"
    NOT_INTERESTED = "not_interested"
    WRONG_FIT = "wrong_fit"
    NO_BUDGET = "no_budget"
    HAS_SOLUTION = "has_solution"
    BAD_TIMING = "bad_timing"
    UNRESPONSIVE = "unresponsive"
    UNKNOWN = "unknown"


class Deal(models.Model):
    class Meta:
        verbose_name = _("Deal")
        verbose_name_plural = _("Deals")
        constraints = [
            models.UniqueConstraint(fields=["lead", "campaign"], name="unique_deal_per_campaign"),
        ]

    lead = models.ForeignKey("Lead", on_delete=models.CASCADE)
    campaign = models.ForeignKey(
        "core.Campaign", on_delete=models.CASCADE, related_name="deals",
    )
    state = models.CharField(
        max_length=20,
        choices=DealState.choices,
        default=DealState.QUALIFIED,
    )
    outcome = models.CharField(
        max_length=20,
        choices=Outcome.choices,
        blank=True,
        default="",
    )
    reason = models.TextField(blank=True, default="")
    # Email channel. The mailbox that sent the opener, bound to the deal: it's the
    # per-box-cap counting key (ChatMessage.filter(deal__mailbox=box)), the reply
    # anchor, and the sticky thread box for the agentic follow-up loop.
    mailbox = models.ForeignKey(
        "emails.Mailbox", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="deals",
    )
    # The opener's subject, generated once by the agent; the follow-up loop reuses
    # it as "Re: …" on every threaded reply.
    email_subject = models.CharField(max_length=300, blank=True, default="")
    # When the opener was sent — the audit timestamp (the per-box daily cap counts
    # outgoing ChatMessages, not this field; see Mailbox.sent_today). Null until sent.
    email_sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # RFC-5322 Message-ID of the opener — the immutable thread root. A reply's
    # In-Reply-To/References carries it, so the IMAP reader matches replies back to
    # this exact campaign/deal (the disambiguator when one lead is emailed across
    # two campaigns). Null until sent.
    email_message_id = models.CharField(max_length=300, blank=True, default="")
    # When the agentic email follow-up loop should next touch this EMAILED deal
    # (read replies + let the agent decide send/wait/complete). The agent owns
    # the pace: each run stamps ``now + decision.follow_up_hours``; the opener
    # seeds it on the first send. The scheduler drains EMAILED deals whose clock
    # is due. Null until the deal reaches EMAILED.
    next_follow_up_at = models.DateTimeField(null=True, blank=True, db_index=True)
    profile_summary = models.JSONField(null=True, blank=True, default=None)
    chat_summary = models.JSONField(null=True, blank=True, default=None)
    creation_date = models.DateTimeField(default=timezone.now)
    update_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        lead_str = str(self.lead) if self.lead_id else "?"
        return f"{lead_str} [{self.state}]"
