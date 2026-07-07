# Email-first pivot — SiteConfig/Task/Campaign config changes, merged into one.
# Retypes Task.task_type off the connect legs onto find_email/follow_up/email,
# adds the contacts-cache + country_code + Campaign discovery fields, renames
# finder_api_key→bettercontact_api_key, and folds llm_provider into ai_model as a
# pydantic-ai provider:model id.
from django.db import migrations, models

_LEGACY_MODEL_PREFIXES = {
    "gpt": "openai", "o1": "openai", "o3": "openai",
    "claude": "anthropic", "gemini": "google",
}


def fold_provider_into_model(apps, schema_editor):
    SiteConfig = apps.get_model("core", "SiteConfig")
    for cfg in SiteConfig.objects.all():
        model = (cfg.ai_model or "").strip()
        if not model or ":" in model:
            continue
        provider = next(
            (p for prefix, p in _LEGACY_MODEL_PREFIXES.items() if model.startswith(prefix)),
            cfg.llm_provider or "openai",
        )
        cfg.ai_model = f"{provider}:{model}"
        cfg.save(update_fields=["ai_model"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_siteconfig_finder_api_key"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("connect", "Connect"),
                    ("check_pending", "Check Pending"),
                    ("follow_up", "Follow Up"),
                    ("email", "Email"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="contacts_api_token",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="contacts_api_url",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.RenameField(
            model_name="siteconfig",
            old_name="finder_api_key",
            new_name="bettercontact_api_key",
        ),
        migrations.RunPython(fold_provider_into_model, migrations.RunPython.noop),
        migrations.RemoveField(model_name="siteconfig", name="llm_provider"),
        migrations.AlterField(
            model_name="siteconfig",
            name="ai_model",
            field=models.CharField(
                blank=True,
                default="",
                help_text="provider:model, e.g. anthropic:claude-sonnet-4-5-20250929",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="country_code",
            field=models.CharField(blank=True, default="", max_length=2),
        ),
        migrations.AlterField(
            model_name="task",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("find_email", "Find Email"),
                    ("collect_email", "Collect Email"),
                    ("follow_up", "Follow Up"),
                    ("email", "Email"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="discovery_offset",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="campaign",
            name="icp_filters",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
