# tests/test_discovery.py
"""Discovery slice — mock the BetterContact transport (`submit_and_poll`) and
the embedder, so no network or ONNX model is needed."""
from unittest.mock import patch

import numpy as np
import pytest
from pydantic import ValidationError

from openoutreach import discovery
from openoutreach.core.pipeline.icp import ICPSpec


def _set_key(value):
    from openoutreach.core.models import SiteConfig

    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = value
    cfg.save()


class TestSeniorityVocabulary:
    """`lead_seniority` is the one filter family with a closed vocabulary, and a
    value outside it returns an empty page instead of an error — so the ICP seed
    constrains it in the schema. A bad seed is the worst case: it starves the
    bootstrap phase, which pages the seed until the GP can score."""

    def test_seed_spec_rejects_a_level_lead_finder_does_not_know(self):
        with pytest.raises(ValidationError):
            ICPSpec(seniority="other")

    def test_seed_spec_accepts_every_real_level(self):
        for level in discovery.LEAD_SENIORITIES:
            assert ICPSpec(seniority=level).seniority == level

    def test_vocabulary_is_derived_from_the_type_so_prompt_and_schema_cannot_drift(self):
        # The prompts render this tuple; the schema validates the same Literal.
        assert "mid-level" in discovery.LEAD_SENIORITIES
        assert "other" not in discovery.LEAD_SENIORITIES
        assert len(discovery.LEAD_SENIORITIES) == 12


class TestSearch:
    def test_returns_leads_and_sends_icp_filters(self, db):
        _set_key("k")
        rows = [{"contact_full_name": "Alice"}]
        with patch.object(discovery, "submit_and_poll", return_value={"leads": rows}) as call:
            result = discovery.search({"lead_seniority": {"include": ["owner"]}}, limit=10)

        assert result.leads == rows
        api_key, url, body = call.call_args.args
        assert api_key == "k"
        assert url == discovery.LEAD_FINDER_URL
        assert body == {"filters": {"lead_seniority": {"include": ["owner"]}}, "limit": 10, "offset": 0}

    def test_no_leads_key_is_empty_list(self, db):
        _set_key("k")
        with patch.object(discovery, "submit_and_poll", return_value={"status": "terminated"}):
            assert discovery.search({}).leads == []

    def test_surfaces_leads_found_at_offset_zero(self, db):
        # The count separates a genuinely empty query from a transport artifact: a
        # burst can answer a 71M-lead query with an empty page (card §4).
        _set_key("k")
        body = {"leads": [], "summary": {"leads_found": 71403396}}
        with patch.object(discovery, "submit_and_poll", return_value=body):
            page = discovery.search({}, offset=0)
        assert page.leads == [] and page.leads_found == 71403396

    def test_count_is_none_past_offset_zero(self, db):
        # leads_found is only trustworthy at offset 0 — past the end of ANY result set
        # the API reports 0 (card §7), so it must never read as "empty" there.
        _set_key("k")
        body = {"leads": [], "summary": {"leads_found": 0}}
        with patch.object(discovery, "submit_and_poll", return_value=body):
            page = discovery.search({}, offset=500)
        assert page.leads_found is None


class TestSourceFieldsFor:
    def test_the_headline_is_not_kept_as_job_title_vocabulary(self):
        """A headline is marketing prose, and its words are not job titles. It still
        reaches ``profile_text`` for the qualifier — only the vocabulary refuses it."""
        row = {
            "contact_job_title": "CTO",
            "contact_headline": "Building AI-powered agents for users",
            "contact_location_country": "United States",
        }
        stored = discovery.source_fields_for(row)

        assert stored == {
            "contact_job_title": "CTO",
            "contact_location_country": "United States",
        }
        assert "users" in discovery.profile_text_for(row)


