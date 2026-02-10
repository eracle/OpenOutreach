# tests/db/test_profiles.py
import pytest

from linkedin.db.crm_profiles import (
    url_to_public_id,
    public_id_to_url,
    set_profile_state,
    get_profile,
    save_scraped_profile,
    get_updated_at_map,
    add_profile_urls,
    get_next_url_to_scrape,
    count_pending_scrape,
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

    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="Empty URL"):
            url_to_public_id("")

    def test_non_profile_url_raises(self):
        with pytest.raises(ValueError, match="Not a valid /in/ profile URL"):
            url_to_public_id("https://www.linkedin.com/feed/")

    def test_only_domain_raises(self):
        with pytest.raises(ValueError, match="Not a valid /in/ profile URL"):
            url_to_public_id("https://www.linkedin.com/")


# ── public_id_to_url (pure function) ──

class TestPublicIdToUrl:
    def test_standard_id(self):
        assert public_id_to_url("johndoe") == "https://www.linkedin.com/in/johndoe/"

    def test_empty_id(self):
        assert public_id_to_url("") == ""

    def test_id_with_slashes_stripped(self):
        assert public_id_to_url("/johndoe/") == "https://www.linkedin.com/in/johndoe/"


# ── DB operations using fake_session (Django ORM) ──

@pytest.mark.django_db
class TestSetAndGetProfile:
    def test_set_state_creates_profile(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.DISCOVERED.value)
        row = get_profile(fake_session, "alice")
        assert row is not None
        assert row["state"] == ProfileState.DISCOVERED.value

    def test_set_state_updates_existing(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.DISCOVERED.value)
        set_profile_state(fake_session, "alice", ProfileState.ENRICHED.value)
        row = get_profile(fake_session, "alice")
        assert row["state"] == ProfileState.ENRICHED.value

    def test_get_nonexistent_returns_none(self, fake_session):
        assert get_profile(fake_session, "nobody") is None


@pytest.mark.django_db
class TestSaveScrapedProfile:
    def test_saves_new_profile(self, fake_session):
        profile_data = {"full_name": "Alice Smith", "headline": "Engineer", "positions": []}
        raw_data = {"included": []}
        save_scraped_profile(
            fake_session,
            "https://www.linkedin.com/in/alicesmith/",
            profile_data,
            raw_data,
        )
        row = get_profile(fake_session, "alicesmith")
        assert row is not None
        assert row["profile"]["full_name"] == "Alice Smith"
        assert row["state"] == ProfileState.ENRICHED.value

    def test_updates_existing_profile(self, fake_session):
        set_profile_state(fake_session, "alicesmith", ProfileState.DISCOVERED.value)
        save_scraped_profile(
            fake_session,
            "https://www.linkedin.com/in/alicesmith/",
            {"full_name": "Alice Smith v2", "positions": []},
            None,
        )
        row = get_profile(fake_session, "alicesmith")
        assert row["profile"]["full_name"] == "Alice Smith v2"
        assert row["state"] == ProfileState.ENRICHED.value

    def test_invalid_url_raises(self, fake_session):
        with pytest.raises(ValueError, match="Not a valid /in/ profile URL"):
            save_scraped_profile(fake_session, "https://linkedin.com/feed/", {}, None)


@pytest.mark.django_db
class TestAddProfileUrls:
    def test_adds_discovered_profiles(self, fake_session):
        from crm.models import Lead
        urls = [
            "https://www.linkedin.com/in/alice/",
            "https://www.linkedin.com/in/bob/",
        ]
        add_profile_urls(fake_session, urls)
        assert Lead.objects.filter(
            website__in=[
                "https://www.linkedin.com/in/alice/",
                "https://www.linkedin.com/in/bob/",
            ]
        ).count() == 2

    def test_ignores_duplicates(self, fake_session):
        from crm.models import Lead
        urls = ["https://www.linkedin.com/in/alice/"]
        add_profile_urls(fake_session, urls)
        add_profile_urls(fake_session, urls)
        assert Lead.objects.filter(
            website="https://www.linkedin.com/in/alice/"
        ).count() == 1

    def test_empty_list_does_nothing(self, fake_session):
        from crm.models import Lead
        initial_count = Lead.objects.count()
        add_profile_urls(fake_session, [])
        assert Lead.objects.count() == initial_count


@pytest.mark.django_db
class TestGetNextUrlToScrape:
    def test_returns_discovered_profiles(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.DISCOVERED.value)
        set_profile_state(fake_session, "bob", ProfileState.ENRICHED.value)
        urls = get_next_url_to_scrape(fake_session, limit=10)
        assert len(urls) == 1
        assert "alice" in urls[0]

    def test_respects_limit(self, fake_session):
        for name in ["a", "b", "c"]:
            set_profile_state(fake_session, name, ProfileState.DISCOVERED.value)
        urls = get_next_url_to_scrape(fake_session, limit=2)
        assert len(urls) == 2

    def test_empty_when_none_discovered(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.ENRICHED.value)
        assert get_next_url_to_scrape(fake_session) == []


@pytest.mark.django_db
class TestCountPendingScrape:
    def test_counts_only_discovered(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.DISCOVERED.value)
        set_profile_state(fake_session, "bob", ProfileState.DISCOVERED.value)
        set_profile_state(fake_session, "charlie", ProfileState.ENRICHED.value)
        assert count_pending_scrape(fake_session) == 2

    def test_zero_when_empty(self, fake_session):
        assert count_pending_scrape(fake_session) == 0


@pytest.mark.django_db
class TestGetUpdatedAtMap:
    def test_returns_timestamps_for_existing(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.ENRICHED.value)
        result = get_updated_at_map(fake_session, ["alice", "nobody"])
        assert len(result) == 1
        assert "alice" in result

    def test_empty_input_returns_empty_dict(self, fake_session):
        result = get_updated_at_map(fake_session, [])
        assert result == {}

    def test_no_matches_returns_empty_dict(self, fake_session):
        result = get_updated_at_map(fake_session, ["nobody"])
        assert result == {}
