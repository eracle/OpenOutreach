from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class DealState(models.TextChoices):
    """OpenOutreach-owned funnel state for a Deal — the finder's pipeline.

    A lead is discovered and qualified without an email in hand (Lead Finder
    returns firmographics, not addresses), so the funnel qualifies first and then
    *optionally* resolves an address. The paid lookup is a two-step async handshake:
    ``buy_address`` fires the provider job and parks the deal at FINDING_EMAIL, and
    ``check_lookup`` polls it. The job handle (``lookup_request_id``), the poll
    backoff (``lookup_attempt`` + ``not_before``) and every other schedule this deal
    is subject to live **on this row** — the state is the queue, so a deal carries
    its own timing and nothing external gates it:

        QUALIFIED ─(GP rank gate)─▶ READY_TO_FIND_EMAIL ─(buy_address)─▶
            FINDING_EMAIL ─(check_lookup on lookup_request_id)─▶
                hit:  RESOLVED (an address is in hand — terminal, and exportable)
                miss: NO_EMAIL_BETTERCONTACT (provider found no address — a distinct
                      terminal, not a failure; still a fit positive → ML label=1)

    **Every state below RESOLVED is terminal, and that is the shape of the product.**
    The finder's output is a row in a file, not a conversation: a deal reaches its
    verdict, optionally gains an address, and stops. It is read from there by
    ``core/export.py`` and leaves over a one-way boundary.

    - **QUALIFIED** — the LLM judged this lead a fit and wrote ``reason``. Exportable
      from this moment; an address is an enrichment on top, never a precondition.
    - **READY_TO_FIND_EMAIL** — passed the GP confidence gate; queued for the *paid*
      provider lookup (one credit per verified hit). The gate rations spend to leads
      the model is confident about, **and nothing else** — it is not a quality score
      and is deliberately absent from the export.
    - **FINDING_EMAIL** — a provider job is in flight; the deal is excluded from the
      candidate pool (so the next submit slot can't re-select it and double-charge)
      while ``check_lookup`` polls to termination. A free hub-cache hit skips this
      state entirely (READY_TO_FIND_EMAIL → RESOLVED directly, no submit).
    - **RESOLVED** — an address is in hand. This is where a fully-enriched deal comes
      to rest, and resting costs nothing because nothing iterates it.

    A lookup *miss* is terminal (NO_EMAIL_BETTERCONTACT — the provider resolved no
    address). It is a *distinct* terminal from FAILED so downstream work can build on
    it (e.g. retry once another enrichment provider exists). The lead was an LLM fit
    positive, so the ML labeler keeps it as label=1 — only reachability failed, not
    fit. A *couldn't-run* (no key / API down at submit) leaves the deal at
    READY_TO_FIND_EMAIL to retry. A job that has not terminated is *never* abandoned:
    ``check_lookup`` doubles ``not_before`` on the same request_id and the deal simply
    waits here, because a timeout is evidence about the provider, not about whether
    this person has a findable address. (The deadline that used to revert
    FINDING_EMAIL → READY_TO_FIND_EMAIL made an outage worse — the deal returned to
    the pool and bought a *second* job for the same lead.) That wait is strictly this
    deal's own: ``not_before`` gates one row.

    FAILED (+ ``Outcome.WRONG_FIT``) is the LLM's own campaign-scoped rejection, and
    it is the ML labeler's only negative. Note it is a **different column** from
    ``Lead.disqualified``, the permanent account-level exclusion — the export filters
    both, and the first shipped version of it filtered only the second.

    **The send states are gone** (READY_TO_EMAIL, EMAILED, UNSUBSCRIBED, COMPLETED),
    along with the whole ``emails`` app. They described a conversation, and the
    conversation is no longer ours: the sending half moved to OpenEmailSequence and
    the opt-out duty went with it. A finder that never contacts anyone is not the
    sender under CAN-SPAM / GDPR / CASL. The retired connect leg (READY_TO_CONNECT/
    PENDING/CONNECTED) was removed with its channel before that.

    NOTE: when adding a state, also add it to ``_STATE_LOG_STYLE`` in
    ``core/db/deals.py`` — an unmapped state logs as a red "ERROR" label.
    """
    QUALIFIED = "Qualified"
    READY_TO_FIND_EMAIL = "Ready to Find Email"
    FINDING_EMAIL = "Finding Email"
    RESOLVED = "Resolved"
    NO_EMAIL_BETTERCONTACT = "No Email (BetterContact)"
    FAILED = "Failed"


class Outcome(models.TextChoices):
    """Why a deal ended. Only the finder's own verdict survives.

    The reply outcomes this used to carry — ``converted``, ``not_interested``,
    ``no_budget``, ``has_solution``, ``bad_timing``, ``unresponsive`` — described how
    a negotiation went, and a negotiation is the sender's. They left with
    ``emails/*``. Keeping them would have been worse than useless: they depend on the
    message, the timing and the sender's skill, so ingesting them means inferring
    "was this a good lead" from "did that email work".

    That also retires a live mislabelling. ``emails/steps/reply.py`` wrote the real
    outcome onto ``deal.outcome`` and moved the deal to COMPLETED — *not* FAILED — and
    ``get_labeled_arrays`` labels every non-FAILED deal **1**, so a lead who replied
    "not interested" was training the model as a positive. It resolved by deletion,
    exactly as planned.
    """
    WRONG_FIT = "wrong_fit"
    UNKNOWN = "unknown"


class Deal(models.Model):
    class Meta:
        verbose_name = _("Deal")
        verbose_name_plural = _("Deals")
        constraints = [
            models.UniqueConstraint(fields=["lead", "campaign"], name="unique_deal_per_campaign"),
        ]
        # The cycle's one query: every step selects on state, and the one that waits
        # narrows it by ``not_before``.
        indexes = [
            models.Index(fields=["state", "not_before"], name="deal_state_not_before_idx"),
        ]

    lead = models.ForeignKey("Lead", on_delete=models.CASCADE)
    campaign = models.ForeignKey(
        "core.Campaign", on_delete=models.CASCADE, related_name="deals",
    )
    state = models.CharField(
        max_length=32,
        choices=DealState.choices,
        default=DealState.QUALIFIED,
    )
    outcome = models.CharField(
        max_length=20,
        choices=Outcome.choices,
        blank=True,
        default="",
    )
    # Why the LLM chose (or rejected) this lead, in its own words. **The product.**
    # Everyone exports rows; almost nobody exports why this lead. It rides the export
    # as a custom variable and it is the only fit signal that leaves — there is no
    # score column, deliberately.
    reason = models.TextField(blank=True, default="")
    # "Do not touch this deal before this time" — the only schedule a deal carries,
    # and it gates *this row alone*. Null means always eligible. Written by the one
    # step that waits: the lookup poll's backoff (``check_lookup``).
    not_before = models.DateTimeField(null=True, blank=True, db_index=True)
    # The in-flight paid lookup: the provider's job handle and how many times it has
    # been polled (the backoff exponent). Both live here rather than in an external
    # row, so a restart resumes the job from the deal itself and a stalled provider
    # can never hold up anything but this lead.
    lookup_request_id = models.CharField(max_length=64, blank=True, default="")
    lookup_attempt = models.PositiveSmallIntegerField(default=0)
    creation_date = models.DateTimeField(default=timezone.now)
    update_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        lead_str = str(self.lead) if self.lead_id else "?"
        return f"{lead_str} [{self.state}]"
