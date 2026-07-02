import io

import joblib
from django.db import migrations


def compress_existing_blobs(apps, schema_editor):
    """Re-dump each Campaign.model_blob with joblib zlib compression.

    joblib.load auto-detects compression, so uncompressed blobs still load.
    Re-dumping with compress=3 shrinks them in place (~3-5x on GP pipelines).
    """
    Campaign = apps.get_model("legacy", "Campaign")
    for campaign in Campaign.objects.exclude(model_blob=None).iterator():
        pipeline = joblib.load(io.BytesIO(campaign.model_blob))
        buf = io.BytesIO()
        joblib.dump(pipeline, buf, compress=3)
        campaign.model_blob = buf.getvalue()
        campaign.save(update_fields=["model_blob"])


class Migration(migrations.Migration):

    dependencies = [
        ("legacy", "0003_siteconfig"),
    ]

    operations = [
        migrations.RunPython(compress_existing_blobs, migrations.RunPython.noop),
    ]
