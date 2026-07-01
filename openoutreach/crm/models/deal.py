from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class DealState(models.TextChoices):
    """OpenOutreach-owned funnel state for a Deal.

    OpenOutreach owns these values, not linkedin_cli. The library's connect/status
    verbs only *observe* three of them off the LinkedIn UI — QUALIFIED, PENDING,
    CONNECTED — and hand them back as plain strings over the CLI boundary; every
    other state is written only here: READY_TO_CONNECT (passed the GP threshold),
    the email fork (READY_TO_EMAIL/EMAILED), and COMPLETED/FAILED (outcome). The
    three UI-observed string values match the library's so lifting a returned
    string into this enum is a plain ``DealState(value)`` lookup at the boundary.

    Email channel: enrichment is a *router*, not a gate, and the route is an
    explicit FSM fork — the state *is* the routing:

        HIT  ─▶ READY_TO_EMAIL ─(EMAIL task)─▶ EMAILED   (Layer-1 quasi-terminal)
        MISS ─▶ stays QUALIFIED ─(GP gate)─▶ READY_TO_CONNECT ─▶ … ─▶ CONNECTED

    On a finder hit the qualify router transitions QUALIFIED → READY_TO_EMAIL (a
    cheap, *ungated* send-queue — any qualified lead with an address, FIFO, paced
    only by the per-box cap; unlike READY_TO_CONNECT it is NOT a GP confidence
    gate). The single Layer-1 send moves it to EMAILED, a quasi-terminal state
    that rests until a human sets an Outcome (no inbound reading yet). A miss,
    finder-off, or couldn't-run leaves the deal QUALIFIED so the GP gate can
    promote it to READY_TO_CONNECT — its only door — and the connection harvests
    contact info on acceptance. The two fork states encode the one-shot guarantee
    in the state column: the email pool holds only READY_TO_EMAIL, so a deal is
    sent exactly once and can never double-send.
    """
    QUALIFIED = "Qualified"
    READY_TO_FIND_EMAIL = "Ready to Find Email"
    READY_TO_EMAIL = "Ready to Email"
    EMAILED = "Emailed"
    READY_TO_CONNECT = "Ready to Connect"
    PENDING = "Pending"
    CONNECTED = "Connected"
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
    connect_attempts = models.IntegerField(default=0)
    backoff_hours = models.IntegerField(default=0)
    next_check_pending_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # Email channel (Layer 1 = single outbound touch, no follow-up cadence yet).
    # The mailbox that sent the email, bound to the deal: it's the per-box-cap
    # counting key (ChatMessage.filter(deal__mailbox=box)), the reply anchor, and
    # the sticky thread box once follow-ups land with inbound reply-reading.
    mailbox = models.ForeignKey(
        "emails.Mailbox", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="deals",
    )
    # The email's subject — set when the single email is sent; the reuse source
    # ("Re: …") once follow-ups land. The agent generates it once.
    email_subject = models.CharField(max_length=300, blank=True, default="")
    # When the single Layer-1 email was sent. The per-box daily cap counts deals
    # sent since local midnight; also the audit timestamp. Null until sent.
    email_sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # RFC-5322 Message-ID of the sent email. The Layer-2 correlation key: a reply's
    # In-Reply-To/References matches it back to this exact campaign/deal (the
    # disambiguator when one lead is emailed across two campaigns). Null until sent.
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
