from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0010_move_engine_models_to_core"),
    ]

    operations = [
        migrations.AddField(
            model_name="linkedinprofile",
            name="contribute_to_hub",
            field=models.BooleanField(default=True),
        ),
    ]
