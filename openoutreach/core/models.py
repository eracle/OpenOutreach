# openoutreach/core/models.py
from __future__ import annotations

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
    # Discovery — the Lead Finder ICP spec {"filters": {...}, "country_code": "xx"},
    # generated once by the LLM from product_docs + target and reused across
    # cycles so pagination stays coherent (empty = not yet generated). The offset
    # is the page cursor into the Lead Finder result set — the state that lets
    # discovery advance past page 1 across cycles and daemon restarts. Both are the
    # simple static-discovery MVP; adaptive ICP refinement is a future redesign
    # (roadmap: off-linkedin-leadfinder-pivot).
    icp_filters = models.JSONField(default=dict, blank=True)
    discovery_offset = models.IntegerField(default=0)

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


class ActionLog(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        DUPLICATE = "duplicate"

    action_type = models.CharField(max_length=80)
    target_type = models.CharField(max_length=80, blank=True, default="")
    target_id = models.CharField(max_length=120, blank=True, default="")
    payload_hash = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=160, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
    )
    result = models.JSONField(null=True, blank=True, default=None)
    error_type = models.CharField(max_length=80, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["action_type", "status"],
                name="core_action_type_status_idx",
            ),
            models.Index(
                fields=["created_at"],
                name="core_action_created_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["action_type", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_action_idempotency_key",
            ),
        ]

    def mark_succeeded(self, result: dict) -> None:
        self.status = self.Status.SUCCEEDED
        self.result = result
        self.error_type = ""
        self.error_message = ""
        self.save(
            update_fields=["status", "result", "error_type", "error_message", "updated_at"],
        )

    def mark_failed(self, error_type: str, error_message: str) -> None:
        self.status = self.Status.FAILED
        self.error_type = error_type
        self.error_message = error_message
        self.save(
            update_fields=["status", "error_type", "error_message", "updated_at"],
        )
