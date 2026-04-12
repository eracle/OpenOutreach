# GDPR Implementation Roadmap — OpenOutreach

Implementation plan for the compliance gaps identified in [GDPR_COMPLIANCE_REPORT.md](GDPR_COMPLIANCE_REPORT.md).

---

## Planned Mitigation: Differential Privacy Embeddings

### Description

The application will transition to a **privacy-by-design** data model where:

1. Profile text is fetched from LinkedIn's Voyager API **transiently** for embedding and LLM qualification
2. A 384-dimensional embedding vector is computed (FastEmbed, BAAI/bge-small-en-v1.5)
3. **Calibrated noise is added** to the embedding before storage, providing differential privacy guarantees
4. The raw profile text and full profile JSON (`Lead.profile_data`) are **not persisted**
5. Only the noisy embedding, a qualification label, and minimal operational metadata are stored long-term

### What This Solves

- **Data minimization (Article 5(1)(c))** — No raw personal data stored; only irreversible noisy vectors
- **Storage limitation (Article 5(1)(e))** — Drastically reduces the volume and sensitivity of stored data
- **Right to erasure (Article 17)** — Noisy embeddings with sufficient noise are no longer personal data under Recital 26; erasure of the embedding row is straightforward regardless
- **Security (Article 32)** — Reduces breach impact since stored vectors cannot reconstruct profile text

### What This Does NOT Solve

The noisy embedding approach is necessary but not sufficient. The following gaps remain even after implementation:

- **Consent and lawful basis** — Still no lawful basis documented for the initial data collection from LinkedIn
- **Transparency** — Data subjects still not informed of collection at any point
- **Credential security** — LinkedIn passwords and cookies still stored in plaintext
- **Transient processing** — Profile text still sent to external LLM APIs for qualification
- **Operational PII** — Lead records still store names, the `public_identifier` is still linkable to a real person, and PII appears in logs/diagnostics
- **Automated decision-making** — GP scoring and LLM qualification still lack Article 22 safeguards

---

## Workstream A — Differential Privacy Embeddings

Replace raw profile storage with noisy embedding vectors. Addresses report findings 1.7, 2.2 (partially), and the data minimisation requirements of Article 5(1)(c).

### A.1 Stop Persisting Full Profile Data

- **`linkedin/db/leads.py`** — Modify `create_enriched_lead()` to NOT store the full profile dict in `Lead.profile_data`. Store only a processing timestamp or empty string.
- **`linkedin/db/leads.py`** — Modify `_update_lead_fields()` to populate only the minimum required fields (see A.2).
- Raw Voyager API JSON is no longer persisted (TheFile model removed).
- **`linkedin/db/enrichment.py`** — Update `ensure_lead_enriched()` to compute embedding immediately during enrichment, then discard the profile dict. The enrichment and embedding steps must be atomic.

### A.2 Minimize Lead Model Fields

The Lead model has the following PII fields. After the transition, only populate:

| Field | Keep? | Rationale |
|-------|-------|-----------|
| `first_name` | Yes — needed for connection request and follow-up message personalization |
| `last_name` | Yes — same as above |
| `linkedin_url` | Yes — LinkedIn URL is the primary key for deduplication |
| `company_name` | Yes — used for Deal creation validation |
| `description` | **No** — must not store full profile JSON |

Fields that are populated but not needed should be left blank. Fields needed for follow-up messaging (`first_name`, `last_name`) should have a documented retention period.

### A.3 Add Calibrated Noise to Embeddings

- **`linkedin/ml/embeddings.py`** — After computing the embedding via FastEmbed, add Gaussian noise before storage:
  ```python
  def add_differential_privacy_noise(embedding: np.ndarray, epsilon: float, sensitivity: float) -> np.ndarray:
      """Add calibrated Gaussian noise for (epsilon, delta)-differential privacy."""
      sigma = sensitivity * np.sqrt(2 * np.log(1.25 / delta)) / epsilon
      noise = np.random.normal(0, sigma, size=embedding.shape)
      return embedding + noise
  ```
