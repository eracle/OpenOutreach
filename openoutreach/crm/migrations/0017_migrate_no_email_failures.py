from django.db import migrations


def forwards(apps, schema_editor):
    """Move enrichment misses off the FAILED bucket onto their own terminal state.

    Historically a 'no email' miss was recorded as FAILED with a blank outcome and
    reason='no email'. Those are fit positives (the LLM qualified them; only
    reachability failed), so they now live at NO_EMAIL_BETTERCONTACT — which the ML
    labeler counts as label=1.
    """
    Deal = apps.get_model("crm", "Deal")
    Deal.objects.filter(state="Failed", outcome="", reason="no email").update(
        state="No Email (BetterContact)", reason="",
    )


def backwards(apps, schema_editor):
    Deal = apps.get_model("crm", "Deal")
    Deal.objects.filter(state="No Email (BetterContact)").update(
        state="Failed", reason="no email",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0016_alter_deal_state"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
