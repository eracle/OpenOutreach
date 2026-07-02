from pathlib import Path

from django.db import migrations, models


def _parse_env(text):
    """Minimal .env parser (no quotes/escapes — matches this project's usage)."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def migrate_env_to_db(apps, schema_editor):
    """Read LLM config from .env, store in SiteConfig, then delete .env."""
    SiteConfig = apps.get_model("legacy", "SiteConfig")

    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_file.exists():
        return

    values = _parse_env(env_file.read_text(encoding="utf-8"))

    SiteConfig.objects.update_or_create(pk=1, defaults={
        "llm_api_key": values.get("LLM_API_KEY", ""),
        "ai_model": values.get("AI_MODEL", ""),
        "llm_api_base": values.get("LLM_API_BASE", ""),
    })

    env_file.unlink()


class Migration(migrations.Migration):

    dependencies = [
        ("legacy", "0002_linkedinprofile_self_lead"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("llm_api_key", models.CharField(blank=True, default="", max_length=500)),
                ("ai_model", models.CharField(blank=True, default="", max_length=200)),
                ("llm_api_base", models.CharField(blank=True, default="", max_length=500)),
            ],
            options={
                "db_table": "linkedin_siteconfig",
                "verbose_name": "Site Configuration",
                "verbose_name_plural": "Site Configuration",
            },
        ),
        migrations.RunPython(migrate_env_to_db, migrations.RunPython.noop),
    ]
