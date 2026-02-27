from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("linkedin", "0005_update_followup_template"),
    ]

    operations = [
        migrations.CreateModel(
            name="ActionLog",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "action_type",
                    models.CharField(
                        choices=[
                            ("connect", "Connect"),
                            ("follow_up", "Follow Up"),
                        ],
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="action_logs",
                        to="linkedin.campaign",
                    ),
                ),
                (
                    "linkedin_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="action_logs",
                        to="linkedin.linkedinprofile",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["linkedin_profile", "action_type", "created_at"],
                        name="linkedin_act_linkedi_idx",
                    ),
                ],
            },
        ),
    ]
