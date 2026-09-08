from django.http import HttpResponseForbidden


class LocalOnlyMiddleware:
    """This single-operator UI is intentionally reachable only on this machine."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.get_host()  # Enforce ALLOWED_HOSTS, including on read-only requests.
        if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
            return HttpResponseForbidden("Este painel está disponível apenas neste computador.")
        response = self.get_response(request)
        response["Cache-Control"] = "no-store"
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        response["Referrer-Policy"] = "same-origin"
        return response
