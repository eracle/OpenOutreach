# openoutreach/core/models.py
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from openoutreach.discovery import SEARCH_FIELDS, describe_node


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

    # Email-finder keys — one per supported vendor, and a key is all it takes to select
    # one (see enrichment/provider.py:active). Blank everywhere disables enrichment.
    # BetterContact's key additionally powers Lead Finder *discovery*, which is billed
    # nothing; Apollo's does not, so an Apollo-only install still needs the other key
    # for discovery. They are not interchangeable at that leg, only at enrichment.
    bettercontact_api_key = models.CharField(max_length=500, blank=True, default="")
    apollo_api_key = models.CharField(max_length=500, blank=True, default="")

    # Which finder resolves addresses when *both* keys are set. Blank means "decide from
    # whichever key exists", which is the whole answer for a one-vendor install; it only
    # has to be set to move an install that holds both.
    email_finder = models.CharField(
        max_length=32, blank=True, default="",
        help_text="bettercontact | apollo — blank picks whichever key is configured",
    )

    # Central contacts service (see openoutreach/contacts/). The token is earned
    # on the first contribution and persisted here — never in the repo; blank
    # means "not registered yet" (resolve misses until the first give-back mints
    # it). The URL is blank by default (falls back to DEFAULT_CONTACTS_API_URL).
    contacts_api_token = models.CharField(max_length=500, blank=True, default="")
    contacts_api_url = models.CharField(max_length=500, blank=True, default="")

    # The operator's ISO-3166 alpha-2 country, collected at onboarding (self-hosted
    # = one operator, so it lives on the config singleton, not a separate account
    # model — identity like email/name stays on the Django ``User``). Drives the
    # email-jurisdiction rules (core/geo.py): newsletter opt-in default + whether
    # we contribute to the contacts store (derived, ``not is_eea_located`` — never
    # a stored toggle).
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
    # ``booking_link`` was here — a meeting URL the outreach agent offered when a lead
    # asked for a call, and the only thing that ever read it was that agent's prompt.
    # It left with the sender: a finder has nobody to offer a meeting to.
    model_blob = models.BinaryField(null=True, blank=True)
    # ISO-3166 alpha-2 target country for this campaign's leads — the contacts
    # geo-gate stamp put on every discovered Lead. A stable ICP attribute (one
    # country per campaign), so it lives here rather than duplicated on each query
    # node. Set by ``icp.generate_seed`` from the LLM's ICP spec on the cold start.
    country_code = models.CharField(max_length=2, blank=True, default="")

    # The ICP's company-size band. A fixed constraint riding every discovery query
    # unchanged, never a search axis: loosening a size bound queries off-ICP rather
    # than widening usefully, and the provider fills a half-open band with
    # any-size companies rather than returning nothing. Set by ``icp.generate_seed``.
    #
    # A column rather than a pair of keywords because it is a *number*, not a search
    # term — the provider takes it as a bare scalar, and it is the one part of a query
    # the walk never chooses.
    headcount_min = models.IntegerField(default=1)
    headcount_max = models.IntegerField(default=10000)

    # The cold-phase priors: synthetic ideal-lead profiles the LLM invented from
    # product_docs + campaign_target (``pipeline/icp.generate_anchors``), and their
    # embeddings as one (N, dim) float32 blob. They are handed to the GP as positives so
    # it can fit before any real lead has qualified — a label set that is all rejections
    # has one class and yields no posterior at all.
    #
    # **These are never leads.** They exist only as GP observations and as the operator's
    # window (in Admin) onto what the model currently believes a good lead looks like; no
    # Lead or Deal row is ever created from them and nobody is ever emailed. Both fields
    # are cleared the moment a real lead qualifies — the cold phase is over, real ground
    # truth supersedes the guess, and a campaign carrying anchors is exactly a campaign
    # still waiting for its first positive.
    anchor_profiles = models.JSONField(default=list, blank=True)
    anchor_embeddings = models.BinaryField(null=True, blank=True)
    # The same invented people as *lead rows* — one ``source_fields`` dict per profile,
    # parallel to ``anchor_profiles``. This is what lets a campaign with no acceptances
    # open a precise frontier: ``vocabulary.refresh`` counts these alongside real accepted
    # profiles, so the walk starts from words the ICP actually named instead of the
    # generic tokens a three-profile corpus admits. Written by the model, never parsed out
    # of the flat line — see ``pipeline/icp._AnchorProfile``.
    anchor_source_fields = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.name


