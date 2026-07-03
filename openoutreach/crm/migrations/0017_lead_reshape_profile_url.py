from django.db import migrations, models


class Migration(migrations.Migration):
    """Reshape Lead onto profile_url (email-first pivot).

    Rename-preserving, not drop-and-recreate: profile_url inherits every existing
    linkedin_url value (and its unique constraint), email inherits api_email, so no
    lead data is lost. public_identifier / urn / contact_info are dead post-pivot
    (no LinkedIn scrape) and dropped; profile_text is the new firmographic text slot
    for the LLM qualifier, blank on existing rows.
    """

    dependencies = [
        ("crm", "0016_deal_next_follow_up_at_alter_deal_state"),
    ]

    operations = [
        migrations.RenameField(
            model_name="lead",
            old_name="linkedin_url",
            new_name="profile_url",
        ),
        migrations.RenameField(
            model_name="lead",
            old_name="api_email",
            new_name="email",
        ),
        migrations.AddField(
            model_name="lead",
            name="profile_text",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RemoveField(
            model_name="lead",
            name="public_identifier",
        ),
        migrations.RemoveField(
            model_name="lead",
            name="urn",
        ),
        migrations.RemoveField(
            model_name="lead",
            name="contact_info",
        ),
    ]
