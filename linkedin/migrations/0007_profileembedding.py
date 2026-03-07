from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0006_actionlog"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProfileEmbedding",
            fields=[
                ("lead_id", models.IntegerField(primary_key=True, serialize=False)),
                ("public_identifier", models.CharField(max_length=200)),
                ("embedding", models.BinaryField()),
                ("label", models.IntegerField(blank=True, null=True)),
                ("llm_reason", models.CharField(blank=True, default="", max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("labeled_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "app_label": "linkedin",
            },
        ),
    ]
