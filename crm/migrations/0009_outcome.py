"""Add ``outcome`` enum field, migrate data from ``closing_reason``, drop ``closing_reason``."""
from django.db import migrations, models


_FORWARD_MAP = {
    "Completed": "converted",
    "Disqualified": "wrong_fit",
    "Failed": "unknown",
}

_REVERSE_MAP = {
    "converted": "Completed",
    "wrong_fit": "Disqualified",
}


def migrate_closing_reason_to_outcome(apps, schema_editor):
    Deal = apps.get_model("crm", "Deal")
    for old, new in _FORWARD_MAP.items():
        Deal.objects.filter(closing_reason=old).update(outcome=new)


def migrate_outcome_to_closing_reason(apps, schema_editor):
    Deal = apps.get_model("crm", "Deal")
    for new, old in _REVERSE_MAP.items():
        Deal.objects.filter(outcome=new).update(closing_reason=old)
    # Everything else maps back to "Failed"
    Deal.objects.filter(outcome__gt="").exclude(
        outcome__in=_REVERSE_MAP.keys(),
    ).update(closing_reason="Failed")


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0008_vacuum"),
    ]

    operations = [
        # 1. Add the new field
        migrations.AddField(
            model_name="deal",
            name="outcome",
            field=models.CharField(
                blank=True,
                choices=[
                    ("converted", "Converted"),
                    ("not_interested", "Not Interested"),
                    ("wrong_fit", "Wrong Fit"),
                    ("no_budget", "No Budget"),
                    ("has_solution", "Has Solution"),
                    ("bad_timing", "Bad Timing"),
                    ("unresponsive", "Unresponsive"),
                    ("unknown", "Unknown"),
                ],
                default="",
                max_length=20,
            ),
        ),
        # 2. Migrate data
        migrations.RunPython(
            migrate_closing_reason_to_outcome,
            migrate_outcome_to_closing_reason,
        ),
        # 3. Drop the old field
        migrations.RemoveField(
            model_name="deal",
            name="closing_reason",
        ),
    ]
