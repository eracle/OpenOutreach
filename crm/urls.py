# crm/urls.py
from django.urls import path

from crm import views

app_name = "crm"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/chart-data/", views.dashboard_chart_data, name="chart_data"),
    path("api/daemon-status/", views.daemon_status_api, name="daemon_status_api"),

    path("leads/", views.LeadListView.as_view(), name="leads"),
    path("leads/<int:pk>/", views.LeadDetailView.as_view(), name="lead_detail"),
    path("leads/<int:pk>/toggle-disqualify/", views.lead_toggle_disqualify, name="lead_toggle_disqualify"),

    path("deals/", views.DealListView.as_view(), name="deals"),
    path("deals/<int:pk>/", views.DealDetailView.as_view(), name="deal_detail"),

    path("campaigns/", views.CampaignListView.as_view(), name="campaigns"),
    path("campaigns/new/", views.CampaignCreateView.as_view(), name="campaign_create"),
    path("campaigns/<int:pk>/", views.CampaignDetailView.as_view(), name="campaign_detail"),
    path("campaigns/<int:pk>/edit/", views.CampaignEditView.as_view(), name="campaign_edit"),

    path("accounts/", views.AccountsView.as_view(), name="accounts"),
    path("accounts/add/", views.ProfileCreateView.as_view(), name="profile_create"),
    path("accounts/<int:pk>/edit/", views.ProfileEditView.as_view(), name="profile_edit"),
    path("accounts/<int:profile_pk>/start/", views.daemon_start, name="daemon_start"),
    path("accounts/<int:profile_pk>/stop/", views.daemon_stop, name="daemon_stop"),

    path("tasks/", views.TaskListView.as_view(), name="tasks"),
    path("activity/", views.ActivityLogView.as_view(), name="activity"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
]
