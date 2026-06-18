# Privacy Notice — OpenOutreach Contacts Store

_This notice explains how the central contacts store operated at `hub.openoutreach.app` collects, uses, and discloses personal data. It is published for the people whose work contact details may appear in the store and for the operators who contribute to and read from it._

## Who operates the store

The store is operated by the maintainer of the open-source project **OpenOutreach**. It is a small database of **work email addresses** pooled across the OpenOutreach operator network: a contact one operator has already resolved can be served to another, lowering each operator's email-finder spend as coverage grows.

Throughout this notice, an **operator** is a person running a self-hosted OpenOutreach instance that contributes to and reads from the store.

## What data is in the store

The store is **minimised**. For each person it holds these core fields:

| Field | Example | Why it is kept |
| --- | --- | --- |
| LinkedIn public identifier | `jane-doe` | The lookup key for contribution and resolution. |
| Country code | `in` | Drives the geographic exclusion below. |
| Work email address(es) | `jane@acme.com` | The contact detail the store exists to serve. |

The store also records, for internal operation, which operator token contributed a record (provenance) and the timestamps of first and last contribution.

**Profile vector.** Where an operator has opted in to contribute it (see the operator notice), the store may also hold a **384-dimension numeric profile vector** (an "embedding") for a person: a compact mathematical representation derived from their public professional profile, **computed on the operator's own machine** so the **raw profile text is never sent or stored**. The vector exists to support the profiling described under *How data is used and disclosed* below. <!-- TODO(lawyer): this is new processing introduced by the agentic-email-marketing product (roadmap p1-e3); the legitimate-interest assessment and this notice must be reviewed before it is relied on. -->

**What is _not_ collected:** no name, headline, job title, company, phone number, postal address, or raw LinkedIn profile text.

Only **professional, business-context (B2B)** contact data is in scope. Consumer contact details and any special-category data are out of scope and are not collected.

## Geographic exclusion (who is _not_ in the store)

Any person located in the **EU/EEA, the UK, or Switzerland** — or whose location cannot be determined — is **never written to the store**. This exclusion runs authoritatively on the server, at the point data enters the store, regardless of what a contributing client sends.

## How data is collected

Data reaches the store from OpenOutreach operators at the two moments a real contact comes into existence:

1. after an operator's paid email-finder lookup returns a work email, and
2. after an operator's 1st-degree LinkedIn connection exposes their contact info.

The maintainer does **not** scrape LinkedIn or buy data to populate the store; it is filled only by operators' own contributions, subject to the geographic exclusion above.

## How data is used and disclosed

- **Resolution (disclosure to operators).** An operator may query the store for a person's email before paying a finder service. A match is returned to that operator. This means **an email in the store may be disclosed to operators other than the one who contributed it**, so they can carry out business-to-business outreach. This is a disclosure of personal data to a third party — comparable to commercial B2B contact-data providers. The data is **not sold**.
- **Profiling for targeting (profile vector).** Where a profile vector has been contributed, it is used to learn which professional-profile characteristics correlate with response to a given product, and to target business-to-business outreach accordingly. This is **profiling** within the meaning of Art. 4(4) GDPR, operating only on the non-EU/EEA/UK/CH professional contacts described above. <!-- TODO(lawyer): rewrite with the profiling balancing test; confirm the Art. 21(2) direct-marketing objection right and the per-country sending-regime gates (ePrivacy/CASL/CAN-SPAM/DPDP) before this purpose goes live. -->
- **No consumer-facing purpose.** The store is not used for advertising to consumers or any consumer-facing purpose.

## Legal basis

Where data-protection law applies, the basis for processing is **legitimate interest** (Art. 6(1)(f) GDPR and equivalents) — facilitating business-to-business professional communication using professional contact data. A legitimate-interest assessment balances that interest against the rights of the people in the store; the geographic exclusion, the strict data minimisation, the B2B-only scope, and the suppression mechanism below are the safeguards that keep that balance reasonable. Operators contributing or resolving data may be controllers or joint controllers and carry their own responsibilities.

<!-- TODO(lawyer): this assessment was written for the resolution purpose only ("no profiling") and cannot be reused for the profile-vector/profiling use added above. Before that use is relied on, the LIA must be rewritten with profiling in the balancing test, and the resolution basis and the marketing/profiling basis may need to be stated separately (see roadmap p1-e3, lawyer-consult list). -->

## Your rights and how to exercise them

If your work email is in the store, you may request **access, correction, or erasure**, and you may **object** to the processing. To exercise any of these, or to be excluded from the store entirely:

- **Suppression / opt-out:** a request submitted to `POST /api/suppress/` (or via the contact route below) removes the record and **blocks the email and public identifier from re-entering** the store. Suppression is honoured across the whole store, including against future re-contribution.

Suppression is recorded immediately as a request; the suppressed identifiers are excluded from the data served to operators, and the underlying records are erased on the store's maintenance cycle.

## Retention

Records persist while they remain useful for resolution and are refreshed when re-contributed. A suppressed record is removed from served results immediately and erased from source on the maintenance cycle.

## Contact

Questions, complaints, or data-subject requests: open an issue on the OpenOutreach repository or contact the maintainer at the address published there. If your country has a data-protection regulator (for example the AEPD in Spain, the ICO in the UK, or another EU/EEA supervisory authority), you may also lodge a complaint directly with that regulator.

---

_This notice is published in good faith and may be updated as the store evolves; material changes will be reflected here._
