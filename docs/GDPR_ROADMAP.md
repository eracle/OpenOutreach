# GDPR Roadmap — OpenOutreach (SUPERSEDED)

> **⚠️ Superseded by the off-platform pivot.** This roadmap planned compliance mitigations for the
> **retired** architecture — the one that logged into a professional network with a real account,
> drove a browser (Playwright/Voyager), scraped profiles, and stored account credentials and session
> cookies. That entire surface has been **removed**: OpenOutreach is now **browserless** and does no
> scraping, holds **no platform account or credentials**, and sources leads from a **licensed data
> provider** (BetterContact Lead Finder) plus a **paid email-finder**. Most of the workstreams below
> therefore no longer apply. The file is kept as a historical record; the **live** compliance posture
> is `PRIVACY_NOTICE.md`, `LEGAL_NOTICE.md`, and the project's Legitimate Interest Assessment. A formal
> lawyer review of the current data model is still outstanding.

## What changed, and how it maps to the old plan

| Old workstream | Status under the current architecture |
| --- | --- |
| **A. Differential-privacy embeddings** (add noise to scraped-profile vectors) | **Moot as framed.** Vectors are now built from the **licensed Lead Finder payload**, not a scrape. The raw firmographic text is processed transiently for embedding + LLM qualification and the store still holds only the numeric vector — but the "irreversible noise on scraped data" motivation is gone. Re-open only if embeddings are ever centralised in raw form. |
| **B. Credential security** (encrypt stored passwords/cookies) | **Removed.** No account password or session cookie is stored anywhere — there is no login. The finding no longer exists. |
| **C. Operational PII cleanup** (redact scraped PII in logs/diagnostics) | **Mostly moot.** No browser/Voyager diagnostics or scraped-profile dumps exist. Ordinary log hygiene for lead names/emails still applies. |
| **D. Data retention enforcement** | **Still relevant**, re-keyed onto the current models (`Lead` keyed on `profile_url`, `Deal`, `Task`, `ChatMessage`). A retention/purge job remains a genuine open item. |
| **E. Right to erasure** / **G. Data portability** | **Still relevant.** Per-person erasure/export should key on the stored `profile_url` and cascade `Lead`/`Deal`/`Task`/`ChatMessage`. |
| **F. Remove stored names** | **Largely satisfied by design.** Discovery persists `profile_url`, `country_code`, `email`, and `profile_text`; there is no transient re-fetch from any platform. Minimisation of any residual name/company fields is still worth a pass. |

## Current posture (authoritative)

- **Discovery** is from a licensed provider under that provider's terms; profile URLs are stored as opaque identifiers and **never fetched**.
- **Enrichment** resolves a work email via a paid finder; the operator is the **controller** for the leads they process (lawful basis, access/erasure/objection, notices).
- **Central contacts store** (`hub.openoutreach.app`) pools **only** paid-finder-resolved emails (no scraping), under a **legitimate-interest** basis with a server-side EU/EEA/UK/CH geo-exclusion and a store-wide suppression/opt-out. See `PRIVACY_NOTICE.md` and the Legitimate Interest Assessment.
- **Outreach** is sent from mailboxes the operator owns; anti-spam duties are the operator's.

Open compliance items that survive the pivot: **retention/purge (D)**, **per-person erasure + export (E/G)**, and the outstanding **lawyer review** of the contacts-store disclosure model.
