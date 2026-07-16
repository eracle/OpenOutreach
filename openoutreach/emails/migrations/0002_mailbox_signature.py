"""Add ``Mailbox.signature`` — the per-box sign-off, NULL until asked.

Nullable on purpose: NULL means the onboarding signature step has never asked
this box, "" means the operator declined one. Collapsing them onto a single ""
would make declining indistinguishable from never-asked, so the step could only
key on emptiness — and would re-prompt a declining operator on every startup.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('emails', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='mailbox',
            name='signature',
            field=models.TextField(blank=True, default=None, null=True),
        ),
    ]
