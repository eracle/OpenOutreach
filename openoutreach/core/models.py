# openoutreach/core/models.py
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from openoutreach.discovery import FILTER_FAMILIES, describe_filters, filters_for


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
    # node. Set by ``frontier.generate_seed`` from the LLM's ICP spec on the first
    # discovery move. (Discovery is a best-first walk over ``DiscoveryQuery`` nodes
    # — the retired ``icp_filters``/``discovery_offset`` single-cursor state was
    # dropped in migration 0007; the seed is regenerated on cold start and then
    # embodied by the fetched seed nodes, not cached.)
    country_code = models.CharField(max_length=2, blank=True, default="")

    # The clause pool — every candidate value the ICP produced, not just the ones
    # the seed used. The descent composes conjunctions from these without asking
    # the LLM again (``core/pipeline/descend.py``), which is the whole reason the
    # pool exists: ``icp.generate_seed`` gets 5 job titles and the seed can only
    # carry one, and the other 4 used to be dropped on the floor and re-invented
    # at every wall.
    #
    # **The pool is per-campaign; the ``Clause`` rows are global.** Not a
    # contradiction: a clause is the same fact whoever searches it
    # (``lead_location = United States``), but *which* clauses are worth trying is
    # this campaign's ICP talking. So the membership is the campaign's and the row
    # is shared.
    clauses = models.ManyToManyField("Clause", blank=True, related_name="campaigns")

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


class Clause(models.Model):
    """One ``(family, value)`` pair — the unit a discovery query is built from.

    First-class rather than a key in a JSON blob: a clause set has to be *grouped
    over*, which is the walk's only fast query, and you cannot group over a key
    inside a blob. Clauses are also the vocabulary the LLM supplies at cold start,
    which the descent composes conjunctions from without asking the LLM again.

    **Globally unique on ``(family, value)``, with no campaign of its own.** A clause
    is not campaign-specific — ``lead_location = United States`` is the same clause
    whoever searches it — and giving it a campaign would duplicate a fact
    ``DiscoveryQuery`` already owns, letting a node point at a clause belonging to
    another campaign. A campaign reaches its clauses through its queries.

    ``family`` is constrained to ``discovery.FILTER_FAMILIES`` — the field names are
    the provider contract, and an unknown one is silently *dropped* (you get the
    unfiltered page, with rows, reading as success). ``value`` is deliberately
    **not** constrained: except for ``lead_seniority`` these are free-text search
    terms, and a value the index doesn't carry simply returns an empty page — a
    normal, cheap answer.
    """

    family = models.CharField(
        max_length=32, choices=[(f, f) for f in FILTER_FAMILIES],
    )
    value = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["family", "value"], name="uniq_clause"),
        ]

    def __str__(self):
        return describe_filters(filters_for([(self.family, self.value)]))

    @classmethod
    def rows_for(cls, clauses) -> list["Clause"]:
        """Get-or-create the rows for ``(family, value)`` pairs, in order.

        Idempotent, and the one place clause rows are minted: the pool, a fetched
        node and a blacklisted set all name the same clauses, and a clause is
        global, so whichever of them reaches a ``(family, value)`` first creates the
        row and the rest find it.
        """
        return [
            cls.objects.get_or_create(family=family, value=value)[0]
            for family, value in clauses
        ]


