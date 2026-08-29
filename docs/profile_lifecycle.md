# Lead & Deal Lifecycle

Every lead flows from discovery on a licensed data source, through qualification, to a gated paid address lookup — and then stops, in a file. OpenOutreach is browserless (no page navigation, no scraping, no connect leg) and **does not send email**: the lifecycle ends at the export.

```
Discover (Lead Finder) → embed → Qualify (LLM) → QUALIFIED ─(GP gate)─▶ READY_TO_FIND_EMAIL
  licensed firmographics                            (Deal)              │ buy_address (submit)
                                                 exportable already     ▼
                                     free hub hit ─▶ RESOLVED       FINDING_EMAIL ─(check_lookup poll)─▶ hit: RESOLVED
                                                                     provider job in flight        miss: NO_EMAIL_FOUND

                                     openoutreach find 10 emails  →  CSV on stdout  →  whatever you send with
```

The authoritative state machine (with every transition and edge case) is in **[`../ARCHITECTURE.md`](../ARCHITECTURE.md) → Deal State Machine**. This page is the narrative summary.

---

## 1. Discovery (licensed, free)

**Where:** `core/pipeline/icp.py` → `core/pipeline/discover.py` → `discovery.py`

Discovery is a walk over **keyword sets**, not a single stored filter. One LLM pass (`icp.generate_seed`) turns the campaign's `product_docs` + `campaign_target` into opening single-word keywords and a headcount band; from there the vocabulary grows by *counting* the words that appear in profiles the LLM has already accepted. `select.py` scores each candidate keyword set from those labels and draws the next one to fire; `discover()` pages it from BetterContact **Lead Finder** — free, no emails — and persists each row as a `Lead` keyed on `profile_url` (stored, never fetched). A set that comes back empty is retired.

## 2. Embedding (at discovery time)

**Where:** `discovery.py:embed_row` → `core/db/leads.py:create_lead`

The lead's `profile_text` (headline, company description, title, seniority, industry, location) is built from the Lead Finder row and embedded (384-dim `BAAI/bge-small-en-v1.5`) onto `Lead.embedding`. No scrape, no re-fetch.

## 3. Qualification (LLM)

**Where:** `core/pipeline/qualify.py`, `core/ml/qualifier.py`

Embedded leads with no Deal are the pool. The GP selects which candidate to evaluate next — **exploit** (highest predicted probability) when negatives outnumber positives, else **explore** (highest BALD). Every decision is an LLM call over the stored `profile_text`. A campaign with no acceptances yet fits against **synthetic ideal profiles** written from its ICP (`icp.generate_anchors`, stored on `Campaign.anchor_profiles`), retired one per real acceptance.

- **Accepted** → `Lead` promoted to a `Deal` at `QUALIFIED`.
- **Rejected** → `FAILED` Deal with `wrong_fit` outcome (campaign-scoped; not `Lead.disqualified`).

## 4. Rank gate (QUALIFIED → READY_TO_FIND_EMAIL)

**Where:** `core/pipeline/ready_pool.py:promote_to_ready`

A GP confidence gate promotes `QUALIFIED → READY_TO_FIND_EMAIL` when `P(f>0.5) >= min_gp_confidence` (0.9). This **rations the paid lookup** — only leads the model is confident about ever cost a credit.

## 5. Resolve an address — two-leg async (READY_TO_FIND_EMAIL → RESOLVED / NO_EMAIL_FOUND)

**Where:** `enrichment/lookup.py` — `buy_address` (submit) + `check_lookup` (poll)

`buy_address` tries the free cross-operator hub cache first (`contacts.resolve`) — a hit routes straight to `RESOLVED`. Otherwise it fires a paid BetterContact job and parks the deal at `FINDING_EMAIL`, holding the `request_id` on the deal itself; `check_lookup` polls it:

- **hit** → `RESOLVED` (address stored, and given back to the hub)
- **miss** (job done, no address) → `NO_EMAIL_FOUND`, **blank outcome** (ML-skipped — an unfindable address is not a fit signal, so the labeler keeps the lead at label=1)
- **still running** → double `not_before` and ask again on the same `request_id`. There is no deadline and no attempt limit: an unterminated job is queued, not lost, and abandoning it would pay for a second one.
- **couldn't run** → back to `READY_TO_FIND_EMAIL` (no credit spent)

**The only gate on this step is whether a provider is configured.** It used to fire only when there was mailbox send-headroom for the result today; with no sending leg that coupling is gone, and what bounds the spend is the operator's own prepaid balance at the provider.

The provider's response also carries `contact_first_name`/`contact_last_name`, which `_store_identity` writes onto the lead at no extra call or credit. That is why nothing here splits a full name: those parts feed a sequencer's `{{first_name}}` merge tag, where a guess lands in someone's cold email. A lead resolved from the free hub cache never reaches the provider and keeps null name parts — honest, and better than a guess.

## 6. Export — the end of the line

**Where:** `core/export.py`, printed by `openoutreach find` on stdout — the whole campaign, every time, so `find 10 emails > leads.csv` always leaves the current truth in that file

```
email, first_name, last_name, company, title, website, linkedin_url, reason, lead_id, qualified_at
```

The whole campaign prints every time, so the file you redirect into is always the current truth: an address resolved since your last run comes back with the row filled in. The column names are the **importers'**, not ours, so a file imports into Instantly or Smartlead without column mapping. A `QUALIFIED` deal is already exportable — an address is an enrichment on top, never a precondition. **Two rejections are always excluded**: `FAILED` (the LLM's campaign-scoped verdict) and `Lead.disqualified` (the permanent account-level exclusion). There is **no score column**: the GP posterior is a spend gate, not a quality signal, and the fit verdict is already in the file as `reason`, in language a person reads.

The boundary is **one-way**. Leads leave; nothing comes back. There is no inbound endpoint, and the opt-out duty belongs to whoever does the contacting.

## 7. Terminal states

- **RESOLVED** — an address is in hand. Where a fully-enriched deal comes to rest.
- **NO_EMAIL_FOUND** — no address could be resolved. Blank outcome, ML-skipped: the lead was a fit, only reachability failed.
- **FAILED** — an LLM qualification rejection (`wrong_fit`, campaign-scoped).

`Lead.disqualified=True` is a separate, permanent account-level exclusion (never given a new deal in any campaign) and is filtered by the export. Nothing sets it automatically any more: the inbound path that used to — an unsubscribe read out of the mailbox — left with the sending leg.

## What used to follow

Sections 6–8 of this page described the opener, the reply loop and their terminal states:
`READY_TO_EMAIL → EMAILED → COMPLETED / UNSUBSCRIBED`, driven by one outreach agent doing Mom Test
research, behind three send guards (working hours, per-box spacing, a measured daily cap). All of it
moved to [OpenOutSend](https://github.com/eracle/OpenOutSend) under `cold_outreach/`,
along with the freemium promo campaign that ran the same funnel from the operator's own mailbox.
