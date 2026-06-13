from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0003_chatmessage_deal_fk"),
    ]

    operations = [
        # Drop the old (deal, linkedin_urn) uniqueness before reshaping the column.
        migrations.RemoveConstraint(
            model_name="chatmessage",
            name="uniq_deal_linkedin_urn",
        ),
        # linkedin_urn → external_id (now holds a urn OR an email Message-ID).
        migrations.RenameField(
            model_name="chatmessage",
            old_name="linkedin_urn",
            new_name="external_id",
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="external_id",
            field=models.CharField(
                blank=True,
                null=True,
                max_length=300,
                help_text="Voyager entityUrn (LinkedIn) or RFC-5322 Message-ID (email); per-deal dedup key",
                verbose_name="External message id",
            ),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="channel",
            field=models.CharField(
                choices=[("linkedin", "LinkedIn"), ("email", "Email")],
                default="linkedin",
                max_length=20,
                verbose_name="Channel",
            ),
        ),
        migrations.AddConstraint(
            model_name="chatmessage",
            constraint=models.UniqueConstraint(
                fields=["deal", "channel", "external_id"],
                name="uniq_deal_channel_external_id",
            ),
        ),
    ]
