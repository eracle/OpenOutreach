from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class DealState(models.TextChoices):
    """OpenOutreach-owned funnel state for a Deal — the email-only pipeline.

    A lead is discovered and qualified without an email in hand (Lead Finder
    returns firmographics, not addresses), so the funnel first *finds* the email
    and then *talks*. The paid lookup is a two-leg async handshake (mirroring the
    retired connect→check_pending pair): a **submit** leg (``find_email``) fires
    the provider job and parks the deal at FINDING_EMAIL, and a **collect** leg
    (``collect_email``) polls that job. The job handle (``request_id``), the poll
    backoff, and the give-up deadline all live in the collect task's *payload* —
    never on the deal — so an in-flight lookup survives a daemon restart on the
    persisted task row, and the deal stays clean:

        QUALIFIED ─(GP rank gate)─▶ READY_TO_FIND_EMAIL ─(find_email/submit)─▶
            FINDING_EMAIL ─(collect_email/poll request_id)─▶
                hit:  READY_TO_EMAIL ─(email opener)─▶ EMAILED ⟲ (agentic follow-up)
                                                       ─▶ COMPLETED / FAILED
                miss: FAILED (reason="no email", outcome blank → ML-skipped)

    - **READY_TO_FIND_EMAIL** — passed the GP confidence gate; queued for the
      *paid* provider lookup (one credit per verified hit). The GP gate rations
      spend to leads the model is confident about, and the submit leg only fires
      when there's mailbox send-headroom for the result today (no email is
      resolved that can't be sent).
    - **FINDING_EMAIL** — a provider job is in flight; the deal is excluded from
      the candidate pool (so the next submit slot can't re-select it and
      double-charge) while the ``collect_email`` leg polls to termination. A free
      hub-cache hit skips this state entirely (READY_TO_FIND_EMAIL →
      READY_TO_EMAIL directly, no submit).
    - **READY_TO_EMAIL** — an address exists; queued for the opener. A cheap,
      *ungated* FIFO send-queue paced only by the per-box daily cap.
    - **EMAILED** — the opener has been sent; the agentic follow-up loop reads
      IMAP replies and decides send/wait/complete until the deal reaches a
      terminal COMPLETED / FAILED. Pacing is the agent's own ``follow_up_hours``.

    A lookup *miss* is terminal (FAILED, ``reason="no email"``, outcome blank so
    the ML labeler skips it rather than scoring it a bad fit); a *couldn't-run*
    (no key / API down at submit) leaves the deal at READY_TO_FIND_EMAIL to
    retry, and a job that never terminates within the poll deadline reverts
    FINDING_EMAIL → READY_TO_FIND_EMAIL for a fresh submit. The LinkedIn connect
    leg (READY_TO_CONNECT/PENDING/CONNECTED) was removed with the channel.

    NOTE: when adding a state, also add it to ``_STATE_LOG_STYLE`` in
    ``core/db/deals.py`` — an unmapped state logs as a red "ERROR" label.
    """
    QUALIFIED = "Qualified"
    READY_TO_FIND_EMAIL = "Ready to Find Email"
    FINDING_EMAIL = "Finding Email"
    READY_TO_EMAIL = "Ready to Email"
    SENDING_EMAIL = "Sending Email"
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
