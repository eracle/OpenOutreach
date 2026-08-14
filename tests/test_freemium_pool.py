import numpy as np

from openoutreach.core.models import Campaign
from openoutreach.core.pipeline.freemium_pool import find_freemium_candidate
from openoutreach.crm.models import Deal, DealState, Lead


class DummyQualifier:
    def rank_profiles(self, profiles):
        return sorted(profiles, key=lambda profile: profile["lead_id"])


def _embedded_lead(profile_url: str) -> Lead:
    lead = Lead.objects.create(profile_url=profile_url)
    lead.embedding_array = np.ones(384, dtype=np.float32)
    lead.save(update_fields=["embedding"])
    return lead


def test_freemium_pool_never_selects_operator_owned_leads(db, campaign):
    freemium = Campaign.objects.create(name="Freemium", is_freemium=True)

    freemium_seed = _embedded_lead("https://example.com/freemium-seed")
    operator_only = _embedded_lead("https://example.com/operator-owned")

    Deal.objects.create(
        campaign=freemium,
        lead=freemium_seed,
        state=DealState.QUALIFIED,
    )
    Deal.objects.create(
        campaign=campaign,
        lead=operator_only,
        state=DealState.QUALIFIED,
    )

    candidate = find_freemium_candidate(freemium, DummyQualifier())

    assert candidate == {
        "lead_id": freemium_seed.pk,
        "profile_url": freemium_seed.profile_url,
    }
