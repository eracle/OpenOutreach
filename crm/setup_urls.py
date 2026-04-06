# crm/setup_urls.py
from django.urls import path
from crm import setup_views

app_name = "setup"

urlpatterns = [
    path("", setup_views.setup_admin, name="setup_admin"),
]
