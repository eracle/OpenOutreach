from django.db import migrations, models


class Migration(migrations.Migration):
    """Generalize the per-channel message id: linkedin_urn -> external_id.

    A RenameField (not add+drop) so existing rows keep their identity — the
    column purpose is unchanged (Voyager entityUrn for a DM, RFC-5322 Message-ID
    for an email), only the name generalizes off the LinkedIn channel.
    """

    dependencies = [
        ("chat", "0004_remove_chatmessage_recipients_remove_chatmessage_to"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="chatmessage",
            name="uniq_deal_linkedin_urn",
        ),
        migrations.RenameField(
            model_name="chatmessage",
            old_name="linkedin_urn",
            new_name="external_id",
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="external_id",
            field=models.CharField(
                max_length=300,
                help_text=(
                    "Per-channel message identity, used for dedup (per deal): the "
                    "Voyager entityUrn for a LinkedIn DM, the RFC-5322 Message-ID "
                    "for an email."
                ),
                verbose_name="External message id",
            ),
        ),
        migrations.AddConstraint(
            model_name="chatmessage",
            constraint=models.UniqueConstraint(
                fields=["deal", "external_id"], name="uniq_deal_external_id",
            ),
        ),
    ]
