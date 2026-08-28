# openoutreach/core/pipeline/vocabulary.py
"""The keyword vocabulary — the tokens a discovery query can be built from.

Growth is **counting, not generation**. The old engine asked an LLM to invent clause
values ("propose new job titles adjacent to these leads"), and the LLM wrote prose:
``Head of Content Strategy``, ``Chief Science Officer``, ``Director of Education``.
Every word in a Lead Finder value is another AND, so a four-token title is near-empty
before it is conjoined with anything — a large, independent cause of the 60-consecutive-
empty-query run that started this rewrite. Here the vocabulary is simply *the words that
appear in profiles the LLM already accepted*, one word per keyword, and narrowing happens
by conjoining tokens at query time against measured feedback.

**Admission is document frequency, and it is nearly free.** Over the label store, a df≥2
floor drops 65% of the vocabulary (3,485 → 1,208 tokens) and loses **zero** good tokens —
none with support ≥ 8 and true precision > 0.8 disappears until df≥10. What it removes is
the singleton tail, which is mostly company names and typos (``cloneable.ai``,
``buildteam.ai.``, ``vendbot``) — and that tail is not harmless: it is 56% of the *top* of
any embedding-based ranking, so it would be fired first. See §13a/§13b of the roadmap card
``p1-e3-leadfinder-index-semantics-and-query-model-rethink``.

**A token's field is measured, not chosen.** ``cto`` is alive in ``lead_job_title`` and
dead everywhere else; ``belgium`` is the reverse. Reading each axis from the lead-row
fields that *are* that axis (``discovery.KEYWORD_SOURCE_FIELDS``) keeps the per-field
vocabularies nearly disjoint at no cost. ``lead_seniority`` is the exception and the
easy case: the provider publishes a closed 12-value list, so it is seeded whole and
never grown.

The upgrade path, if the singleton tail ever needs a sharper cut than df≥2, is Dunning's
log-likelihood ratio — it kills df=1 junk *and* the high-frequency-uninformative tokens a
df floor waves through (``services`` reads 25/34 in the store and would score negative).
Not needed yet.
"""
from __future__ import annotations

import logging
import re

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

logger = logging.getLogger(__name__)

# A token is a word: a letter, then letters/digits and the punctuation real job titles
# carry (``ai-native``, ``ai/ml``, ``co-founder``, ``r&d``). Single characters are
# dropped — they are never a search term and they match everything.
_TOKEN = re.compile(r"[a-z][a-z0-9&/+.\-]{1,}")

