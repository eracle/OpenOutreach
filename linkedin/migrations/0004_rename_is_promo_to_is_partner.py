# linkedin/migrations/0004_rename_is_promo_to_is_partner.py
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0003_unify_campaigns"),
    ]

    operations = [
        migrations.RenameField(
            model_name="campaign",
            old_name="is_promo",
            new_name="is_partner",
        ),
    ]
