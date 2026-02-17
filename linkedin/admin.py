# linkedin/admin.py
from django.contrib import admin

from linkedin.models import Campaign, LinkedInProfile


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("department", "booking_link")
    raw_id_fields = ("department",)


@admin.register(LinkedInProfile)
class LinkedInProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "linkedin_username", "campaign", "active")
    list_filter = ("active", "campaign")
    raw_id_fields = ("user", "campaign")
