# tests/test_export.py
"""The lead export — the finder's public output.

Three things are worth pinning down. The **column names are other people's**: Instantly
and Smartlead require ``email``/``first_name``/``last_name`` and recognise ``company``/
``title``/``website``/``linkedin_url``, so a file this writes imports without mapping,
and a rename here silently breaks that. A **Deal is not an endorsement** — both
rejections (`FAILED`, and `Lead.disqualified`) have to be filtered, and missing one
shipped rejected leads to production. And it is a **pure database read**: no GP fit, no
LLM call, nothing mutated.
"""
import csv
import io
import json
from unittest.mock import patch

import pytest

from openoutreach.core import export
from openoutreach.crm.models import Company, DealState
from tests.factories import DealFactory, LeadFactory


def _lead(**kwargs):
    defaults = {
        "full_name": "Ada Lovelace",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "job_title": "CTO",
        "email": "ada@acme.com",
        "company": Company.objects.get_or_create(
            key="acme.com", defaults={"name": "Acme", "domain": "acme.com"})[0],
    }
    return LeadFactory(embedded=True, **{**defaults, **kwargs})


def _deal(campaign, reason="fits the ICP", **lead_kwargs):
    return DealFactory(campaign=campaign, lead=_lead(**lead_kwargs),
                       state=DealState.RESOLVED, reason=reason)


# ── the record ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestLeadRecord:
    def test_maps_our_columns_onto_the_importers_names(self, campaign):
        deal = _deal(campaign)

        record = export.lead_record(deal)

        assert record["email"] == "ada@acme.com"
        assert (record["first_name"], record["last_name"]) == ("Ada", "Lovelace")
        assert record["company"] == "Acme"      # Company.name, not "company_name"
        assert record["title"] == "CTO"         # Lead.job_title, not "job_title"
        assert record["website"] == "acme.com"  # Company.domain, not "domain"
        assert record["linkedin_url"] == deal.lead.profile_url
        assert record["reason"] == "fits the ICP"
        assert record["lead_id"] == deal.lead.pk

    def test_a_lead_with_no_company_exports_nulls_not_blanks(self, campaign):
        record = export.lead_record(_deal(campaign, company=None))

        assert record["company"] is None and record["website"] is None

    def test_an_unenriched_lead_exports_null_name_parts(self, campaign):
        """A hub-cache hit resolves an address and no identity. Nothing is invented."""
        deal = _deal(campaign, first_name=None, last_name=None)

        record = export.lead_record(deal)

        assert record["first_name"] is None and record["last_name"] is None

    def test_the_record_carries_exactly_the_contract_fields(self, campaign):
        assert set(export.lead_record(_deal(campaign))) == set(export.JSON_FIELDS)

    def test_the_record_carries_the_text_the_qualifier_judged_on(self, campaign):
        """The facts a sender writes a message from cross as raw text, not as an
        extraction: summarising for a message is the receiver's job."""
        deal = _deal(campaign, profile_text="cto at acme, devtools, 40 staff")

        assert export.lead_record(deal)["profile_text"] == "cto at acme, devtools, 40 staff"

    def test_a_lead_with_no_profile_text_carries_an_empty_string(self, campaign):
        """Empty, never absent — a receiver keying on the field should not have to tell
        *no text* from *no such key*."""
        record = export.lead_record(_deal(campaign, profile_text=""))

        assert record["profile_text"] == ""

    def test_every_row_carries_when_it_was_qualified(self, campaign):
        """Provenance the file keeps: a caller tells which rows its own call produced by
        comparing against the time it started, and a sequencer imports only what is newer
        than its last import. A `new` flag would go stale the second time the file is
        read; a timestamp is true forever."""
        deal = _deal(campaign)

        stamped = export.lead_record(deal)["qualified_at"]

        assert stamped == deal.creation_date.isoformat(timespec="seconds")
        assert stamped > "2020"  # sortable as a string, which is the point of ISO 8601


