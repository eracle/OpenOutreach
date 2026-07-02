# openoutreach/linkedin/models.py
from __future__ import annotations

import logging
from datetime import date

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from openoutreach.core.models import Campaign

logger = logging.getLogger(__name__)

# action_type → daily_limit_field
_RATE_LIMIT_FIELDS = {
    "connect": "connect_daily_limit",
    "follow_up": "follow_up_daily_limit",
}


class LinkedInProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="linkedin_profile",
    )
    self_lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    linkedin_username = models.CharField(max_length=200)
    linkedin_password = models.CharField(max_length=200, blank=True, default="")
    # ISO-3166 alpha-2 of the operator's country, collected at onboarding. Drives
    # the active-hours timezone (tz_country) and the email-jurisdiction defaults
    # (core/geo.py: newsletter opt-in + contacts-store contribution). Replaces the
    # old LinkedIn-profile country scrape.
    country_code = models.CharField(max_length=2, blank=True, default="")
    subscribe_newsletter = models.BooleanField(default=True)
    # Operator opt-in to give back to the central contacts store at all — the
    # whole contribution (emails and, when cached, the profile vector for the
    # agentic-email-marketing product; linkedin-docs
    # roadmap/p1-e3-agentic-email-marketing-product.md). Not asked at onboarding —
    # set from the operator's LinkedIn country at first daemon run by
    # ``apply_gdpr_contribution_override`` (keyed to ``is_eea_located``, not the
    # broad newsletter set): on outside the EEA/UK/CH, off inside it (or unknown
    # location). Off = no give-back at all (and so no give-to-get credits / no
    # resolve). Either way the raw profile text never leaves the operator's
    # machine — only the vector.
    contribute_to_hub = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    connect_daily_limit = models.PositiveIntegerField(default=20)
    follow_up_daily_limit = models.PositiveIntegerField(default=25)
    legal_accepted = models.BooleanField(default=False)
    cookie_data = models.JSONField(null=True, blank=True)
    newsletter_processed = models.BooleanField(default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exhausted: dict[str, date] = {}

    def can_execute(self, action_type: str) -> bool:
        """Check if the action is allowed under the daily rate limit."""
        # Reset exhaustion flag on a new day
        exhausted_date = self._exhausted.get(action_type)
        if exhausted_date is not None and exhausted_date != date.today():
            del self._exhausted[action_type]
        if action_type in self._exhausted:
            return False

        daily_field = _RATE_LIMIT_FIELDS[action_type]
        self.refresh_from_db(fields=[daily_field])

        daily_limit = getattr(self, daily_field)
        if daily_limit is not None and self._daily_count(action_type) >= daily_limit:
            return False

        return True

    def record_action(self, action_type: str, campaign: Campaign) -> None:
        """Persist a rate-limited action."""
        ActionLog.objects.create(
            linkedin_profile=self, campaign=campaign, action_type=action_type,
        )

    def mark_exhausted(self, action_type: str) -> None:
        """Mark the action type as externally exhausted for today."""
        self._exhausted[action_type] = date.today()
        logger.warning("Rate limit: %s externally exhausted for today", action_type)

    def _daily_count(self, action_type: str) -> int:
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return ActionLog.objects.filter(
            linkedin_profile=self, action_type=action_type,
            created_at__gte=today_start,
        ).count()

    def __str__(self):
        return f"{self.user.username} ({self.linkedin_username})"


class SearchKeyword(models.Model):
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="search_keywords",
    )
    keyword = models.CharField(max_length=500)
    used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("campaign", "keyword")]

    def __str__(self):
        return self.keyword


class ActionLog(models.Model):
    class ActionType(models.TextChoices):
        CONNECT = "connect", "Connect"
        FOLLOW_UP = "follow_up", "Follow Up"

    linkedin_profile = models.ForeignKey(
        LinkedInProfile,
        on_delete=models.CASCADE,
        related_name="action_logs",
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="action_logs",
    )
    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["linkedin_profile", "action_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action_type} by {self.linkedin_profile} at {self.created_at}"
