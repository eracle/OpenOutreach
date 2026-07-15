# openoutreach/core/models.py
from __future__ import annotations

import numpy as np
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class SiteConfig(models.Model):
    """Singleton model for global site configuration (LLM keys, etc.)."""

    # The model is a pydantic-ai model identifier in `provider:model` form
    # (e.g. ``anthropic:claude-sonnet-4-5-20250929``, ``openai:gpt-4o``,
    # ``groq:llama-3.3-70b``). The provider lives inside this single string —
    # there is no separate provider field to drift out of sync. A bare model
    # name whose prefix is unambiguous (``gpt``/``o1``/``o3``→openai,
    # ``claude``→anthropic, ``gemini``→google) is also accepted; everything
    # else must carry an explicit prefix. See core/llm.py:split_model_id.
    ai_model = models.CharField(
        max_length=200, blank=True, default="",
        help_text="provider:model, e.g. anthropic:claude-sonnet-4-5-20250929",
    )
    llm_api_key = models.CharField(max_length=500, blank=True, default="")
    # Only consulted for the openai_compatible provider (OpenRouter / Together / Ollama / vLLM).
    llm_api_base = models.CharField(max_length=500, blank=True, default="")

    # BetterContact email-finder key; blank disables enrichment (see emails/bettercontact.py).
    bettercontact_api_key = models.CharField(max_length=500, blank=True, default="")

    # Central contacts service (see openoutreach/contacts/). The token is earned
    # on the first contribution and persisted here — never in the repo; blank
    # means "not registered yet" (resolve misses until the first give-back mints
    # it). The URL is blank by default (falls back to DEFAULT_CONTACTS_API_URL).
    contacts_api_token = models.CharField(max_length=500, blank=True, default="")
    contacts_api_url = models.CharField(max_length=500, blank=True, default="")

    # The operator's ISO-3166 alpha-2 country, collected at onboarding (self-hosted
    # = one operator, so it lives on the config singleton, not a separate account
    # model — identity like email/name stays on the Django ``User``). Drives the
    # active-hours timezone (tz_country) and the email-jurisdiction rules
    # (core/geo.py): newsletter opt-in default + whether we contribute to the
    # contacts store (derived, ``not is_eea_located`` — never a stored toggle).
    country_code = models.CharField(max_length=2, blank=True, default="")

    class Meta:
        verbose_name = "Site Configuration"
        verbose_name_plural = "Site Configuration"

    def __str__(self):
        return "Site Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "SiteConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Campaign(models.Model):
    name = models.CharField(max_length=200, unique=True)
    users = models.ManyToManyField(User, blank=True, related_name="campaigns")
    product_docs = models.TextField(blank=True)
    campaign_target = models.TextField(blank=True)
    booking_link = models.URLField(max_length=500, blank=True)
    is_freemium = models.BooleanField(default=False)
    action_fraction = models.FloatField(default=0.2)
    seed_public_ids = models.JSONField(default=list, blank=True)
    model_blob = models.BinaryField(null=True, blank=True)
    # ISO-3166 alpha-2 target country for this campaign's leads — the contacts
    # geo-gate stamp put on every discovered Lead. A stable ICP attribute (one
    # country per campaign), so it lives here rather than duplicated on each query
    # node. Set by ``frontier.ensure_seed`` from the LLM's ICP spec. (Discovery is
    # a graph search over ``DiscoveryQuery`` nodes — the retired
    # ``icp_filters``/``discovery_offset`` single-cursor state was migrated into a
    # seed node and dropped in migration 0007.)
    country_code = models.CharField(max_length=2, blank=True, default="")

    def __str__(self):
        return self.name


class TaskQuerySet(models.QuerySet):
    def _priority_order(self):
        """Opportunity-cost rank for a single worker: value-to-funnel first.

        Every task run defers the rest, so ready work is ranked by what it's
        worth: a live reply (``follow_up``) and a cheap poll that unblocks a deal
        (``collect_email``) preempt a cold opener (``email``), which preempts
        starting new *paid* speculative work (``find_email``). This orders
        *claiming* among ready tasks only — it must never drive the sleep clock
        (see ``seconds_to_next``)."""
        return models.Case(
            models.When(task_type=Task.TaskType.FOLLOW_UP, then=models.Value(0)),
            models.When(task_type=Task.TaskType.COLLECT_EMAIL, then=models.Value(1)),
            models.When(task_type=Task.TaskType.EMAIL, then=models.Value(2)),
            default=models.Value(3),
            output_field=models.IntegerField(),
        )

    def pending(self):
        """PENDING tasks, highest funnel-value first, then oldest-scheduled."""
        return self.filter(status=Task.Status.PENDING).order_by(
            self._priority_order(), "scheduled_at",
        )

    def claim_next(self) -> "Task | None":
        """The highest-priority task that is due (its ``scheduled_at`` has arrived)."""
        return self.pending().filter(scheduled_at__lte=timezone.now()).first()

    def seconds_to_next(self) -> float | None:
        """Seconds until the *earliest-scheduled* pending task, or None if empty.

        Ordered by ``scheduled_at`` alone — NOT by priority — so the daemon sleeps
        to the soonest due-time and never oversleeps a sooner low-priority task
        (a ``find_email`` due in 1m) sitting behind a far-future high-priority one
        (a ``follow_up`` due in 6h)."""
        next_task = (
            self.filter(status=Task.Status.PENDING)
            .order_by("scheduled_at")
            .only("scheduled_at")
            .first()
        )
        if next_task is None:
            return None
        return max((next_task.scheduled_at - timezone.now()).total_seconds(), 0)


