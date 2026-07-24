from django.db import migrations, models

PREVIOUS_DEFAULT = 30
NEW_DEFAULT = 40


def raise_boxes_on_the_old_default(apps, schema_editor):
    """Carry existing boxes up to the new cap.

    Only rows still sitting on the previous default are touched — a box the
    operator retuned in the Admin keeps its value. Nothing ever offered 30 as a
    choice, so matching it means "never changed".
    """
    Mailbox = apps.get_model("emails", "Mailbox")
    Mailbox.objects.filter(daily_limit=PREVIOUS_DEFAULT).update(daily_limit=NEW_DEFAULT)


def lower_boxes_back(apps, schema_editor):
    Mailbox = apps.get_model("emails", "Mailbox")
    Mailbox.objects.filter(daily_limit=NEW_DEFAULT).update(daily_limit=PREVIOUS_DEFAULT)


class Migration(migrations.Migration):

    dependencies = [
        ("emails", "0002_mailbox_signature"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mailbox",
            name="daily_limit",
            field=models.PositiveIntegerField(default=NEW_DEFAULT),
        ),
        migrations.RunPython(raise_boxes_on_the_old_default, lower_boxes_back),
    ]