class Keyword(models.Model):
    """One ``(field, token)`` pair — the unit a discovery query is built from.

    A **single word**, not a phrase. Multi-word values were the old model's silent
    killer: every extra word is another AND (``Manager`` → ``Content Manager`` is a
    ~300× narrowing), so the LLM-written four-token titles the pool used to hold —
    ``Head of Content Strategy``, ``Chief Science Officer`` — were near-empty before
    they were conjoined with anything. Joining tokens is still how the walk narrows,
    but it happens at query time (``discovery.filters_for``), one token per move,
    against measured feedback rather than an LLM's guess at a job title.

    **Globally unique on ``(field, token)``, with no campaign of its own** — a token
    is not campaign-specific, ``lead_location = belgium`` is the same search term
    whoever runs it. A campaign reaches its vocabulary through its query nodes.

    ``field`` is constrained to ``discovery.SEARCH_FIELDS``: the field names are the
    provider contract, and an unknown one is silently *dropped* (you get the
    unfiltered page, with rows, reading as success). ``token`` is deliberately
    **not** constrained — except for ``lead_seniority`` these are free-text search
    terms, and a token the index doesn't carry simply returns an empty page.
    """

    field = models.CharField(
        max_length=32, choices=[(f, f) for f in SEARCH_FIELDS],
    )
    token = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["field", "token"], name="uniq_keyword"),
        ]

    def __str__(self):
        return f"{self.field.removeprefix('lead_')} {self.token}"

    @property
    def pair(self) -> tuple[str, str]:
        return (self.field, self.token)

    @classmethod
    def rows_for(cls, keywords) -> list["Keyword"]:
        """Get-or-create the rows for ``(field, token)`` pairs, in order. Idempotent."""
        return [
            cls.objects.get_or_create(field=field, token=token)[0]
            for field, token in keywords
        ]




class QueryNode(models.Model):
    """One node in a campaign's discovery walk — a keyword set, and where it has been paged to.

    The walk is a **greedy add-only descent over keyword sets**, valued by counting and
    sampled by Thompson. A node is a set of ``(field, token)`` keywords; its children
    are itself plus one more token. There is no remove move — the frontier is global,
    so a shallow node's untried siblings stay reachable without one.

    **This model replaces a whole lattice.** The old walk stored clauses, fetched
    ``DiscoveryQuery`` rows, and an ``EmptyClauseSet`` blacklist, and computed a
    Cartesian product of *maximal* conjunctions on every call — 63,000 candidates for a
    live pool, essentially all of them genuinely empty, because the clause model assumed
    orthogonal facets over what is really a keyword index. See the roadmap card
    ``p1-e3-leadfinder-index-semantics-and-query-model-rethink``.

    **The node carries no value column.** Its estimate is derived from the label store
    every time it is needed — ``a``/``b`` are the qualified/rejected leads whose profile
    text contains all of this node's tokens — so there is no counter to drift, nothing to
    migrate, and nothing to reconcile after a crash. It is also the *same* estimator
    before and after the node is fired, which is what makes a bad page self-correcting:
    a node that looks good from the store and returns nobody useful has its own misses
    land in the counters that made it look good.

    **``parent`` is the level, not provenance.** A child inherits its parent's measured
    rate as the prior its own counts move off (``select.estimate``), because that
    predicts a child's true precision better than the child's raw counts alone (0.661 vs
    0.653, §13c). A node reachable by several paths — and with add-only over three
    fields most are — is created once on its canonical key and keeps the parent giving
    the **highest** estimate.

    **State is a corpus fact, never a model fact.** A node is retired only for
    emptiness: nothing is ever retired for scoring badly, because the qualifier refits
    constantly and a barren yield is a verdict about a view. Which *kind* of emptiness
    depends on the offset it appeared at, because the provider reports ``0`` both for a
    query that matches nobody and for one paged past its end (§7).
    """

    class State(models.TextChoices):
        # Never fetched. Its estimate is its parent's, moved by its own store counts.
        FRONTIER = "frontier", "frontier"
        # Fetched at least once and still has pages left.
        FIRED = "fired", "fired"
        # Paged until a page came back empty. Retired; children pruned when this
        # happened below the 10k reach cap, since every superset's population is
        # then already in our DB.
        DRAINED = "drained", "drained"
        # Returned nothing at offset 0 — the index matches nobody. Retired, and its
        # whole subtree with it: a superset of these tokens matches a subset of people.
        DEAD = "dead", "dead"

    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="query_nodes",
    )
    keywords = models.ManyToManyField(Keyword, related_name="nodes")
    # sha256 of the canonicalized keyword set — the node-identity key, a column
    # because the set lives across an M2M and no unique constraint can span one.
    token_key = models.CharField(max_length=64)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children",
    )
    # Where the next page starts. Advances by the page size on every fetch; the
    # provider stops answering past 10,000 whatever the query counts.
    next_offset = models.IntegerField(default=0)
    state = models.CharField(max_length=16, choices=State.choices, default=State.FRONTIER)
    # The provider's exact corpus count at offset 0, when we have asked for it. Free,
    # in the same call, and read only as a diagnostic — the walk fires nodes rather
    # than counting them first, since a dead node generates no children either way.
    leads_found = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Query Node"
        verbose_name_plural = "Query Nodes"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "token_key"], name="uniq_query_node",
            ),
        ]
        indexes = [
            models.Index(fields=["campaign", "state"], name="query_node_state_idx"),
        ]

    @property
    def pairs(self) -> list[tuple[str, str]]:
        """This node's keywords as sorted ``(field, token)`` pairs."""
        return sorted(self.keywords.values_list("field", "token"))

    def to_filters(self) -> dict:
        """This node as a Lead Finder filter dict — the only thing the provider sees."""
        from openoutreach.discovery import filters_for

        return filters_for(self.pairs, (self.campaign.headcount_min, self.campaign.headcount_max))

    def __str__(self):
        """The query itself, not its row id — a node *is* its keyword set."""
        suffix = "" if self.state == self.State.FRONTIER else f" [{self.state}]"
        offset = f" @{self.next_offset}" if self.next_offset else ""
        return f"{describe_node(self.pairs)}{offset}{suffix}"
