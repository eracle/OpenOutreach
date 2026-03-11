# Backwards-compatibility re-export
from linkedin.api.newsletter import (  # noqa: F401
    subscribe_to_newsletter,
    normalize_boolean,
    ensure_newsletter_subscription,
    BREVO_FORM_URL,
)
