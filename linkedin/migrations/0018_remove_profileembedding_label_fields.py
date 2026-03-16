from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0017_backfill_deal_next_step_reason"),
    ]

    operations = [
        migrations.RemoveField(model_name="profileembedding", name="label"),
        migrations.RemoveField(model_name="profileembedding", name="llm_reason"),
        migrations.RemoveField(model_name="profileembedding", name="labeled_at"),
    ]