# ── selection ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestLeadRecords:
    def test_a_lead_the_qualifier_rejected_is_never_exported(self, campaign):
        """The bug the live install exposed: a Deal is not an endorsement.

        An LLM rejection is `FAILED` + `wrong_fit`, campaign-scoped — it does **not**
        set `Lead.disqualified`, which is the permanent account-level exclusion. Filtering
        on `disqualified` alone exported 1,944 rows from a campaign where most deals were
        rejections, with `reason` reading "does not align well with the target market".
        """
        _deal(campaign)
        DealFactory(campaign=campaign, lead=_lead(), state=DealState.FAILED,
                    outcome="wrong_fit", reason="does not align with the target market")

        records = list(export.lead_records(campaign))

        assert len(records) == 1
        assert "does not align" not in records[0]["reason"]

    def test_an_opted_out_lead_is_never_exported(self, campaign):
        _deal(campaign)
        _deal(campaign, disqualified=True)

        assert len(list(export.lead_records(campaign))) == 1

    def test_the_export_never_touches_the_qualifier(self, campaign):
        """A read-only export must not fit a GP, and must not spend an LLM call.

        Scoring used to mean ``qualifier_for``, which is an O(n^3) fit over every label
        (minutes on a real campaign) and which calls ``ensure_anchors`` — so a cold
        campaign would have generated anchors, mutating campaign state from an export.
        """
        _deal(campaign)

        with patch("openoutreach.core.ml.qualifier.qualifier_for") as qualifier_for:
            assert len(list(export.lead_records(campaign))) == 1

        qualifier_for.assert_not_called()

    def test_an_unembedded_lead_is_still_exported(self, campaign):
        DealFactory(campaign=campaign, lead=LeadFactory(email="a@b.com"),
                    state=DealState.RESOLVED)

        assert len(list(export.lead_records(campaign))) == 1


# ── serialisation ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestWriters:
    def test_csv_headers_are_the_contract_in_order(self, campaign):
        _deal(campaign)
        stream = io.StringIO()

        export.write_csv(export.lead_records(campaign), stream)

        stream.seek(0)
        assert next(csv.reader(stream)) == list(export.RECORD_FIELDS)

    def test_csv_writes_a_missing_field_as_an_empty_cell(self, campaign):
        _deal(campaign, first_name=None)
        stream = io.StringIO()

        export.write_csv(export.lead_records(campaign), stream)

        stream.seek(0)
        assert next(csv.DictReader(stream))["first_name"] == ""

    def test_the_row_count_is_reported(self, campaign):
        _deal(campaign)
        _deal(campaign, email="second@acme.com")

        assert export.write_csv(export.lead_records(campaign), io.StringIO()) == 2

    def test_the_csv_is_a_projection_and_leaves_the_profile_text_out(self, campaign):
        """A paragraph in a custom variable is useless to an importer, and would cost the
        property that makes the CSV worth having: it imports with no column mapping."""
        _deal(campaign, profile_text="cto at acme, devtools, 40 staff")
        stream = io.StringIO()

        export.write_csv(export.lead_records(campaign), stream)

        assert "profile_text" not in stream.getvalue()

    def test_json_lines_writes_the_whole_record_one_object_per_line(self, campaign):
        """A truncated stream stays usable — every complete record before the break has
        already been delivered."""
        _deal(campaign, profile_text="cto at acme")
        _deal(campaign, email="second@acme.com", profile_text="head of eng at beta")
        stream = io.StringIO()

        count = export.write_json_lines(export.lead_records(campaign), stream)

        lines = stream.getvalue().splitlines()
        assert count == 2 and len(lines) == 2
        assert [json.loads(line)["profile_text"] for line in lines] == [
            "cto at acme", "head of eng at beta"]
        assert set(json.loads(lines[0])) == set(export.JSON_FIELDS)


# ── counting ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestExportCounts:
    """One definition of *exportable*, so `status` and a `find` goal cannot disagree
    about what "ten leads" means."""

    def test_it_counts_the_rows_the_export_would_write(self, campaign):
        _deal(campaign)
        DealFactory(campaign=campaign, lead=_lead(email="x@y.com"), state=DealState.FAILED,
                    outcome="wrong_fit", reason="no fit")

        assert export.export_counts(campaign) == (1, 1)

    def test_a_row_without_an_address_still_counts_as_exportable(self, campaign):
        """Exportable is not mailable: an address is an enrichment, never a
        precondition."""
        _deal(campaign, email=None)

        assert export.export_counts(campaign) == (1, 0)
