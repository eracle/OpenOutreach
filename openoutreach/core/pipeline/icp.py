# openoutreach/core/pipeline/icp.py
"""ICP generators — the LLM writes the campaign's cold-start priors, in two shapes.

The same two inputs (``product_docs + campaign_target``) are the only prior available
before any lead has been judged, and the engine needs them expressed two ways:

- ``generate_seed`` — the ICP as a **query**: one value per family (a title, a
  seniority, a country, a size band), the single most precise conjunction the model can
  name. That conjunction is the campaign's whole starting **pool**, so the initial
  maximal set is exactly one query — the seed. Breadth is not seeded; it grows from the
  leads that qualify (``mint.py``), which add more values per family and so more
  maximals for the selector to rank.
- ``generate_anchors`` — the ICP as **profiles**: a few invented leads that would be
  ideal fits, written in the shape ``discovery.profile_text_for`` produces. Embedded and
  handed to the GP as synthetic positives (``BayesianQualifier.set_anchors``), they are
  what lets the model fit at all on a campaign whose every real verdict so far is a
  rejection — a single-class label set never produces a posterior, so without them BALD,
  P(f>0.5), and every piece of steering that reads them stay unavailable for the whole
  cold phase. They are permanent: once written they stand alongside whatever real
  positives arrive, for the campaign's whole life.

Profiles rather than the product text itself because the space they have to land in is
one of *lead* embeddings: marketing prose about the product embeds nowhere near a row of
firmographics, so it would anchor the model in a region no candidate occupies. They are
also embedded **without** query terms (unlike a discovered lead, whose retrieving query
rides its embedding) — an anchor is a claim about what a good lead looks like, not about
which query to run, and folding the seed's keywords in would have discovery score the
seed highly on the strength of our own guess.

One value per family, never headcount as a range to search: the size band is a single
ICP attribute that rides every maximal unchanged. See ``discovery.filters_for``.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

import jinja2
import numpy as np
from pydantic import BaseModel, Field
from termcolor import colored

from openoutreach.core.conf import PROMPTS_DIR
from openoutreach.discovery import LEAD_SENIORITIES, Seniority

logger = logging.getLogger(__name__)

# How many synthetic ideal profiles anchor a cold campaign. Several rather than one so
# the positive region is outlined rather than pinned to a single hallucination, but few
# enough that a handful of real labels outweighs them.
ANCHOR_COUNT = 3


class ICPSpec(BaseModel):
    """The LLM's provider-agnostic ICP output — the walk's opening **vocabulary**.

    Not a query. The seed used to be "one value per family, the single most precise
    conjunction", which is what the clause model needed; the walk now conjoins tokens
    itself against measured feedback, so what it wants from the LLM is *words worth
    trying*, and as many as the ICP genuinely implies.

    **``domain_keywords`` is the field that makes an ICP an ICP.** The old spec had
    nowhere to put "what the target company actually does" — no field for it — so a
    health-and-wellness campaign seeded on ``content``/``lead``/``united``/``states``
    and every query it could compose selected for *role* while being blind to
    *industry*. The obvious home would be ``lead_industry``, and that field is inert:
    a nonsense value returns the identical count to no filter at all (§8 of the roadmap
    card). But domain words are demonstrably alive in ``lead_job_title``, which matches
    title *and* headline text — ``saas`` counts 3,306, ``startup`` 6,223, ``llm`` 1,214,
    ``agentic`` 1,213, ``stealth`` 932 (§10). So they go there, alongside the role words,
    and the frontier conjoins the two.

    ``seniority`` is typed to Lead Finder's vocabulary, not ``str``: an unknown level
    returns an empty page rather than an error, wasting a fetch. Everything else is free
    text — a token the index doesn't carry is a normal empty page, one fetch spent, and
    the walk retires it.
    """

    role_keywords: list[str] = Field(
        default_factory=list,
        description="Single lowercase words from the job titles the buyer holds — "
                    "'founder', 'head', 'marketing', 'content'. Words, never phrases.",
    )
    domain_keywords: list[str] = Field(
        default_factory=list,
        description="Single lowercase words naming what the target company does or "
                    "sells — 'wellness', 'supplement', 'nutrition', 'saas'. Words, "
                    "never phrases.",
    )
    seniority: Seniority | None = None
    location: str = ""
    headcount_min: int = 1
    headcount_max: int = 10000
    country_code: str = ""


# Which ``ICPSpec`` attrs feed which search axis. Both keyword lists land in
# ``lead_job_title`` — the only free-text axis, and the one that matches headline text as
# well as titles, which is what makes a domain word like ``wellness`` reachable at all.
# Headcount is absent: numbers riding every query, not search terms.
_SEED_FIELDS = (
    ("lead_job_title", "role_keywords"),
    ("lead_job_title", "domain_keywords"),
    ("lead_seniority", "seniority"),
    ("lead_location", "location"),
)


def _seed_keywords(spec: ICPSpec) -> list[tuple[str, str]]:
    """The ICP as ``(field, token)`` keywords — the vocabulary the walk opens with.

    **A job title is split into words; the closed axes are not.** Lead Finder reads
    ``"Head of Growth"`` as three ANDed words, a query narrow enough to be empty before
    the walk has learned anything, so splitting hands the frontier three separate
    one-token nodes and lets *measurement* decide which pair is worth conjoining — which
    is how ``"founder cto"`` (9,027 rows, near-perfect precision) gets found and ``"head
    of growth"`` never gets fired. Stopwords go with them, so ``of`` never becomes a
    search term, and the model's own phrasing survives being sloppy.

    The same split applied to the other two axes was silently fatal, because they match a
    whole value: ``"United States"`` seeded ``united`` and ``states``, which count 0
    apiece and died at offset 0 as *nobody matches this*; ``c_suite`` came apart at the
    underscore and seeded ``suite``. A place is re-cased instead (``as_place``) and a
    seniority is passed through — it is already one of the twelve values the provider
    publishes, typed as such on ``ICPSpec``.
    """
    from openoutreach.core.pipeline.vocabulary import tokenize
    from openoutreach.discovery import as_place

    keywords = set()
    for field, attr in _SEED_FIELDS:
        value = getattr(spec, attr)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not item:
                continue
            if field == "lead_job_title":
                keywords |= {(field, token) for token in tokenize(str(item))}
            elif field == "lead_location":
                keywords.add((field, as_place(item)))
            else:
                keywords.add((field, str(item)))
    return sorted(keywords)


def generate_seed(campaign) -> list[tuple[str, str]]:
    """LLM-generate the campaign's opening vocabulary and size band.

    The cold start, and the **only** LLM call discovery makes about queries: with no
    qualified leads there are no profiles to count words from, so the ICP text is the one
    available source. Everything after this is counting (``vocabulary.refresh``). Also
    folds ``country_code`` and the headcount band onto the campaign — the band rides every
    query unchanged and is never searched.

    Returns the seed keywords, or ``[]`` when the ICP is empty.
    """
    from pydantic_ai import Agent

    from openoutreach.core.llm import get_llm_model, run_agent_sync
    from openoutreach.core.models import Keyword
    from openoutreach.discovery import describe_node

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    prompt = env.get_template("icp_filters.j2").render(
        product_docs=campaign.product_docs,
        campaign_target=campaign.campaign_target,
        seniorities=LEAD_SENIORITIES,
    )

    agent = Agent(
        get_llm_model(),
        output_type=ICPSpec,
        model_settings={"temperature": 0.3, "timeout": 60},
    )
    spec = run_agent_sync(agent.run(prompt)).output

    keywords = _seed_keywords(spec)
    if not keywords:
        return []

    Keyword.rows_for(keywords)

    updates = []
    country_code = spec.country_code.lower()
    if country_code and campaign.country_code != country_code:
        campaign.country_code = country_code
        updates.append("country_code")
    if (campaign.headcount_min, campaign.headcount_max) != (spec.headcount_min, spec.headcount_max):
        campaign.headcount_min = spec.headcount_min
        campaign.headcount_max = spec.headcount_max
        updates += ["headcount_min", "headcount_max"]
    if updates:
        campaign.save(update_fields=updates)

    # The seed is a *query*, not a description of a buyer — it says `founder cto` where
    # the operator asked for "engineering leaders at small SaaS firms". The operator's
    # echo is `log_icp_echo` below; this one stays for the maintainer reading the walk.
    logger.debug("[%s] %s: %s · headcount %d–%d", campaign,
                 colored("discovery seed", "cyan", attrs=["bold"]),
                 colored(describe_node(keywords), "cyan"),
                 spec.headcount_min, spec.headcount_max)
    return keywords


# ── anchors: the ICP as synthetic profiles ───────────────────────────


class Anchor(NamedTuple):
    """One invented ideal lead: the line the GP embeds, and the row the walk counts.

    Two shapes of the same claim, because the two consumers read different things. The
    GP wants ``profile`` — one flat line in ``profile_text_for``'s shape, embedded whole.
    The vocabulary wants ``source_fields`` — the same person as a *lead row*, each value
    already under the field it is searchable in, exactly as ``discovery.source_fields_for``
    stores one for a real lead.
    """

    profile: str
    source_fields: dict


class _AnchorProfile(BaseModel):
    """One invented lead — written once as a line, and again as its queryable fields.

    The fields are asked for rather than parsed out. Splitting the flat line by guess is
    what made anchors unusable as vocabulary: a bag of words cannot say whether
    ``united states`` is a job title or a place, and filing it wrong poisons the axis for
    the campaign's life. The model already knows which is which — it just was never asked.
    """

    profile: str = Field(
        description="Lowercase one-line lead profile: headline, industry, job title, "
                    "company name, seniority, company industry, state, country — space "
                    "separated, no labels.",
    )
    job_title: str = Field(
        default="",
        description="This lead's job title alone, lowercase, no company and no location "
                    "— e.g. 'head of revenue'.",
    )
    location_state: str = Field(
        default="",
        description="State, province or region alone, or empty if the country has none "
                    "worth naming — e.g. 'california'.",
    )
    location_country: str = Field(
        default="",
        description="Country alone — e.g. 'united states'.",
    )


class _AnchorProfiles(BaseModel):
    """The LLM's invented ideal leads, each one line in ``profile_text_for``'s shape."""

    profiles: list[_AnchorProfile] = Field(default_factory=list)


def generate_anchors(campaign, count: int = ANCHOR_COUNT, existing=()) -> list[Anchor]:
    """LLM-invent ``count`` ideal-lead profiles. ``[]`` on an outage or empty ICP.

    ``existing`` are the profiles already written for this campaign — shown to the model
    so a top-up round widens the positive region instead of restating it.

    Best-effort by design: an unanchored campaign still runs, it just spends its cold
    phase without a fitted GP, so failure must not propagate to the caller.
    """
    from pydantic_ai import Agent

    from openoutreach.core.llm import get_llm_model, run_agent_sync

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    prompt = env.get_template("anchor_profiles.j2").render(
        product_docs=campaign.product_docs,
        campaign_target=campaign.campaign_target,
        count=count,
        existing=list(existing),
    )

    try:
        agent = Agent(
            get_llm_model(),
            output_type=_AnchorProfiles,
            # Warmer than the seed: the seed wants the single most likely conjunction,
            # these want spread across the ideal region.
            model_settings={"temperature": 0.8, "timeout": 60},
        )
        result = run_agent_sync(agent.run(prompt)).output
    except Exception:
        logger.exception("[%s] anchor generation failed — campaign stays unanchored", campaign)
        return []

    return [anchor for anchor in map(_as_anchor, result.profiles) if anchor.profile]


def _as_anchor(written: _AnchorProfile) -> Anchor:
    """One LLM-written profile as the pair the two consumers need.

    ``source_fields_for`` is reused rather than reimplemented so an anchor row and a
    discovered lead row are built by the same function: it keeps only the keys
    ``KEYWORD_SOURCE_FIELDS`` reads and drops the empty ones, which is what makes an
    anchor with no state contribute its country alone instead of a blank place.
    """
    from openoutreach.discovery import source_fields_for

    return Anchor(
        profile=written.profile.strip().lower(),
        source_fields=source_fields_for({
            "contact_job_title": written.job_title.strip().lower(),
            "contact_location_state": written.location_state.strip().lower(),
            "contact_location_country": written.location_country.strip().lower(),
        }),
    )


def stored_anchors(campaign) -> np.ndarray | None:
    """The campaign's persisted anchor embeddings as ``(N, dim)``, or ``None``."""
    if not (campaign.anchor_embeddings and campaign.anchor_profiles):
        return None
    stored = np.frombuffer(bytes(campaign.anchor_embeddings), dtype=np.float32)
    return stored.reshape(len(campaign.anchor_profiles), -1).copy()


