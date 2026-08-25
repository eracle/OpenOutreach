# openoutreach/core/export.py
"""The lead export contract — one record shape, printed by the verb that found them.

This is the finder's **public output**: what leaves OpenOutreach and reaches whatever
the operator actually sends with. The boundary card
(``roadmap/p1-e3-leadfinder-sequencer-boundary.md`` in openoutreach-docs) specifies it,
and the rule it exists to serve is that our own sender gets no privileged path — a
sequencer, a CRM and a spreadsheet all read the same rows.

**The columns are the importers', not ours.** Instantly and Smartlead both *require*
``email``, ``first_name`` and ``last_name``, and both recognise ``company``, ``title``,
``website`` and ``linkedin_url`` as standard fields, mapping anything else to a custom
variable. So the record uses those names exactly — ``company``, not ``company_name``;
``title``, not ``job_title`` — and a file exported here imports without column mapping.
Everything we might like to ship but they do not know (state, campaign, country,
discovery provenance) is left out rather than dumped in as noise variables.

**One record, two serialisations.** ``JSON_FIELDS`` is the record; ``RECORD_FIELDS`` is the
importer-shaped projection of it, and the CSV writer projects rather than defining a second
schema, so the two cannot drift. The whole record crosses as JSON Lines (``find --json``,
read by a sender on the other side of a pipe); the CSV drops ``profile_text``, because a
paragraph in a custom variable is useless to an importer and would cost the property that
makes the CSV worth having.

**The compatibility rule is the substitute for the shared package we are not building**: a
receiver ignores keys it does not know, and this side never renames a key or repurposes
one — it only ever adds. Two repos and one record cannot stay in step on good intentions.

**There is no score column, deliberately.** An earlier version exported the GP's
``P(f>0.5)``. That was a category error: ``core/pipeline/ready_pool.py`` defines
``min_gp_confidence`` as "the paid-lookup spend gate **and nothing else**" — the GP
decides whether to spend a credit resolving an address, not whether a lead fits. The fit
verdict is the LLM's and it is already here as ``reason``, in language a person reads.
Exporting the posterior invited thresholding on a number nobody calibrated, and separated
nothing: every lead in this file already has a Deal, so it already passed the qualifier.

It also made the export expensive and unsafe. Scoring meant ``qualifier_for(campaign)``,
which warm-starts over every label and fits a GP — O(n³), minutes on a real campaign
(2,538 deals on the live install; the docstring there assumes "tens to low hundreds") —
and which calls ``ensure_anchors``, so a cold campaign would have made **LLM calls and
mutated campaign state from a read-only export**. This module now touches nothing but the
database.
"""
from __future__ import annotations

import csv
import json
from typing import IO, Iterable

# The record, in order.
#
# Required by Instantly + Smartlead: email, first_name, last_name.
# Standard-mapped by both: company, title, website, linkedin_url.
# A custom variable, and the reason this product exists: reason.
# Ours: lead_id — the join key for outcomes coming back, since a sequencer echoes
# custom variables in its webhooks and an address can change under us.
# Ours: qualified_at — when the verdict was written, so a file carries its own
# provenance. An agent tells which rows its own call produced by comparing against the
# time it started; a sequencer imports only what is newer than its last import. A `new`
# flag would have been the obvious alternative and is wrong: invocation-relative state
# written into a file that outlives the invocation is a lie the second time it is read.
RECORD_FIELDS = (
    "email",
    "first_name",
    "last_name",
    "company",
    "title",
    "website",
    "linkedin_url",
    "reason",
    "lead_id",
    "qualified_at",
)

# The record itself: the importer's columns plus the one field only a sender needs.
#
# `profile_text` is the raw firmographic string the qualifier judged on — the facts a
# message is written from, crossing as text rather than as an extraction. **Summarising
# for a message is the sender's job**: the extraction is tuned for the reader who wants
# it (an opener wants the recent and the specific, a verdict wants the durable), it is
# paid for only for people actually written to, and one text derived twice on two sides
# with no rule for which wins is the drift this avoids.
JSON_FIELDS = RECORD_FIELDS + ("profile_text",)


