"""Make ``Mailbox.signature`` nullable, so NULL means "never asked".

``0002`` added the column as ``NOT NULL DEFAULT ''``, which made "declined a
signature" and "connected this box before signatures existed" the same value.
The onboarding mailbox step is satisfied by ``has_mailbox()``, so it never re-ran
for an existing box and the prompt never fired: every install that had a mailbox
before ``0002`` sends unsigned mail, permanently, with nothing to notice it by.

Splitting the two states fixes that — NULL is asked once by the onboarding
signature step, "" sticks and is never re-asked. Existing "" rows backfill to
NULL because on an install that already applied ``0002`` they can only mean
*never asked*: the prompt is the sole writer of a non-NULL value, and it never
ran. A box connected after ``0002`` whose operator genuinely declined gets asked
once more — one prompt, and then it sticks.
"""
from django.db import migrations, models


def unasked_signatures_to_null(apps, schema_editor):
    Mailbox = apps.get_model("emails", "Mailbox")
    Mailbox.objects.filter(signature="").update(signature=None)


def null_signatures_to_blank(apps, schema_editor):
    """Reverse: re-collapse NULL onto "" so the column can go back to NOT NULL."""
    Mailbox = apps.get_model("emails", "Mailbox")
    Mailbox.objects.filter(signature__isnull=True).update(signature="")


class Migration(migrations.Migration):

    dependencies = [
        ("emails", "0002_mailbox_signature"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mailbox",
            name="signature",
            field=models.TextField(blank=True, default=None, null=True),
        ),
        migrations.RunPython(unasked_signatures_to_null, null_signatures_to_blank),
    ]
