"""Data migration: copy llm_reason from ProfileEmbedding into Deal.next_step.

For labeled ProfileEmbeddings with an associated Deal in the same department,
writes {"reason": llm_reason} into Deal.next_step (merging with existing JSON).
This preserves the reason for display/logging after the label fields are removed
from ProfileEmbedding in the next migration.
"""
import json

from django.db import migrations


def forwards(apps, schema_editor):
    ProfileEmbedding = apps.get_model("linkedin", "ProfileEmbedding")
    Deal = apps.get_model("crm", "Deal")

    labeled = ProfileEmbedding.objects.filter(label__isnull=False).exclude(llm_reason="")
    for pe in labeled:
        deals = Deal.objects.filter(lead_id=pe.lead_id)
        for deal in deals:
            try:
                meta = json.loads(deal.next_step) if deal.next_step else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            if "reason" not in meta:
                meta["reason"] = pe.llm_reason
                deal.next_step = json.dumps(meta)
                deal.save(update_fields=["next_step"])


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0016_add_seed_public_ids"),
        ("crm", "__latest__"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
