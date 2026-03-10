"""Backfill ProfileEmbedding.label from historical CRM decisions.

Leads disqualified before the labeling system (2026-03-09) were never
written to ProfileEmbedding.label.  This migration sets:
  label=0  for embeddings whose Lead is disqualified
  label=1  for embeddings whose Lead was promoted to a Contact
Only touches rows where label IS NULL.
"""

from django.db import migrations
from django.utils import timezone


def backfill_labels(apps, schema_editor):
    ProfileEmbedding = apps.get_model("linkedin", "ProfileEmbedding")
    Lead = apps.get_model("crm", "Lead")
    Contact = apps.get_model("crm", "Contact")

    unlabeled = ProfileEmbedding.objects.filter(label__isnull=True)
    unlabeled_ids = set(unlabeled.values_list("lead_id", flat=True))
    if not unlabeled_ids:
        return

    disqualified_ids = set(
        Lead.objects.filter(id__in=unlabeled_ids, disqualified=True)
        .values_list("id", flat=True)
    )
    promoted_ids = set(
        Contact.objects.filter(lead__in=unlabeled_ids)
        .values_list("lead", flat=True)
    )

    now = timezone.now()

    if disqualified_ids:
        ProfileEmbedding.objects.filter(
            lead_id__in=disqualified_ids, label__isnull=True
        ).update(label=0, labeled_at=now)

    if promoted_ids:
        ProfileEmbedding.objects.filter(
            lead_id__in=promoted_ids, label__isnull=True
        ).update(label=1, labeled_at=now)


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0008_rename_new_stage_to_qualified"),
    ]

    operations = [
        migrations.RunPython(backfill_labels, migrations.RunPython.noop),
    ]
