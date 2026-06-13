from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0013_alter_deal_state"),
        ("emails", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="deal",
            name="email_subject",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="deal",
            name="mailbox",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deals",
                to="emails.mailbox",
            ),
        ),
    ]
