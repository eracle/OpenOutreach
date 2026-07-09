# Privacy Notice — OpenOutreach Contacts Store

_This notice explains how the central contacts store operated at `hub.openoutreach.app` collects, uses, and discloses personal data. It is published for the people whose work contact details may appear in the store and for the operators who contribute to and read from it._

## Who operates the store

The store is operated by the maintainer of the open-source project **OpenOutreach**. It is a small database of **work email addresses** pooled across the OpenOutreach operator network: a contact one operator has already resolved can be served to another, lowering each operator's email-finder spend as coverage grows.

Throughout this notice, an **operator** is a person running a self-hosted OpenOutreach instance that contributes to and reads from the store.

## What data is in the store

The store is **minimised**. For each person it holds these core fields:

| Field | Example | Why it is kept |
| --- | --- | --- |
| Profile identifier | `https://…/in/jane-doe` | A stored profile URL — held as an opaque key, never fetched — used for contribution and resolution. |
| Country code | `in` | Drives the geographic exclusion below. |
| Work email address(es) | `jane@acme.com` | The contact detail the store exists to serve. |

The store also records, for internal operation, which operator token contributed a record (provenance) and the timestamps of first and last contribution.

**Profile vector.** Where an operator has opted in to contribute it (see the operator notice), the store may also hold a **384-dimension numeric profile vector** (an "embedding") for a person: a compact mathematical representation derived from their public professional profile, **computed on the operator's own machine** so the **raw profile text is never sent or stored**. The vector exists to support the similarity search described under *How data is used and disclosed* below.

**What is _not_ collected:** no name, headline, job title, company, phone number, postal address, or raw profile text.

Only **professional, business-context (B2B)** contact data is in scope. Consumer contact details and any special-category data are out of scope and are not collected.

## Geographic exclusion (who is _not_ in the store)

Any person located in the **EU/EEA, the UK, or Switzerland** — or whose location cannot be determined — is **never written to the store**. This exclusion runs authoritatively on the server, at the point data enters the store, regardless of what a contributing client sends.

## How data is collected

Data reaches the store from OpenOutreach operators at the one moment a real contact comes into existence: **after an operator's paid email-finder lookup returns a verified work email.**

The maintainer does **not** scrape any website or buy data to populate the store; it is filled only by operators' own paid-finder contributions, subject to the geographic exclusion above.

## How data is used and disclosed

- **Resolution (disclosure to operators).** An operator may query the store for a person's email before paying a finder service. A match is returned to that operator. This means **an email in the store may be disclosed to operators other than the one who contributed it**, so they can carry out business-to-business outreach. This is a disclosure of personal data to a third party — comparable to commercial B2B contact-data providers. The data is **not sold**.
- **Similarity search (profile vector).** Where a profile vector has been contributed, an operator may query the store for the stored professional contacts **most similar** to a given profile, and a set of matching records is returned to that operator so they can carry out their own business-to-business outreach. Like resolution, this is a **disclosure of professional contact data to operators**: it returns *which existing contacts resemble a query*, not any new score, rating, or prediction about a person, and it makes **no automated decision** producing legal or similarly significant effects. It operates only on the non-EU/EEA/UK/CH professional contacts described above. The store itself **does not send** any outreach — operators send from their own infrastructure and are responsible for that. You may object to this use and request suppression at any time (see *Your rights* below).
- **No consumer-facing purpose.** The store is not used for advertising to consumers or any consumer-facing purpose.

## Legal basis

Where data-protection law applies, the store relies on **legitimate interest** (Art. 6(1)(f) GDPR and equivalents) for both of its purposes — **resolution** (serving a known work email) and **similarity search** (returning the stored contacts most similar to a query). Both are the same activity in substance: **facilitating business-to-business professional communication using professional contact data**, by disclosing existing professional contacts to operators who carry out their own outreach. Neither purpose involves profiling for the store's own marketing, automated decision-making with legal or similarly significant effects, or the **sending** of marketing email by the store — operators send from their own infrastructure and are responsible for their own sends and the anti-spam law that governs them.

A legitimate-interest assessment balances that interest against the rights of the people in the store. The safeguards that keep the balance reasonable are: the **geographic exclusion** (people located in the EU/EEA, UK, or Switzerland — or whose location cannot be determined — are never written to the store, so they are never in the searchable set); strict **data minimisation** (a handful of work-contact fields plus a numeric vector — the **raw profile text is computed on the operator's machine and never transmitted or stored**); the **B2B-only, professional-context** scope, with no special-category and no consumer data; and the **objection and suppression** rights below, honoured across the whole store. Operators contributing or resolving data may be controllers or joint controllers and carry their own responsibilities.

## Your rights and how to exercise them

If your work email is in the store, you may request **access, correction, or erasure**, and you may **object** to the processing. To exercise any of these, or to be excluded from the store entirely:

- **Suppression / opt-out:** a request submitted to `POST /api/v2/suppress/` (or via the contact route below) removes the record and **blocks the email and public identifier from re-entering** the store. Suppression is honoured across the whole store, including against future re-contribution, and applies to both the resolution and the similarity-search purposes.

Suppression is recorded immediately as a request; the suppressed identifiers are excluded from the data served to operators, and the underlying records are erased on the store's maintenance cycle.

## Retention

Records persist while they remain useful for resolution and are refreshed when re-contributed. A suppressed record is removed from served results immediately and erased from source on the maintenance cycle.

## Contact

Questions, complaints, or data-subject requests: open an issue on the OpenOutreach repository or contact the maintainer at the address published there. If your country has a data-protection regulator (for example the AEPD in Spain, the ICO in the UK, or another EU/EEA supervisory authority), you may also lodge a complaint directly with that regulator.

---

_This notice is published in good faith and may be updated as the store evolves; material changes will be reflected here._
