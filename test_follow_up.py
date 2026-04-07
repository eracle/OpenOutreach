import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")
import django
django.setup()

import logging
logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")

from linkedin.browser.registry import get_or_create_session
from linkedin.models import LinkedInProfile
from linkedin.tasks.follow_up import _handle_post_accept_video_flow

public_id = sys.argv[1] if len(sys.argv) > 1 else None
if not public_id:
    print("Usage: .venv/bin/python test_follow_up.py <public_id>")
    sys.exit(1)

profile = LinkedInProfile.objects.filter(active=True).first()
session = get_or_create_session(profile)
session.campaign = session.campaigns[0]

from crm.models import Lead
from linkedin.url_utils import public_id_to_url

lead = Lead.objects.filter(public_identifier=public_id).first()
if not lead:
    print(f"No lead found for {public_id}")
    sys.exit(1)

profile_dict = lead.to_profile_dict() or {"public_identifier": public_id, "url": public_id_to_url(public_id)}

print(f"Testing follow-up as @{profile.linkedin_username} → {public_id}")
result = _handle_post_accept_video_flow(session, public_id, profile_dict, session.campaign.pk)
print(f"Result: {result}")