class TestKeywordsFor:
    """A row grows two kinds of keyword, because the index matches two ways: a job title
    by its words, a location as one whole place."""

    def test_a_place_is_one_keyword_re_cased_for_the_index(self):
        from openoutreach.core.pipeline.vocabulary import keywords_for

        keywords = keywords_for({"contact_job_title": "Head of Growth",
                                 "contact_location_country": "United states"})

        assert ("lead_location", "United States") in keywords
        # The fragments the old word-split produced, every one of which counts 0.
        assert ("lead_location", "united") not in keywords
        assert ("lead_location", "states") not in keywords
        # A title still contributes words, which is the axis that conjoins them.
        assert ("lead_job_title", "head") in keywords
        assert ("lead_job_title", "growth") in keywords

    def test_a_state_carries_its_country(self):
        """`California` counts 0 on its own; `California, United States` counts 2.7M."""
        from openoutreach.core.pipeline.vocabulary import keywords_for

        keywords = keywords_for({"contact_location_state": "California",
                                 "contact_location_country": "United states"})

        assert ("lead_location", "California, United States") in keywords

    def test_a_connective_stays_lowercase(self):
        """Measured: `Bosnia and Herzegovina` counts 36,067, `Bosnia And Herzegovina` 0."""
        from openoutreach.core.pipeline.vocabulary import keywords_for

        keywords = keywords_for({"contact_location_country": "Bosnia and herzegovina"})

        assert ("lead_location", "Bosnia and Herzegovina") in keywords

    def test_a_row_with_no_place_grows_no_location_keyword(self):
        from openoutreach.core.pipeline.vocabulary import keywords_for

        keywords = keywords_for({"contact_job_title": "CTO"})

        assert keywords == {("lead_job_title", "cto")}


class TestFiltersFor:
    """The one place a node becomes provider JSON, and the two shapes it can take."""

    def test_job_title_words_are_joined_because_they_and_inside_the_field(self):
        filters = discovery.filters_for([("lead_job_title", "founder"),
                                         ("lead_job_title", "cto")])

        # Sorted, so one node is one query however it was reached.
        assert filters == {"lead_job_title": {"include": ["cto founder"],
                                              "exact_match": False}}

    def test_a_closed_axis_is_sent_as_its_own_value(self):
        """Never space-joined: `["director founder"]` counts 0 where `["director"]`
        counts 5.0M, and a place is matched whole."""
        filters = discovery.filters_for([("lead_seniority", "director"),
                                         ("lead_location", "California, United States")])

        assert filters == {
            "lead_seniority": {"include": ["director"]},
            "lead_location": {"include": ["California, United States"]},
        }

    def test_the_headcount_band_rides_along_unchanged(self):
        filters = discovery.filters_for([("lead_seniority", "head")], headcount=(2, 500))

        assert filters["company_headcount_min"] == 2
        assert filters["company_headcount_max"] == 500


class TestProfileTextFor:
    def test_joins_fields_in_order_lowercased(self):
        row = {
            "contact_headline": "Head of Growth", "contact_industry": "SaaS",
            "contact_job_title": "CMO", "company_name": "Acme",
            "contact_seniority": "Founder", "company_industry": "B2B",
            "contact_location_state": "California",
            "contact_location_country": "United States",
        }
        assert discovery.profile_text_for(row) == (
            "head of growth saas cmo acme founder b2b california united states"
        )

    def test_tolerates_missing_and_null_fields(self):
        assert discovery.profile_text_for({"contact_headline": "Hi", "company_name": None}) == "hi"

    def test_skips_absent_fields_without_padding(self):
        # a sparse row stays short rather than padding out to a rich row's shape
        assert discovery.profile_text_for({"contact_job_title": "CEO"}) == "ceo"

    def test_drops_the_fabricated_company_free_text(self):
        # Lead Finder staples a fuzzy-matched company record onto every row (1-4
        # distinct per 100-row page), so these carry no per-lead signal to rank on.
        row = {
            "contact_job_title": "Founder",
            "company_description": "Meta's mission is to build the future of human connection",
            "company_keywords": ["bee keeper", "chaplin", "dive master"],
            "contact_location": "Berlin",
        }
        assert discovery.profile_text_for(row) == "founder"