def lead_record(deal) -> dict:
    """One Deal as an export record — the full record, `JSON_FIELDS`.

    The Deal, not the Lead, is the unit: the qualification verdict (``reason``) is
    per-campaign, and the same person can be a lead in two campaigns with two different
    answers.

    ``reason`` is **operator-facing**: it is evidence for the person running this — the
    justification for a yes/no, third-person and evaluative — never text for the person
    receiving the mail.
    """
    lead = deal.lead
    company = lead.company
    return {
        "email": lead.email,
        # From the enrichment provider's own response, never split in-house. Null for a
        # lead resolved through the free hub cache, which never calls BetterContact.
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "company": company.name if company else None,
        "title": lead.job_title,
        "website": company.domain if company else None,
        "linkedin_url": lead.profile_url,
        "reason": deal.reason,
        "lead_id": lead.pk,
        # ISO 8601, UTC, second resolution — a string a reader and `sort` both handle.
        "qualified_at": deal.creation_date.isoformat(timespec="seconds"),
        # Empty, never absent, for a lead that has none: a receiver keying on the field
        # should not have to tell "no text" from "no such key".
        "profile_text": lead.profile_text or "",
    }


def lead_records(campaign) -> Iterable[dict]:
    """Every lead in ``campaign`` the qualifier **accepted**, as records, oldest first.

    A lead is judged once it has a Deal — that is where the LLM's ``reason`` lives, so
    an unjudged lead has nothing to say in a contract whose selling point is *why this
    lead*. But a Deal is not an endorsement: the two rejections are separate columns and
    both have to be excluded, which is the trap this filter exists to close.

    - **`FAILED`** is the LLM's own rejection, campaign-scoped (`FAILED` + `wrong_fit`).
      The `reason` on those rows reads *"does not align well with the target market"* —
      exporting them hands a sender the people the model explicitly said no to.
    - **`Lead.disqualified`** is the permanent, account-level exclusion (an opt-out).

    Filtering only on `disqualified` catches the second and misses the first, which is
    what shipped and what the live install exposed: 1,944 rows exported from a campaign
    where most deals were rejections.

    Lazy on purpose: one indexed query streamed straight to the writer, so a campaign
    with thousands of deals never materialises twice.
    """
    from openoutreach.crm.models import Deal, DealState

    deals = (
        Deal.objects.filter(campaign=campaign, lead__disqualified=False)
        .exclude(state=DealState.FAILED)
        .select_related("lead", "lead__company")
        .order_by("lead__creation_date")
    )
    return (lead_record(deal) for deal in deals.iterator())


# ── serialisation ────────────────────────────────────────────────

def write_csv(records: Iterable[dict], stream: IO[str]) -> int:
    """Write records as CSV with a header row, projected to ``RECORD_FIELDS``.

    ``None`` writes as an empty cell — the csv module's own behaviour, which is exactly
    what an importer expects for a field we were never told.

    ``extrasaction="ignore"`` is what makes this a **projection** of the one record
    rather than a second schema: a field added to ``JSON_FIELDS`` for a sender does not
    silently become a column an importer has to map.
    """
    writer = csv.DictWriter(stream, fieldnames=list(RECORD_FIELDS), extrasaction="ignore")
    writer.writeheader()
    count = 0
    for record in records:
        writer.writerow(record)
        count += 1
    return count


def write_json_lines(records: Iterable[dict], stream: IO[str]) -> int:
    """Write the full records as JSON Lines — one object per line. Returns the count.

    **Line-delimited, not one document**, because a truncated stream stays usable: an
    object that stops halfway is a parse error and the whole batch is lost, where a
    line-delimited stream has already delivered every complete record before the break
    and the rest is a re-run — which is safe, since ingest on the far side is idempotent.
    """
    count = 0
    for record in records:
        stream.write(json.dumps(record) + "\n")
        count += 1
    return count


# ── counting the deliverable ─────────────────────────────────────

def export_counts(campaign) -> tuple[int, int]:
    """Exportable rows, and how many carry an address.

    **An exportable row is not necessarily a mailable one.** The export excludes only the
    two rejections, so a `QUALIFIED` lead exports with a blank ``email`` — an address is
    an enrichment on top, never a precondition. Both numbers are counted from the records
    themselves rather than from a state standing in for them, which is what lets `status`
    and a `find` goal agree on what "ten leads" means.
    """
    exportable = with_email = 0
    for record in lead_records(campaign):
        exportable += 1
        with_email += bool(record.get("email"))
    return exportable, with_email
