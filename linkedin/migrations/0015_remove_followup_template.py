# linkedin/migrations/0015_remove_followup_template.py
"""Remove followup_template field and clean up stale freemium campaigns.

The followup_template field is replaced by the agentic follow-up system.
Also removes duplicate freemium campaigns caused by the is_promo →
is_partner → is_freemium rename (e.g. "Partner Outreach" alongside
"Freemium Outreach"), keeping only the most recent one.
"""

from django.db import migrations


def remove_stale_freemium(apps, schema_editor):
    Campaign = apps.get_model("linkedin", "Campaign")
    Department = apps.get_model("common", "Department")

    freemium = list(Campaign.objects.filter(is_freemium=True).order_by("-pk"))
    if len(freemium) <= 1:
        return

    for old in freemium[1:]:
        dept = old.department
        old.delete()
        if not Campaign.objects.filter(department=dept).exists():
            Department.objects.filter(pk=dept.pk).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0014_backfill_disqualified_deals"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="campaign",
            name="followup_template",
        ),
        migrations.RunPython(remove_stale_freemium, migrations.RunPython.noop),
    ]