class TestPersonFor:
    """The row's identity, kept as columns for the export rather than as text.

    Discovery knows one ``contact_full_name`` and nothing finer — first/last arrive
    later from the enrichment provider's own response, never from a split here.
    """

    def test_reads_the_name_and_title_the_provider_reports(self):
        row = {"contact_full_name": "Ada Lovelace", "contact_job_title": "CTO"}
        assert discovery.person_for(row) == {"full_name": "Ada Lovelace", "job_title": "CTO"}

    def test_an_unreported_field_is_none_not_an_empty_string(self):
        # One representation of "they didn't tell us", so the column never holds both.
        assert discovery.person_for({}) == {"full_name": None, "job_title": None}

    def test_null_and_empty_and_padded_values_all_normalise(self):
        row = {"contact_full_name": "  Ada Lovelace  ", "contact_job_title": ""}
        assert discovery.person_for(row) == {"full_name": "Ada Lovelace", "job_title": None}

    def test_no_first_or_last_name_is_invented(self):
        # The whole point: a split here would end up in a sequencer's {{first_name}}.
        assert set(discovery.person_for({"contact_full_name": "Ada Lovelace"})) == {
            "full_name", "job_title"}


@pytest.mark.django_db
class TestCompanyFor:
    def test_creates_the_company_and_keys_it_on_the_domain(self):
        company = discovery.company_for({"company_name": "Acme", "company_domain": "acme.com"})

        assert (company.name, company.domain, company.key) == ("Acme", "acme.com", "acme.com")

    def test_two_leads_at_one_firm_share_a_row(self):
        first = discovery.company_for({"company_name": "Acme", "company_domain": "Acme.com"})
        second = discovery.company_for({"company_name": "Acme", "company_domain": "acme.com"})

        assert first.pk == second.pk  # the key lowercases, so casing cannot fork the row

    def test_a_company_with_no_domain_keys_on_its_name(self):
        company = discovery.company_for({"company_name": "Acme"})

        assert company.key == "name:acme"

    def test_a_row_naming_no_company_stores_nothing(self):
        assert discovery.company_for({"contact_job_title": "CTO"}) is None
        assert discovery.company_for({"company_name": "", "company_domain": None}) is None


class TestEmbedProfile:
    def test_appends_query_terms_to_profile_text(self):
        # The keyword injection: a lead is embedded as its firmographic text PLUS its
        # retrieving query's terms, so the GP learns which query keywords surface good
        # leads. Only the embedding carries the terms — profile_text (the LLM's input)
        # does not, or the LLM would rubber-stamp a lead for matching its own query.
        with patch("openoutreach.core.ml.embeddings.embed_text", return_value=np.ones(384)) as embed:
            discovery.embed_profile("head of growth acme", "title cmo · seniority owner")
        embed.assert_called_once_with("head of growth acme title cmo · seniority owner")

    def test_profile_only_when_no_query_terms(self):
        with patch("openoutreach.core.ml.embeddings.embed_text", return_value=np.ones(384)) as embed:
            discovery.embed_profile("hi")
        embed.assert_called_once_with("hi")


class TestKeywordTerms:
    def test_renders_the_tokens_as_plain_words(self):
        # Folded into a discovered lead's embedding only, never into profile_text.
        keywords = [("lead_job_title", "CMO"), ("lead_seniority", "owner")]
        assert discovery.keyword_terms(keywords) == "cmo owner"