- **Calibration**: The noise scale (`epsilon`) must be calibrated so that:
  - The GP model still has usable signal for qualification ranking (utility)
  - Individual profiles cannot be re-identified by nearest-neighbor search against a known embedding database (privacy)
  - Recommended starting point: `epsilon` between 1.0 and 10.0, with empirical evaluation on qualification accuracy
- **`crm/models/lead.py`** — Store a `noise_epsilon` field on `Lead` to track the privacy budget used, enabling future re-noising if standards tighten.

### A.4 Remove `llm_reason` from Persistent Storage

- **`crm/models/lead.py`** — The `Deal.reason` field stores free-text LLM reasoning about why a profile was qualified/disqualified. This text often contains personal data (e.g. "Senior engineer at Google with 10 years of ML experience in San Francisco"). Either:
  - Remove the field entirely and log reasons transiently, or
  - Anonymize the reason text before storage (strip names, companies, locations)

### A.5 Handle Profile Text for Follow-Up Messages

Follow-up messages (`renderer.py`) require profile context (name, headline, company). After removing `Lead.profile_data`:
- Pass the minimal Lead fields (`first_name`, `last_name`, `title`) directly to the template
- For richer context, fetch profile data transiently from LinkedIn at message-send time (already connected, so data is accessible) rather than from stored records

### A.6 Update `build_profile_text()` Pipeline

- **`linkedin/ml/profile_text.py`** — No changes needed (already operates on in-memory dict)
- **`linkedin/pipeline/qualify.py`** — `_fetch_profile_text()` must ensure the profile dict is used only in-memory and not persisted after embedding + LLM call complete

---

## Workstream B — Credential Security

Encrypt stored credentials and session tokens at rest. Addresses report findings 1.1 and 2.3 (Article 32).

1. Create `linkedin/crypto.py` — Fernet encryption with key derived from Django `SECRET_KEY` via PBKDF2
2. Encrypt `linkedin_password` in DB — store Fernet token in the existing `CharField`; add `password` property that decrypts on read; write a data migration to encrypt existing plaintext values
3. Encrypt cookie files — wrap Playwright `storage_state()` save/load to encrypt the JSON blob; set file permissions to 0600
4. Update `AccountSession.__init__` and `_maybe_refresh_cookies()` to decrypt on read
5. Update `start_browser_session()` to decrypt cookie file before passing to Playwright and re-encrypt after save

---

## Workstream C — Operational PII Cleanup

Reduce PII exposure in logs, diagnostics, and Lead records. Addresses report findings 1.7, 2.1, and 2.2 (Articles 5(1)(c), 5(1)(e), 32).

1. Stop populating `company_name`, `email`, `phone`, `city_name` in `_update_lead_fields()`
2. Redact PII in logs — replace email addresses and full names with hashed/truncated identifiers in:
   - `linkedin/api/newsletter.py:47,51,61,97`
   - `linkedin/onboarding.py:214`
   - `linkedin/actions/search.py:97,102,105`
3. Auto-purge diagnostic dumps older than 7 days — add `purge_old_diagnostics()` to `linkedin/diagnostics.py`, call it at daemon startup
4. Restrict diagnostics directory permissions to 0700 on creation

---

## Workstream D — Data Retention Enforcement

Enforce maximum retention periods to satisfy Article 5(1)(e) storage limitation. Addresses report finding 1.3. EU DPA guidance for B2B direct marketing generally accepts 6 months as the ceiling.

1. Implement `purge_expired_data()` in a new `linkedin/management/commands/purge_expired_data.py`, callable as `python manage.py purge_expired_data`
2. Call `purge_expired_data()` at daemon startup (in `daemon.py`, alongside `heal_tasks()`)
3. Retention schedule:

