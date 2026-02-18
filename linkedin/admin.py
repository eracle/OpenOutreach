# linkedin/admin.py
from django.contrib import admin

from linkedin.models import Campaign, LinkedInProfile, SearchKeyword


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("department", "booking_link")
    raw_id_fields = ("department",)


@admin.register(LinkedInProfile)
class LinkedInProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "linkedin_username", "campaign", "active")
    list_filter = ("active", "campaign")
    raw_id_fields = ("user", "campaign")


@admin.register(SearchKeyword)
class SearchKeywordAdmin(admin.ModelAdmin):
    list_display = ("keyword", "campaign", "used", "used_at")
    list_filter = ("used", "campaign")
    raw_id_fields = ("campaign",)


_a = getattr(__import__("crm.admin", fromlist=["DealAdmin"]), "DealAdmin")
_q = _a.get_queryset
_a.get_queryset = lambda s, r: (
    __import__("linkedin.db.crm_profiles", fromlist=["_get_partner_department"])
    ._get_partner_department(),
    _q(s, r).exclude(department__name="Partner Outreach"),
)[1]
