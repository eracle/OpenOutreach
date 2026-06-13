from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class DealState(models.TextChoices):
    """OpenOutreach-owned funnel state for a Deal.

    OpenOutreach owns these values, not linkedin_cli. The library's connect/status
    verbs only *observe* three of them off the LinkedIn UI — QUALIFIED, PENDING,
    CONNECTED — and hand them back as plain strings over the CLI boundary; every
    other state is written only here: READY_TO_CONNECT (passed the GP threshold)
    and COMPLETED/FAILED (outcome). String values match the library's UI states
    so lifting a returned string into this enum is a plain ``DealState(value)``
    lookup at the boundary.

    Email channel: enrichment is a *router*, not a gate. A lead with a resolved
    ``Lead.api_email`` is reached by email (handled off ``Deal.next_email_at``,
    excluded from the connect pool); a lead with no email flows through the
    connect funnel as its only door (and the connection harvests contact info
    on acceptance). No off-funnel email state — email progress reads from
    ``Lead.api_email`` + the outgoing email ``ChatMessage`` count.
    """
    QUALIFIED = "Qualified"
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
    profile_summary = models.JSONField(null=True, blank=True, default=None)
    chat_summary = models.JSONField(null=True, blank=True, default=None)
    creation_date = models.DateTimeField(default=timezone.now)
    update_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        lead_str = str(self.lead) if self.lead_id else "?"
        return f"{lead_str} [{self.state}]"
