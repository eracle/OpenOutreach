# linkedin/models.py
from django.contrib.auth.models import User
from django.db import models


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
    is_promo = models.BooleanField(default=False)
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
