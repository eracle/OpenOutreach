# Email-first pivot — the Mailbox model (SMTP + IMAP), merged into the initial.
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Mailbox",
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
                ("host", models.CharField(default="smtp.gmail.com", max_length=255)),
                ("port", models.PositiveIntegerField(default=587)),
                ("username", models.CharField(max_length=320, unique=True)),
                ("password", models.CharField(max_length=255)),
                ("from_address", models.EmailField(max_length=320)),
                ("daily_limit", models.PositiveIntegerField(default=30)),
                ("imap_host", models.CharField(default="imap.gmail.com", max_length=255)),
                ("imap_port", models.PositiveIntegerField(default=993)),
            ],
            options={
                "verbose_name_plural": "Mailboxes",
            },
        ),
    ]
