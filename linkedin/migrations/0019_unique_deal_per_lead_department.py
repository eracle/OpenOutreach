"""Add unique constraint: one Deal per Lead per Department.

Deduplicates existing rows first (keeps the most recent Deal per lead+department),
then creates a partial unique index.
"""
from django.db import migrations


def dedup_deals(apps, schema_editor):
    """Delete older duplicate Deals, keeping the one with the highest pk."""
    Deal = apps.get_model("crm", "Deal")
    from django.db.models import Max

    dupes = (
        Deal.objects.filter(lead_id__isnull=False, department_id__isnull=False)
        .values("lead_id", "department_id")
        .annotate(max_id=Max("id"))
    )
    for group in dupes:
        Deal.objects.filter(
            lead_id=group["lead_id"],
            department_id=group["department_id"],
        ).exclude(id=group["max_id"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0018_remove_profileembedding_label_fields"),
        ("crm", "__latest__"),
    ]

    operations = [
        migrations.RunPython(dedup_deals, migrations.RunPython.noop),
        migrations.RunSQL(
            sql=(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_crm_deal_lead_department "
                "ON crm_deal (lead_id, department_id) "
                "WHERE lead_id IS NOT NULL AND department_id IS NOT NULL;"
            ),
            reverse_sql="DROP INDEX IF EXISTS uq_crm_deal_lead_department;",
        ),
    ]