# Document-frequency floor over the qualified profiles. Two, not one: the singleton tail
# is 65% of the vocabulary and costs nothing to drop (§13a).
MIN_DOCUMENT_FREQUENCY = 2


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens of a text, stopwords stripped.

    ``sklearn``'s stopword list rather than an NLP dependency: sklearn is already
    required for the qualifier, while ``nltk`` and ``spacy`` are not installed and
    would each pull a model download into a first run for no measured gain.
    """
    return {t for t in _TOKEN.findall(text.lower()) if t not in ENGLISH_STOP_WORDS}


def keywords_for(fields: dict) -> set[tuple[str, str]]:
    """One lead row's stored per-field text → the ``(field, token)`` keywords it grows.

    **Two axes, two units of vocabulary, and the difference is the provider's, not a
    preference.** A job title is free text whose words are matched individually, so it
    contributes *words* and the walk conjoins them. A location is matched as one whole
    place, so it contributes exactly one value.

    Splitting a location on spaces is what this replaces, and it was silently fatal:
    ``United states`` became ``united`` and ``states``, ``New york`` became ``new`` and
    ``york``, and every fragment counts **0** at the provider. The walk spent a
    round-trip apiece finding that out, then wrote each one off at offset 0 as *nobody
    matches this* — pruning its whole subtree over what was really a fact about our
    syntax. A live campaign put 19 such fragments in its vocabulary and killed Israel and
    Spain outright.

    A US state is only a place *with* its country (``California`` counts 0,
    ``California, United States`` counts 2,714,366), so a row carrying a state yields the
    pair and a row with only a country yields the country.
    """
    from openoutreach.discovery import KEYWORD_SOURCE_FIELDS, as_place

    (title_key,) = KEYWORD_SOURCE_FIELDS["lead_job_title"]
    state_key, country_key = KEYWORD_SOURCE_FIELDS["lead_location"]

    keywords = {("lead_job_title", token)
                for token in tokenize(str(fields.get(title_key) or ""))}
    country, state = as_place(fields.get(country_key)), as_place(fields.get(state_key))
    if country:
        keywords.add(("lead_location", f"{state}, {country}" if state else country))
    return keywords


def keyword_words(token: str) -> frozenset[str]:
    """A keyword's words, as a profile's own tokens would spell them.

    A keyword is not always one word any more — ``California, United States`` is a single
    ``lead_location`` value — while a profile is still counted as a bag of words. So a
    node's tokens are matched by *word containment*: every word of every keyword has to
    appear in the profile. For a one-word job title that is the old behaviour exactly.

    Stopwords are stripped for the same reason they are stripped from profiles, and by
    the same tokenizer, so ``District of Columbia, United States`` still matches a profile
    whose ``of`` was dropped. A keyword that is *nothing but* stopwords keeps its raw
    words instead: an empty word set is a subset of every profile, which would make such a
    keyword match everybody rather than nobody.
    """
    words = tokenize(token)
    return frozenset(words or token.lower().split())


def profile_tokens(profile_text: str) -> frozenset[str]:
    """The token set of one stored profile — the unit node counting is done over.

    **Field-agnostic on purpose.** A node's ``a``/``b`` are counted by asking whether a
    labelled profile contains all of the node's tokens *anywhere*, not whether each token
    appears in the field the query will put it in. That is exactly how the estimator was
    measured (§13c), and it is also what lets the whole existing label store count for
    nodes built from fields those older rows never recorded.
    """
    return frozenset(tokenize(profile_text))


# ── growing the vocabulary ───────────────────────────────────────────

def _qualified_source_fields(campaign) -> list[dict]:
    """Per-field raw text of the leads the LLM accepted — the vocabulary's only source.

    Qualified mirrors the GP's own labelling: any deal that is not an LLM rejection
    (``FAILED`` + ``wrong_fit``). Leads discovered before ``Lead.source_fields`` existed
    carry ``{}`` and contribute nothing here — they still count toward every node's
    ``a``/``b`` (that reads ``profile_text``), they just cannot say which *field* one of
    their words belongs in, and guessing would put ``united states`` into job titles.
    """
    from openoutreach.crm.models import Deal, DealState, Lead, Outcome

    lead_ids = (
        Deal.objects.filter(campaign=campaign, lead_id__isnull=False)
        .exclude(state=DealState.FAILED, outcome=Outcome.WRONG_FIT)
        .values_list("lead_id", flat=True)
    )
    return [
        fields for fields in
        Lead.objects.filter(pk__in=lead_ids).values_list("source_fields", flat=True)
        if fields
    ]


def refresh(campaign) -> int:
    """Fold the qualified leads' words into the vocabulary. Returns tokens added.

    Cheap enough to run on every discovery pass — it is a tokenize and a count over a few
    hundred profiles, with no LLM call and no provider call, which is the whole point of
    replacing minting with counting. There is no cadence to tune and no high-water mark to
    store: re-deriving the vocabulary is faster than remembering when we last did.
    """
    from openoutreach.core.models import Keyword

    profiles = _qualified_source_fields(campaign)
    if not profiles:
        # The cold-phase reality, and worth naming: with nothing qualified yet there are
        # no words to count, so the vocabulary is whatever the ICP seed put there and the
        # frontier cannot grow past its depth-1 nodes until the first lead is accepted.
        logger.debug("[%s] vocabulary: no qualified lead carries per-field text yet — "
                     "nothing to grow from (seed vocabulary stands)", campaign)
        return 0

    # Document frequency per (field, token): how many qualified profiles carry it.
    frequency: dict[tuple[str, str], int] = {}
    for fields in profiles:
        for pair in keywords_for(fields):
            frequency[pair] = frequency.get(pair, 0) + 1

    admitted = [pair for pair, df in frequency.items() if df >= MIN_DOCUMENT_FREQUENCY]
    logger.debug("[%s] vocabulary: %d qualified profile(s) → %d distinct token(s) → "
                 "%d admitted at df>=%d", campaign, len(profiles), len(frequency),
                 len(admitted), MIN_DOCUMENT_FREQUENCY)
    if not admitted:
        return 0

    known = set(Keyword.objects.values_list("field", "token"))
    fresh = [pair for pair in admitted if pair not in known]
    if fresh:
        Keyword.rows_for(fresh)
        logger.info("[%s] vocabulary: +%d keyword(s) from %d qualified profile(s)",
                    campaign, len(fresh), len(profiles))
    return len(fresh)


def seed_seniorities() -> int:
    """Ensure the closed ``lead_seniority`` vocabulary exists. Returns rows added.

    The one axis whose vocabulary is published rather than discovered, so it is seeded
    whole instead of grown: a value outside the list is not an error but an empty page,
    and there are only twelve. ``c_suite`` is included despite appearing zero times in
    13,827 stored profiles — the store says nothing about what the *input* side accepts,
    and one empty page settles it far more cheaply than a special case would.
    """
    from openoutreach.core.models import Keyword
    from openoutreach.discovery import LEAD_SENIORITIES

    pairs = [("lead_seniority", s) for s in LEAD_SENIORITIES]
    known = set(Keyword.objects.filter(field="lead_seniority").values_list("field", "token"))
    fresh = [p for p in pairs if p not in known]
    if fresh:
        Keyword.rows_for(fresh)
    return len(fresh)


def admitted_keywords() -> list[tuple[str, str]]:
    """Every ``(field, token)`` a query node may be built from, sorted."""
    from openoutreach.core.models import Keyword

    return sorted(Keyword.objects.values_list("field", "token"))