class DiscoveryQuery(models.Model):
    """One **fetched** node in a campaign's discovery walk — a Lead Finder query.

    A node is a **set of clauses plus an offset**: at most one clause per family, all
    ANDed, at a pagination depth that has already been pulled once. Discovery is a
    lazy best-first walk over these nodes (see ``core/pipeline/frontier.py``).

    **Why a clause set and not a filter dict.** A filter dict can express an
    include-list — an OR — and an OR is strictly dominated: it compresses several
    ~10k-row sampling windows into one. A filter is not a narrowing of a result set;
    it *moves a window* over a corpus ordered by provider preference, so the only way
    to see different people is a different conjunction. One value per family is
    therefore the whole expressive space worth having (~144 nodes for a 5/5/3 clause
    pool, against ~8,200 with full OR), and ``discovery.filters_for`` enforces it.

    **Only fetched nodes are persisted** — the next query is computed lazily from
    them, so there is no pending queue and no ``parent`` provenance to inherit rank
    from. A node is fetched exactly once (dedup on the unique
    ``(campaign, clause_key, offset)`` triple) and never re-fetched.

    ``exhausted`` marks a clause-set line whose deepen returned an empty page (the
    reactive end-of-depth signal). All offsets of that line are flagged together and
    excluded from selection. Emptiness is the **only** thing that retires a line: a
    barren *yield* is a verdict about a view, not about the query.

    **A node's value is not a column.** It is the ``(examined, qualified)`` pair
    that ``frontier.node_stats`` counts over its first-touch leads' deals — measured
    ground truth, computed per move and never stored, so it can never go stale
    against the deals it summarizes. The value **steers discovery only**: it decides
    which query region to walk next and never gates a lead. Every saved lead advances
    through ``qualify → promote_to_ready → find_email`` on its own P, over the global
    pool, regardless of which node discovered it.
    """

    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="discovery_queries",
    )
    clauses = models.ManyToManyField(Clause, related_name="queries")
    # sha256 of the canonicalized clause set — the node-identity key for dedup, so
    # the same conjunction never enters the walk twice. A column because the set
    # lives across an M2M, which no unique constraint can span.
    clause_key = models.CharField(max_length=64)
    offset = models.IntegerField(default=0)
    # A clause-set line whose deepen hit an empty page (reactive end-of-depth).
    # Set on every offset of that line at once; excluded from selection.
    exhausted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Discovery Query"
        verbose_name_plural = "Discovery Queries"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "clause_key", "offset"],
                name="uniq_discovery_node",
            ),
        ]
        indexes = [
            models.Index(fields=["campaign", "exhausted"], name="discovery_camp_exhausted_idx"),
        ]

    @property
    def clause_pairs(self) -> list[tuple[str, str]]:
        """This node's clauses as sorted ``(family, value)`` pairs.

        The same clauses as the ``clauses`` M2M, in the form a *proposed* query
        carries — the walk picks a query before any row exists for it.
        """
        return sorted(self.clauses.values_list("family", "value"))

    def to_filters(self) -> dict:
        """This node as a Lead Finder filter dict — the only thing the provider sees."""
        return filters_for(self.clause_pairs)

    def __str__(self):
        """The query itself, not its row id.

        A node *is* its clause set — "node 10" says nothing about what was searched,
        and these render in logs and admin where the whole question is which region
        the walk picked.
        """
        flag = " (exhausted)" if self.exhausted else ""
        return f"{describe_filters(self.to_filters())} @{self.offset}{flag}"


class EmptyClauseSet(models.Model):
    """A conjunction Lead Finder matches nobody with, of any size down to one clause.

    Written by ``discover`` when a fetch at offset 0 comes back empty, and read as a
    pruning rule: **a candidate is dead iff some recorded set is a subset of it.**
    That is anti-monotone — a superset of an empty conjunction is empty — so one dry
    fetch retires a sublattice without another call.

    **``k=1`` is the whole point, and it used to live elsewhere.** A singleton set is
    exactly "this clause matches nobody alone", which was once a
    ``Clause.is_live`` tri-state written by a dedicated ``limit=1`` probe sweep. It
    was the same fact stored twice: excluding a dead clause from the pool and pruning
    every candidate that contains it prune identically, since ``{c} ⊆ candidate`` iff
    ``c ∈ candidate``. So the column is gone and the subset test does both jobs.
    The subset test only bites when the recorded sets are *shorter* than the
    candidates, so it prunes most within a *pass* when the short sets are recorded
    first. ``descend`` walks deepest-first (chosen 2026-07-17), which records the
    singletons last, so the pruning it earns is mostly *cross-refill*: once
    ``lead_location: Europe`` is on record as a singleton empty, it prunes every
    freshly-composed superset before that superset is ever fetched.

    **The unit is the whole set, and never a clause inside it.** An empty conjunction
    convicts nothing smaller than itself: ``lead_department: Sales`` is honored and
    returns rows on its own, yet sat in six 0-row conjunctions. Blaming its clauses
    would delete ``Sales`` from every campaign's pool on evidence that says nothing
    about it — the "department is poison" error, automated. A clause is retired here
    only by its *own* singleton coming back empty, which is sound: alone, a clause has
    nothing else to blame.

    **Only emptiness lands here — never a barren yield.** A conjunction whose leads
    all get rejected is a verdict about *the people in that window*, not about whether
    the query matches anybody, and yield propagates in neither direction: a node whose
    window is all-Meta can have a refinement whose window is gold. Nor does an empty
    page at ``offset > 0`` belong here — that is a vein running out, not a query that
    matches nobody. See the roadmap card ``p2-e3-discovery-query-graph-search``.

    **Global, with no campaign FK** — the same argument as ``Clause``. Emptiness
    is a fact about the provider's index, not about a campaign: a conjunction that
    matches nobody matches nobody whoever asks. So one campaign's dry fetches prune
    every campaign's lattice, free.
    """

    clauses = models.ManyToManyField(Clause, related_name="empty_sets")
    # sha256 of the canonicalized clause set — the identity key, for the same
    # reason ``DiscoveryQuery`` carries one: no unique constraint can span an M2M.
    clause_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Empty Clause Set"
        verbose_name_plural = "Empty Clause Sets"

    def __str__(self):
        return f"{describe_filters(filters_for(self.clause_pairs))} → nothing"

    @property
    def clause_pairs(self) -> list[tuple[str, str]]:
        """This set's clauses as sorted ``(family, value)`` pairs."""
        return sorted(self.clauses.values_list("family", "value"))
