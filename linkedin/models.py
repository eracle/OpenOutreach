# linkedin/models.py
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

# action_type → (daily_limit_field, weekly_limit_field)
_LIMIT_FIELDS = {
    "connect": ("connect_daily_limit", "connect_weekly_limit"),
    "follow_up": ("follow_up_daily_limit", None),
}


class Campaign(models.Model):
    department = models.OneToOneField(
        "common.Department",
        on_delete=models.CASCADE,
        related_name="campaign",
    )
    product_docs = models.TextField(blank=True)
    campaign_objective = models.TextField(blank=True)
    followup_template = models.TextField(blank=True)
    booking_link = models.URLField(max_length=500, blank=True)
    is_partner = models.BooleanField(default=False)
    action_fraction = models.FloatField(default=0.0)

    def __str__(self):
        return self.department.name

    class Meta:
        app_label = "linkedin"


class LinkedInProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="linkedin_profile",
    )
    linkedin_username = models.CharField(max_length=200)
    linkedin_password = models.CharField(max_length=200)
    subscribe_newsletter = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    connect_daily_limit = models.PositiveIntegerField(default=20)
    connect_weekly_limit = models.PositiveIntegerField(default=100)
    follow_up_daily_limit = models.PositiveIntegerField(default=30)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exhausted: dict[str, date] = {}

    def can_execute(self, action_type: str) -> bool:
        """Check if the action is allowed under daily/weekly rate limits."""
        # Reset exhaustion flag on a new day
        exhausted_date = self._exhausted.get(action_type)
        if exhausted_date is not None and exhausted_date != date.today():
            del self._exhausted[action_type]
        if action_type in self._exhausted:
            return False

        daily_field, weekly_field = _LIMIT_FIELDS[action_type]

        self.refresh_from_db(fields=[daily_field] + ([weekly_field] if weekly_field else []))

        daily_limit = getattr(self, daily_field)
        if daily_limit is not None and self._daily_count(action_type) >= daily_limit:
            return False

        if weekly_field:
            weekly_limit = getattr(self, weekly_field)
            if weekly_limit is not None and self._weekly_count(action_type) >= weekly_limit:
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

    def _weekly_count(self, action_type: str) -> int:
        now = timezone.now()
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        return ActionLog.objects.filter(
            linkedin_profile=self, action_type=action_type,
            created_at__gte=monday,
        ).count()

    def __str__(self):
        return f"{self.user.username} ({self.linkedin_username})"

    class Meta:
        app_label = "linkedin"


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
        app_label = "linkedin"
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
        app_label = "linkedin"
        indexes = [
            models.Index(fields=["linkedin_profile", "action_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action_type} by {self.linkedin_profile} at {self.created_at}"
