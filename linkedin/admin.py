# linkedin/admin.py
from django.contrib import admin

from linkedin.models import ActionLog, Campaign, LinkedInProfile, SearchKeyword


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("department", "booking_link", "is_partner", "action_fraction")
    raw_id_fields = ("department",)


@admin.register(LinkedInProfile)
class LinkedInProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "linkedin_username", "active")
    list_filter = ("active",)
    raw_id_fields = ("user",)


@admin.register(SearchKeyword)
class SearchKeywordAdmin(admin.ModelAdmin):
    list_display = ("keyword", "campaign", "used", "used_at")
    list_filter = ("used", "campaign")
    raw_id_fields = ("campaign",)


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ("action_type", "linkedin_profile", "campaign", "created_at")
    list_filter = ("action_type", "campaign")
    raw_id_fields = ("linkedin_profile", "campaign")
    date_hierarchy = "created_at"
    readonly_fields = ("linkedin_profile", "campaign", "action_type", "created_at")
