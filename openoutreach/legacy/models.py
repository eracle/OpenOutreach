# openoutreach/legacy/models.py
#
# Intentionally model-less. This app was the original home of the engine models
# (SiteConfig / Campaign / Task, since moved to `core`) and the retired channel
# models (LinkedInProfile / SearchKeyword / ActionLog, deleted in migration
# 0012 — their behavior now lives on the Django `User`, `SiteConfig`, `Mailbox`,
# and `ChatMessage`). It is kept installed solely to anchor migration history
# that `core`/`crm` depend on, so existing installs stay on a forward-only,
# backward-compatible migration graph.
