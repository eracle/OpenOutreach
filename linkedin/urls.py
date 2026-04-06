# linkedin/urls.py
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.urls import include, path

urlpatterns = [
    path("", lambda request: redirect("/crm/"), name="root"),
    path("admin/", admin.site.urls),
    path("crm/", include("crm.urls")),
    path("setup/", include("crm.setup_urls")),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="crm/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page="/crm/"), name="logout"),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