def ensure_anchors(campaign) -> np.ndarray | None:
    """The campaign's anchor embeddings as ``(N, dim)``, filled up to ``ANCHOR_COUNT``.

    Generates on first use and fills the remainder on a later call if an earlier one came
    back short. Already-written profiles are shown to the model so the second round widens
    the ideal region rather than restating it, and the set is persisted — the daemon must
    not re-invent anchors (and re-anchor the GP somewhere slightly different) on every
    restart.

    ``None`` when the campaign has no ICP text to work from, or the LLM call failed and
    nothing is stored — callers treat that as "no anchors", never as an error. A failed
    fill-up keeps whatever is already there.

    Never called once a real lead has qualified: from that point the set is permanent,
    and the daemon restores it with ``stored_anchors`` instead of inventing more.
    """
    from openoutreach.discovery import embed_profile

    profiles = list(campaign.anchor_profiles or [])
    stored = stored_anchors(campaign)
    if len(profiles) >= ANCHOR_COUNT:
        return stored

    if not (campaign.product_docs or campaign.campaign_target):
        return stored

    fresh = [
        anchor for anchor in generate_anchors(campaign, count=ANCHOR_COUNT - len(profiles),
                                              existing=profiles)
        if anchor.profile not in profiles
    ]
    if not fresh:
        return stored

    embeddings = np.array([embed_profile(a.profile) for a in fresh], dtype=np.float32)
    if stored is not None:
        embeddings = np.vstack([stored, embeddings])

    campaign.anchor_profiles = profiles + [a.profile for a in fresh]
    # Kept parallel to the profiles rather than derived from them: the fields are the
    # model's own assignment, and nothing downstream can recover them from the flat line.
    campaign.anchor_source_fields = (
        list(campaign.anchor_source_fields or []) + [a.source_fields for a in fresh]
    )
    campaign.anchor_embeddings = embeddings.tobytes()
    campaign.save(update_fields=["anchor_profiles", "anchor_source_fields",
                                 "anchor_embeddings"])
    logger.debug("[%s] %s: +%d synthetic ideal profile(s) (%d total)", campaign,
                 colored("anchors", "cyan", attrs=["bold"]), len(fresh), len(embeddings))
    log_icp_echo(campaign)
    return embeddings


def log_icp_echo(campaign) -> None:
    """Tell the operator who the system thinks this campaign sells to. No-op unanchored.

    **This is the earliest proof the product description was understood**, and therefore
    the earliest chance to correct it — the loop the README sells. The material costs
    nothing to print: the anchors are already computed, already one line each in
    ``profile_text``'s shape, and until now only their *count* was ever shown.

    Printed on the pass that writes them and again at the start of every later run, so
    the operator meets it before the first search rather than only on a cold start.
    """
    profiles = list(campaign.anchor_profiles or [])
    if not profiles:
        return

    logger.info("%s", colored("Looking for people like:", "cyan", attrs=["bold"]))
    for profile in profiles:
        logger.info("    · %s", profile)