| Data | TTL | Purge logic |
|------|-----|-------------|
| Diagnostic dumps | **7 days** | Delete old diagnostic folders by timestamp prefix |
| Completed/failed tasks | **6 months** | `Task.objects.filter(status__in=[COMPLETED, FAILED], completed_at__lt=cutoff).delete()` |
| Action logs | **6 months** | `ActionLog.objects.filter(created_at__lt=cutoff).delete()` |
| Disqualified leads | **6 months** | Delete Lead (with embedded data) for `disqualified=True` older than cutoff |
| Completed deals (COMPLETED/FAILED state) | **6 months** | Delete Deal and Lead (with embedded data) for finished outreach sequences |
| Stale embedded leads (no Deal, old) | **6 months** | `Lead.objects.filter(embedding__isnull=False, deals__isnull=True, creation_date__lt=cutoff).delete()` |

---

## Workstream E — Right to Erasure (Per-Profile Deletion)

Implement individual data subject erasure to satisfy Article 17. Addresses report finding 1.4.

1. Create `linkedin/management/commands/delete_profile.py` — accepts `public_id` as argument
2. Cascade deletion across all tables holding data for that profile:
   - `Lead` (lookup via `linkedin_url=public_id_to_url(public_id)`) — includes embedded data stored on the Lead
   - `Deal` (lookup via `lead__linkedin_url=public_id_to_url(public_id)`)
   - `Task` entries with `public_id` in `payload` JSON
   - `ActionLog` entries are not directly linked to a profile public_id — no action needed (they reference `LinkedInProfile`, i.e. the operator's account, not the target)
3. Delete any diagnostic folders whose saved HTML contains the `public_id`
4. Log the erasure event without PII: `logger.info("Erasure completed for 1 profile (request_id=%s)", uuid)`
5. Also expose as a Django Admin action on the Lead model for convenience

---

## Workstream F — Remove Stored Names from Lead Records

Eliminate the last remaining third-party personal data from the database by removing `first_name` and `last_name` from persistent storage. Addresses report finding 1.7 and completes the data minimisation goal of Article 5(1)(c). After this workstream, no third-party personal data remains in the DB — only pseudonymous identifiers and noisy embeddings.

Currently these fields are used in:
- **Follow-up message personalization** (`renderer.py`) — needs name to generate a message
- **Deal creation** (`promote_lead_to_deal` in `db/leads.py`) — requires `company_name` for validation

These can be replaced by **transient fetching from LinkedIn at the point of use**:

1. Stop populating `first_name`, `last_name` in `_update_lead_fields()` — only store `linkedin_url` (for dedup)
2. Update `renderer.py` / follow-up message flow to fetch profile name transiently from LinkedIn at send time via Voyager API. At this point the user is already connected with the target, so the data is accessible. The fetched data is used in-memory for template rendering and discarded.
3. Update Django Admin display — show `public_identifier` (derived from `Lead.linkedin_url`) instead of name columns where Lead names were shown
4. Write a data migration to clear `first_name`, `last_name` on existing Lead records

After this workstream, the only identifiers in the DB are:
- `Lead.linkedin_url` — LinkedIn URL (pseudonymous, publicly accessible)

These are pseudonymous under GDPR Recital 26 but still personal data (trivially re-identifiable). They are the minimum required for dedup and operational navigation.

---

## Workstream G — Data Portability (Per-Profile Export)

Implement data subject export to satisfy Article 20 (Right to Data Portability). Addresses report finding 3.6.

1. Create `linkedin/management/commands/export_profile.py` — accepts `public_id` as argument, outputs a JSON file
2. Collect all data held for that profile:
   - `Lead` fields (linkedin_url, disqualified status, creation date)
   - `Deal` fields (state, outcome, reason, creation/update dates)
   - `Task` entries referencing the profile (task_type, status, scheduled_at, created_at)
3. Output as structured JSON to stdout or a file, with a schema that could be provided to the data subject
4. Also expose as a Django Admin action on the Lead model
