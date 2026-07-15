"""Reshape DiscoveryQuery to the lazy walk and drop the legacy single cursor.

Discovery moved from a single ``(Campaign.icp_filters, discovery_offset)`` cursor
to a lazy best-first walk over **fetched** ``DiscoveryQuery`` nodes. This migration:

- reshapes ``DiscoveryQuery`` to the lazy schema — drops the ``status`` queue and
  ``parent`` provenance (only fetched nodes are stored now), adds the ``exhausted``
  flag, and swaps the ``(campaign, status)`` index for ``(campaign, exhausted)``;
- folds each campaign's cached ICP ``country_code`` onto ``Campaign.country_code``
  (the one bit of cursor state worth keeping — it geo-stamps every discovered lead
  and would otherwise cost an LLM call to re-derive);
- drops the two dead cursor columns.

The cursor *position* is not carried forward: the seed is regenerated on the next
discovery move and re-paged from 0. Lead Finder pages are free and ``create_lead``
dedups by ``profile_url``, so re-paging is a cheap, idempotent one-time cost.
"""
from django.db import migrations, models


def fold_country_code(apps, schema_editor):
    Campaign = apps.get_model("core", "Campaign")

    for campaign in Campaign.objects.all():
        spec = campaign.icp_filters or {}
        country_code = spec.get("country_code", "")
        if country_code and not campaign.country_code:
            campaign.country_code = country_code
            campaign.save(update_fields=["country_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_campaign_country_code_discoveryquery"),
    ]

    operations = [
        migrations.AddField(
            model_name="discoveryquery",
            name="exhausted",
            field=models.BooleanField(default=False),
        ),
        migrations.RemoveIndex(
            model_name="discoveryquery",
            name="discovery_camp_status_idx",
        ),
        migrations.RemoveField(model_name="discoveryquery", name="status"),
        migrations.RemoveField(model_name="discoveryquery", name="parent"),
        migrations.AddIndex(
            model_name="discoveryquery",
            index=models.Index(fields=["campaign", "exhausted"], name="discovery_camp_exhausted_idx"),
        ),
        # Reverse is a no-op: reversing re-adds the (empty) cursor columns; the
        # folded country_code stays put.
        migrations.RunPython(fold_country_code, migrations.RunPython.noop),
        migrations.RemoveField(model_name="campaign", name="icp_filters"),
        migrations.RemoveField(model_name="campaign", name="discovery_offset"),
    ]
