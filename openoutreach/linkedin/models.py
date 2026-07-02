# openoutreach/linkedin/models.py
#
# The LinkedIn channel's models (LinkedInProfile / SearchKeyword / ActionLog)
# were removed in the email-first pivot. Operator identity now lives on the
# Django ``User`` + ``SiteConfig.country_code`` (see core/session.py,
# core/onboarding.py). The app is kept installed only because its migration
# history is load-bearing (core/migrations/0001 depends on it); the surviving
# ML/pipeline modules under this package are plain Python, not app models.
