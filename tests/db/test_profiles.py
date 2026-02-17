# tests/db/test_profiles.py
import pytest

from datetime import date, timedelta

from django.utils import timezone

from linkedin.db.crm_profiles import (
    url_to_public_id,
    public_id_to_url,
    set_profile_state,
    get_profile,
    get_updated_at_map,
    create_enriched_lead,
    disqualify_lead,
    promote_lead_to_contact,
    get_leads_for_qualification,
    count_leads_for_qualification,
    lead_exists,
    get_qualified_profiles,
    count_qualified_profiles,
    get_pending_profiles,
    get_connected_profiles,
)
from linkedin.navigation.enums import ProfileState


# ── url_to_public_id (pure function) ──

class TestUrlToPublicId:
    def test_standard_url(self):
        assert url_to_public_id("https://www.linkedin.com/in/johndoe/") == "johndoe"

    def test_url_without_trailing_slash(self):
        assert url_to_public_id("https://www.linkedin.com/in/johndoe") == "johndoe"

    def test_url_with_query_params(self):
        assert url_to_public_id("https://www.linkedin.com/in/johndoe?foo=bar") == "johndoe"

    def test_url_with_extra_path_segments(self):
        assert url_to_public_id("https://www.linkedin.com/in/johndoe/detail/contact-info/") == "johndoe"

    def test_percent_encoded_id(self):
        assert url_to_public_id("https://www.linkedin.com/in/john%20doe/") == "john doe"

    def test_empty_url_returns_none(self):
        assert url_to_public_id("") is None

    def test_non_profile_url_returns_none(self):
        assert url_to_public_id("https://www.linkedin.com/feed/") is None

    def test_only_domain_returns_none(self):
        assert url_to_public_id("https://www.linkedin.com/") is None


# ── public_id_to_url (pure function) ──

class TestPublicIdToUrl:
    def test_standard_id(self):
        assert public_id_to_url("johndoe") == "https://www.linkedin.com/in/johndoe/"

    def test_empty_id(self):
        assert public_id_to_url("") == ""

    def test_id_with_slashes_stripped(self):
        assert public_id_to_url("/johndoe/") == "https://www.linkedin.com/in/johndoe/"


# ── DB operations using fake_session (Django ORM) ──

SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


@pytest.mark.django_db
class TestLeadExists:
    def test_exists_after_create(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        assert lead_exists("https://www.linkedin.com/in/alice/") is True

    def test_not_exists(self, fake_session):
        assert lead_exists("https://www.linkedin.com/in/nobody/") is False

    def test_invalid_url(self, fake_session):
        assert lead_exists("https://linkedin.com/feed/") is False


@pytest.mark.django_db
class TestCreateEnrichedLead:
    def test_creates_lead_with_profile(self, fake_session):
        pk = create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        assert pk is not None
        row = get_profile(fake_session, "alice")
        assert row is not None
        assert row["profile"]["first_name"] == "Alice"

    def test_creates_company(self, fake_session):
        from crm.models import Lead
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        lead = Lead.objects.get(website="https://www.linkedin.com/in/alice/")
        assert lead.company is not None
        assert lead.company.full_name == "Acme"

    def test_returns_none_for_duplicate(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        pk2 = create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        assert pk2 is None

    def test_no_deal_created(self, fake_session):
        from crm.models import Deal
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        assert Deal.objects.filter(owner=fake_session.django_user).count() == 0

    def test_attaches_raw_data(self, fake_session):
        from common.models import TheFile
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
            data={"raw": "voyager"},
        )
        assert TheFile.objects.count() == 1

    def test_no_company_when_no_positions(self, fake_session):
        from crm.models import Lead
        profile = {"first_name": "Bob", "headline": "Freelancer", "positions": []}
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/bob/",
            profile,
        )
        lead = Lead.objects.get(website="https://www.linkedin.com/in/bob/")
        assert lead.company is None


@pytest.mark.django_db
class TestDisqualifyLead:
    def test_sets_disqualified(self, fake_session):
        from crm.models import Lead
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        disqualify_lead(fake_session, "alice", reason="Bad fit")
        lead = Lead.objects.get(website="https://www.linkedin.com/in/alice/")
        assert lead.disqualified is True

    def test_disqualified_state_in_get_profile(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        disqualify_lead(fake_session, "alice")
        row = get_profile(fake_session, "alice")
        assert row["state"] == "disqualified"


@pytest.mark.django_db
class TestPromoteLeadToContact:
    def test_creates_contact_and_deal(self, fake_session):
        from crm.models import Contact, Deal
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        contact, deal = promote_lead_to_contact(fake_session, "alice")
        assert contact is not None
        assert deal is not None
        assert deal.stage.name == "New"
        assert Contact.objects.count() == 1
        assert Deal.objects.count() == 1

    def test_raises_without_company(self, fake_session):
        profile = {"first_name": "Bob", "headline": "Freelancer", "positions": []}
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/bob/",
            profile,
        )
        with pytest.raises(ValueError, match="no Company"):
            promote_lead_to_contact(fake_session, "bob")

    def test_promotes_lead_contact_fk(self, fake_session):
        from crm.models import Lead
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        promote_lead_to_contact(fake_session, "alice")
        lead = Lead.objects.get(website="https://www.linkedin.com/in/alice/")
        assert lead.contact is not None


