from django.urls import path

from openoutreach.web import views
from openoutreach.web.jobs import job

urlpatterns = [
    path("", views.index),
    path("assets/<str:name>", views.asset),
    path("api/dashboard", views.dashboard),
    path("api/leads", views.leads),
    path("api/config/campaign", views.configuration, {"section": "campaign"}),
    path("api/config/integrations", views.configuration, {"section": "integrations"}),
    path("api/export", views.export),
    path("api/enrich/whatsapp", views.enrich_whatsapp),
    path("api/job", job),
]
