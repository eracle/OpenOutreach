"""`run` hands what the finder found to the sender, as cold outreach, and then sends.

What the sender does with a cold deal — the opener, then the end — is the
sender's to test, and it does (`openoutsend/tests/test_end_to_end.py`). This side owns
only the hand-off: `find --json` into a buffer, the sender's own ingest, then `send`.
"""
import json
from unittest.mock import patch

import pytest

from openoutreach import __main__ as cli

LEAD = {"lead_id": "lead-1", "email": "sam@example.com", "first_name": "Sam"}

pytestmark = pytest.mark.django_db


def _find_one_lead(name, *args, stdout, **kwargs):
    """The finder's `find --json`, answering with one lead."""
    stdout.write(json.dumps(LEAD) + "\n")


def test_run_enrols_what_was_found_in_the_cold_sequence_and_sends():
    from openoutsend.leads.models import Deal

    sent = []
    with patch("openoutreach.wizard.onboard"), \
            patch("django.core.management.call_command", side_effect=_find_one_lead), \
            patch.object(cli, "_send", side_effect=lambda rest: sent.append(rest) or 0):
        assert cli._run([]) == 0

    deal = Deal.objects.get(lead__email=LEAD["email"])
    assert (deal.sequence, deal.state) == ("cold", "awaiting_E0")
    assert sent == [[]]
