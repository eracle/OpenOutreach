# crm/setup_views.py
"""One-time setup views for first launch."""
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.shortcuts import redirect, render


def setup_admin(request):
    """One-time admin account creation. Redirects to CRM if already set up."""
    if User.objects.filter(is_superuser=True).exists():
        return redirect("/crm/")

    errors = []

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        password2 = request.POST.get("password2", "").strip()

        if not username:
            errors.append("Username is required.")
        if not password:
            errors.append("Password is required.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != password2:
            errors.append("Passwords do not match.")
        if username and User.objects.filter(username=username).exists():
            errors.append(f"Username '{username}' is already taken.")

        if not errors:
            user = User.objects.create_superuser(
                username=username,
                password=password,
            )
            login(request, user)
            return redirect("/crm/")

    return render(request, "crm/setup.html", {"errors": errors})
