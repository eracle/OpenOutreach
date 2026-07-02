# Move the engine models (SiteConfig, Campaign, Task) from the `linkedin` app
# into `core` — step 1 of 2: adopt them in core's state, pointing at the
# existing linkedin_* tables. No database changes here; the companion
# linkedin.0010 deletes the models from linkedin's state, and core.0002
# renames the tables to their core_* defaults.
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("legacy", "0009_drop_legacy_pending_tasks"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="SiteConfig",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "llm_provider",
                            models.CharField(
                                choices=[
                                    ("openai", "OpenAI"),
                                    ("anthropic", "Anthropic"),
                                    ("google", "Google"),
                                    ("groq", "Groq"),
                                    ("mistral", "Mistral"),
                                    ("cohere", "Cohere"),
                                    ("openai_compatible", "OpenAI-compatible"),
                                ],
                                default="openai",
                                max_length=32,
                            ),
                        ),
                        ("llm_api_key", models.CharField(blank=True, default="", max_length=500)),
                        ("ai_model", models.CharField(blank=True, default="", max_length=200)),
                        ("llm_api_base", models.CharField(blank=True, default="", max_length=500)),
                    ],
                    options={
                        "verbose_name": "Site Configuration",
                        "verbose_name_plural": "Site Configuration",
                        "db_table": "linkedin_siteconfig",
                    },
                ),
                migrations.CreateModel(
                    name="Campaign",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("name", models.CharField(max_length=200, unique=True)),
                        ("product_docs", models.TextField(blank=True)),
                        ("campaign_objective", models.TextField(blank=True)),
                        ("booking_link", models.URLField(blank=True, max_length=500)),
                        ("is_freemium", models.BooleanField(default=False)),
                        ("action_fraction", models.FloatField(default=0.2)),
                        ("seed_public_ids", models.JSONField(blank=True, default=list)),
                        ("model_blob", models.BinaryField(blank=True, null=True)),
                        (
                            "users",
                            models.ManyToManyField(
                                blank=True,
                                related_name="campaigns",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "linkedin_campaign",
                    },
                ),
                migrations.CreateModel(
                    name="Task",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "task_type",
                            models.CharField(
                                choices=[
                                    ("connect", "Connect"),
                                    ("check_pending", "Check Pending"),
                                    ("follow_up", "Follow Up"),
                                ],
                                max_length=20,
                            ),
                        ),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("pending", "Pending"),
                                    ("running", "Running"),
                                    ("completed", "Completed"),
                                    ("failed", "Failed"),
                                ],
                                default="pending",
                                max_length=20,
                            ),
                        ),
                        ("scheduled_at", models.DateTimeField()),
                        ("payload", models.JSONField(default=dict)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("started_at", models.DateTimeField(blank=True, null=True)),
                        ("completed_at", models.DateTimeField(blank=True, null=True)),
                    ],
                    options={
                        "db_table": "linkedin_task",
                        "indexes": [
                            models.Index(
                                fields=["status", "scheduled_at"],
                                name="linkedin_ta_status_d04eec_idx",
                            )
                        ],
                    },
                ),
            ],
        ),
    ]