class Task(models.Model):
    class TaskType(models.TextChoices):
        FIND_EMAIL = "find_email"        # submit leg — fire a paid lookup
        COLLECT_EMAIL = "collect_email"  # poll leg — check an in-flight lookup (payload carries request_id)
        FOLLOW_UP = "follow_up"
        EMAIL = "email"

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"

    task_type = models.CharField(max_length=20, choices=TaskType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    scheduled_at = models.DateTimeField()
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = TaskQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(
                fields=["status", "scheduled_at"],
                name="core_task_status_sched_idx",
            ),
        ]

    def __str__(self):
        return f"{self.task_type} [{self.status}] scheduled={self.scheduled_at}"

    def mark_running(self):
        self.status = self.Status.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def mark_completed(self):
        self.status = self.Status.COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])

    def mark_failed(self):
        self.status = self.Status.FAILED
        self.save(update_fields=["status"])


class DiscoveryQuery(models.Model):
    """One node in a campaign's discovery graph — a Lead Finder query.

    A node is ``(params, offset)``: a Lead Finder filter dict at a pagination
    depth. Discovery is a best-first search over these nodes (see
    ``core/pipeline/frontier.py``), replacing the old single
    ``(Campaign.icp_filters, discovery_offset)`` cursor (retired in migration
    0007, which seeded each campaign's first node from that cursor).

    Lifecycle (``status``):

    - ``PENDING`` — enqueued by the seed or an expansion, not yet fetched. The
      only fetchable state; picked by its parent's rank (it has no leads of its
      own yet).
    - ``FETCHED`` — its Lead Finder page has been pulled once; its leads exist
      (linked back via ``Lead.discovered_by``) and ``score`` is stored. Never
      re-fetched — the per-move re-rank re-scores this node's leads against the
      current GP, no API call and no stored embedding matrix (the embeddings
      already live on each ``Lead``). Stays on the frontier as a scored reference
      its ``PENDING`` children inherit rank from, and as an expansion origin,
      until retired.
    - ``RETIRED`` — a dry page, or evicted by the frontier size-cap. Off the
      frontier.

    ``score`` is the node's value: the **number of its leads the GP would accept**
    for the paid pipeline — i.e. how many clear the acceptance gate
    ``P(f>0.5) > min_gp_confidence`` (see ``ready_pool.count_accepted``). It is
    meaningful only once the qualifier is in exploit mode (``n_neg > n_pos``);
    before that the frontier expands broad and unranked and ``score`` stays null.

    This score **steers discovery only** — it decides which query region to
    explore next. It never gates a lead: every saved lead advances through
    ``qualify → promote_to_ready → find_email → enrich`` on its own P, over the
    global pool, regardless of which node discovered it. The frontier reuses the
    acceptance threshold merely as a yardstick for a query's promise.
    """

    class Status(models.TextChoices):
        PENDING = "pending"    # enqueued, not yet fetched — the fetchable state
        FETCHED = "fetched"    # page pulled once; embeddings + score stored
        RETIRED = "retired"    # dry page, or evicted by the size-cap

    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="discovery_queries",
    )
    # The Lead Finder filter dict (the same shape as the old icp_filters["filters"]).
    params = models.JSONField(default=dict)
    # sha256 of the canonicalized params — the node-identity key for dedup, so two
    # equivalent filter dicts (key order aside) never both enter the frontier.
    params_hash = models.CharField(max_length=64)
    offset = models.IntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    # The node's value: count of its leads the GP would accept (P(f>0.5) >
    # min_gp_confidence), recomputed each re-rank from the leads' embeddings. Null
    # before the node is fetched or while the qualifier is pre-exploit (untrusted).
    score = models.FloatField(null=True, blank=True)
    # The node this was deepened or mutated from; null for a seed node.
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Discovery Query"
        verbose_name_plural = "Discovery Queries"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "params_hash", "offset"],
                name="uniq_discovery_node",
            ),
        ]
        indexes = [
            models.Index(fields=["campaign", "status"], name="discovery_camp_status_idx"),
        ]

    def __str__(self):
        return f"DiscoveryQuery#{self.pk} [{self.status}] offset={self.offset} score={self.score}"

    @property
    def lead_embeddings(self) -> np.ndarray:
        """(n, 384) matrix of this node's first-touch leads' embeddings.

        The re-rank scores the node from these against the current GP — no stored
        matrix, no re-fetch. Empty (0, 384) when the node has no embedded leads.
        """
        embs = [
            np.frombuffer(bytes(e), dtype=np.float32)
            for e in self.leads.filter(embedding__isnull=False).values_list("embedding", flat=True)
        ]
        if not embs:
            return np.empty((0, 384), dtype=np.float32)
        return np.array(embs, dtype=np.float32)