@pytest.mark.django_db
class TestGetLeadsForQualification:
    def test_returns_enriched_leads(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        leads = get_leads_for_qualification(fake_session)
        assert len(leads) == 1
        assert leads[0]["public_identifier"] == "alice"
        assert leads[0]["lead_id"] is not None

    def test_excludes_disqualified(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        disqualify_lead(fake_session, "alice")
        leads = get_leads_for_qualification(fake_session)
        assert len(leads) == 0

    def test_excludes_promoted(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        promote_lead_to_contact(fake_session, "alice")
        leads = get_leads_for_qualification(fake_session)
        assert len(leads) == 0

    def test_count_matches_list(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/bob/",
            {**SAMPLE_PROFILE, "first_name": "Bob"},
        )
        assert count_leads_for_qualification(fake_session) == 2
        assert len(get_leads_for_qualification(fake_session)) == 2


@pytest.mark.django_db
class TestSetAndGetProfile:
    def test_set_state_on_deal(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        promote_lead_to_contact(fake_session, "alice")
        set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
        row = get_profile(fake_session, "alice")
        assert row is not None
        assert row["state"] == ProfileState.PENDING.value

    def test_set_state_requires_deal(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        with pytest.raises(ValueError, match="No Deal"):
            set_profile_state(fake_session, "alice", ProfileState.NEW.value)

    def test_get_nonexistent_returns_none(self, fake_session):
        assert get_profile(fake_session, "nobody") is None


@pytest.mark.django_db
class TestGetUpdatedAtMap:
    def test_returns_timestamps_for_existing(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        result = get_updated_at_map(fake_session, ["alice", "nobody"])
        assert len(result) == 1
        assert "alice" in result

    def test_empty_input_returns_empty_dict(self, fake_session):
        result = get_updated_at_map(fake_session, [])
        assert result == {}


# ── get_qualified_profiles (Deals at "New" stage) ──

@pytest.mark.django_db
class TestGetQualifiedProfiles:
    def _promote(self, session, public_id="alice"):
        url = f"https://www.linkedin.com/in/{public_id}/"
        create_enriched_lead(session, url, SAMPLE_PROFILE)
        promote_lead_to_contact(session, public_id)

    def test_returns_qualified(self, fake_session):
        self._promote(fake_session)
        profiles = get_qualified_profiles(fake_session)
        assert len(profiles) == 1
        assert profiles[0]["public_identifier"] == "alice"

    def test_excludes_other_stages(self, fake_session):
        self._promote(fake_session)
        set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
        profiles = get_qualified_profiles(fake_session)
        assert len(profiles) == 0

    def test_count_qualified(self, fake_session):
        self._promote(fake_session, "alice")
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/bob/",
            {**SAMPLE_PROFILE, "first_name": "Bob"},
        )
        promote_lead_to_contact(fake_session, "bob")
        assert count_qualified_profiles(fake_session) == 2


# ── get_pending_profiles ──

@pytest.mark.django_db
class TestGetPendingProfiles:
    def _make_pending(self, session, public_id="alice"):
        url = f"https://www.linkedin.com/in/{public_id}/"
        create_enriched_lead(session, url, SAMPLE_PROFILE)
        promote_lead_to_contact(session, public_id)
        set_profile_state(session, public_id, ProfileState.PENDING.value)

    def test_returns_old_pending_default_backoff(self, fake_session):
        self._make_pending(fake_session)
        from crm.models import Deal
        deal = Deal.objects.filter(owner=fake_session.django_user).first()
        Deal.objects.filter(pk=deal.pk).update(
            update_date=timezone.now() - timedelta(hours=120)
        )

        profiles = get_pending_profiles(fake_session, recheck_after_hours=72)
        assert len(profiles) == 1
        assert profiles[0]["public_identifier"] == "alice"
        assert profiles[0]["meta"] == {}

    def test_excludes_recent_pending(self, fake_session):
        self._make_pending(fake_session)
        profiles = get_pending_profiles(fake_session, recheck_after_hours=72)
        assert len(profiles) == 0

    def test_uses_per_profile_backoff(self, fake_session):
        import json
        self._make_pending(fake_session)
        from crm.models import Deal
        deal = Deal.objects.filter(owner=fake_session.django_user).first()
        Deal.objects.filter(pk=deal.pk).update(
            update_date=timezone.now() - timedelta(hours=150),
            next_step=json.dumps({"backoff_hours": 200}),
        )
        profiles = get_pending_profiles(fake_session, recheck_after_hours=1)
        assert len(profiles) == 0


# ── get_connected_profiles ──

@pytest.mark.django_db
class TestGetConnectedProfiles:
    def test_returns_connected(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        promote_lead_to_contact(fake_session, "alice")
        set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)

        profiles = get_connected_profiles(fake_session)
        assert len(profiles) == 1


# ── Terminal states ──

@pytest.mark.django_db
class TestIgnoredState:
    def test_ignored_sets_inactive(self, fake_session):
        from crm.models import Deal
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        promote_lead_to_contact(fake_session, "alice")
        set_profile_state(fake_session, "alice", ProfileState.IGNORED.value)
        deal = Deal.objects.filter(owner=fake_session.django_user).first()
        assert deal.active is False
        assert deal.closing_reason is not None
        assert deal.closing_reason.name == "Ignored"

    def test_ignored_state_roundtrip(self, fake_session):
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        promote_lead_to_contact(fake_session, "alice")
        set_profile_state(fake_session, "alice", ProfileState.IGNORED.value)
        row = get_profile(fake_session, "alice")
        assert row["state"] == ProfileState.IGNORED.value