class TestDescribeFilters:
    """The log rendering of a Lead Finder filter set. Pure (no colour) so the
    call sites own presentation and these stay readable."""

    def test_renders_the_families_a_mutation_varies(self):
        assert discovery.describe_filters({
            "company_headcount_min": 1, "company_headcount_max": 20,
            "lead_job_title": {"include": ["Founder", "CTO"], "exact_match": False},
            "lead_location": {"include": ["United States"]},
        }) == "headcount 1–20 · job_title Founder, CTO · location United States"

    def test_collapses_the_two_headcount_bounds_into_one_range(self):
        """min/max are two keys describing one thing; they read as one."""
        out = discovery.describe_filters({"company_headcount_min": 1, "company_headcount_max": 20})
        assert out == "headcount 1–20"

    def test_marks_an_open_ended_headcount_bound(self):
        assert discovery.describe_filters({"company_headcount_min": 50}) == "headcount 50–?"
        assert discovery.describe_filters({"company_headcount_max": 50}) == "headcount ?–50"

    def test_keeps_exact_match_because_it_changes_what_matches(self):
        assert discovery.describe_filters(
            {"lead_job_title": {"include": ["SDR"], "exact_match": True}}
        ) == "job_title SDR (exact)"

    def test_strips_the_lead_prefix(self):
        """Every family we search is ``lead_*`` (the only ``company_*`` keys are the
        two headcount bounds, consumed by the range renderer) — so only ``lead_`` is
        stripped; an unmodeled key stays verbatim (see the unknown-key test)."""
        assert discovery.describe_filters({
            "lead_department": {"include": ["Sales"]},
            "lead_skills": {"include": ["negotiation"]},
        }) == "department Sales · skills negotiation"

    def test_empty_filters_say_so_rather_than_render_blank(self):
        """An all-unset proposal means 'the LLM is dry' — it must not read as a query."""
        assert discovery.describe_filters({}) == "(no filters)"

    def test_survives_an_empty_include_list(self):
        assert discovery.describe_filters({"lead_skills": {"include": []}}) == "skills (none)"

    def test_renders_an_unknown_key_rather_than_dropping_it(self):
        """Filters are free-form dicts; a key we don't model must still be visible."""
        assert discovery.describe_filters({"some_new_filter": "x"}) == "some_new_filter x"


class TestSeedKeywords:
    """The ICP becomes a *vocabulary*, not one precise query."""

    def test_domain_words_reach_the_searchable_axis(self):
        # The gap this closes: ICPSpec had no field for what the target company *does*,
        # so a health-and-wellness campaign seeded on role words alone and every query
        # it composed was blind to industry. lead_industry is inert (card §8), but
        # domain words are alive in lead_job_title, which matches headline text too.
        from openoutreach.core.pipeline.icp import ICPSpec, _seed_keywords

        spec = ICPSpec(role_keywords=["founder", "head"],
                       domain_keywords=["wellness", "supplement"],
                       seniority="founder", location="United States")
        keywords = _seed_keywords(spec)

        assert ("lead_job_title", "wellness") in keywords
        assert ("lead_job_title", "supplement") in keywords
        assert ("lead_job_title", "founder") in keywords
        assert ("lead_seniority", "founder") in keywords

    def test_phrases_are_split_and_stopwords_dropped(self):
        # Lead Finder ANDs the words inside a value, so "head of growth" would match
        # nobody. Split into separate one-token nodes and let the walk conjoin them.
        from openoutreach.core.pipeline.icp import ICPSpec, _seed_keywords

        keywords = _seed_keywords(ICPSpec(role_keywords=["Head of Growth"]))

        assert ("lead_job_title", "head") in keywords
        assert ("lead_job_title", "growth") in keywords
        assert ("lead_job_title", "of") not in keywords
        assert ("lead_job_title", "head of growth") not in keywords

    def test_an_empty_icp_seeds_nothing(self):
        from openoutreach.core.pipeline.icp import ICPSpec, _seed_keywords

        assert _seed_keywords(ICPSpec()) == []

    def test_the_closed_axes_are_seeded_whole(self):
        """Splitting these is fatal, not merely lossy: `United States` seeded `united`
        and `states`, which count 0 apiece and die at offset 0 as *nobody matches this*,
        and `c_suite` came apart at the underscore into `suite`."""
        from openoutreach.core.pipeline.icp import ICPSpec, _seed_keywords

        keywords = _seed_keywords(ICPSpec(seniority="c_suite", location="United States"))

        assert ("lead_location", "United States") in keywords
        assert ("lead_seniority", "c_suite") in keywords
        assert ("lead_location", "united") not in keywords
        assert ("lead_location", "states") not in keywords

    def test_a_seeded_place_is_re_cased_for_the_index(self):
        """The index reports `united states` and matches `United States`."""
        from openoutreach.core.pipeline.icp import ICPSpec, _seed_keywords

        assert _seed_keywords(ICPSpec(location="united kingdom")) == [
            ("lead_location", "United Kingdom")]
