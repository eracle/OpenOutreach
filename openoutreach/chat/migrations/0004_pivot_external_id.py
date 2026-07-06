# Email-first pivot — generalize the per-channel message id, merged into one.
# Drops the LinkedIn-only recipients/to columns and renames linkedin_urn→external_id
# rename-preserving (existing rows keep their id; the column now holds an RFC-5322
# Message-ID for email, a Voyager entityUrn for legacy LinkedIn rows).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0003_chatmessage_deal_fk"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="chatmessage",
            name="recipients",
        ),
        migrations.RemoveField(
            model_name="chatmessage",
            name="to",
        ),
        migrations.RemoveConstraint(
            model_name="chatmessage",
            name="uniq_deal_linkedin_urn",
        ),
        migrations.RenameField(
            model_name="chatmessage",
            old_name="linkedin_urn",
            new_name="external_id",
        ),
        migrations.AddConstraint(
            model_name="chatmessage",
            constraint=models.UniqueConstraint(
                fields=["deal", "external_id"], name="uniq_deal_external_id",
            ),
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="external_id",
            field=models.CharField(
                help_text=(
                    "Message identity, used for dedup (per deal): the RFC-5322 "
                    "Message-ID of the email. (Legacy LinkedIn rows hold a Voyager "
                    "entityUrn.)"
                ),
                max_length=300,
                verbose_name="External message id",
            ),
        ),
    ]
