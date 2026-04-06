# crm/middleware.py
"""Middleware to redirect to first-time setup if no superuser exists."""
from django.contrib.auth.models import User
from django.shortcuts import redirect


class FirstTimeSetupMiddleware:
    """Redirect all requests to /setup/ if no superuser exists yet."""

    # Paths that should NOT be redirected (to avoid infinite loops)
    EXEMPT_PREFIXES = ("/setup/", "/static/", "/admin/")

    def __init__(self, get_response):
        self.get_response = get_response
        self._setup_done = None

    def __call__(self, request):
        # Cache the check so we don't hit DB on every request after setup
        if self._setup_done is None:
            self._setup_done = User.objects.filter(is_superuser=True).exists()

        if not self._setup_done:
            # Allow setup and static URLs through
            if not any(request.path.startswith(p) for p in self.EXEMPT_PREFIXES):
                return redirect("/setup/")
            # Re-check after each request during setup (in case user just created)
            response = self.get_response(request)
            self._setup_done = User.objects.filter(is_superuser=True).exists()
            return response

        return self.get_response(request)
