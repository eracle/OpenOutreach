# LEGAL NOTICE – OpenOutreach

**Effective upon use of this software**

OpenOutreach is a self-hosted, open-source, **email-first** AI sales agent. It discovers B2B leads from a **licensed third-party data provider**, qualifies them on your own machine, resolves a work email for the best-fit leads through a **paid third-party email-finder**, and sends outreach email from **mailboxes you own and control**. It is **browserless: it does not use, log into, scrape, or automate any social network or professional-network account, and it stores no such credentials.** By running this software, you acknowledge and accept the following facts, risks, and terms.

> This notice describes how the software behaves and is **not legal advice**. You are responsible for your own compliance; where the stakes warrant it, consult a lawyer. Material aspects of the data model below are still pending a formal legal review.

### 1. No Platform Scraping or Automation
OpenOutreach performs **no** automated access to any social or professional network — no login, no browser session, no bot, no scraping, no messaging on such a platform. Lead **discovery** comes from a licensed data provider (currently BetterContact **Lead Finder**), and **enrichment** (resolving a work email) comes from a paid email-finder — both third-party services **you** sign up for and configure with **your own** API key, used under **that provider's** terms.

- **Profile URLs are identifiers, not fetch targets.** A discovered lead may carry a professional-network profile URL as an opaque identifier. OpenOutreach **stores it and never visits it** — it is a lookup/dedup key, nothing more.
- **You accept the third-party terms.** You are responsible for using the data provider, email-finder, and sending services in line with each provider's terms of service and acceptable-use policy.

### 2. Automatic Newsletter Subscription (Non-Opt-In Jurisdictions)
During onboarding you enter the **country** your operation is based in. If that country is **not** covered by opt-in email-marketing law (e.g. GDPR/ePrivacy, CASL, LGPD, Spam Act, etc.), the software **defaults** your `subscribe_newsletter` setting to enabled — the email you provide at onboarding may be added to the OpenOutreach mailing list.

- **Protected jurisdictions**: for operators based in the EU/EEA, UK, Switzerland, Canada, Brazil, Australia, Japan, South Korea, or New Zealand, the newsletter default is **off** and any explicit choice you make is honoured.
- **Unknown location**: if the country is left unset, the software treats you as protected (no auto-subscription).
- **Opting out**: change `subscribe_newsletter` during onboarding or later in the Django Admin (on `SiteConfig`).

### 3. No Warranty – Use at Your Own Risk
OpenOutreach is provided **AS IS**, without warranties of any kind (express or implied), including fitness for a particular purpose, non-infringement, or that it will not cause harm to your accounts, mailboxes, domains, or data.

The developer(s):
- Do not guarantee any results from using the tool
- Are not responsible for account/domain/mailbox suspensions, deliverability harm, lost business, legal consequences, or other damages
- Recommend you review the terms of every third-party service you connect (data provider, email-finder, mailbox/SMTP provider) before use

### 4. How the Project Is Funded (Affiliate + Optional Freemium Promotion)
OpenOutreach is free and open-source. It sustains itself in two ways, both disclosed here:

- **Affiliate links (primary).** The unavoidably-paid third-party services the tool relies on — the email-finder and, optionally, cold-email sending infrastructure — are surfaced during onboarding through **affiliate links**. If you sign up through one, the project may earn a commission **at no markup to you**. You are free to sign up any other way.
- **Freemium promotional campaign.** If you run a **freemium** campaign (a free campaign kit for cold-start), a fraction of the tool's activity (`action_fraction`) is devoted to a **maintainer-configured promotional campaign** run over **email from your own mailbox**, to recipients unrelated to your own targets — your own qualified leads are never affected. The campaign content and that fraction are **retrieved from a remote server** controlled by the maintainer and may change between versions or runs without notice. These sends appear as sent from **your** mailbox and are subject to the same anti-spam responsibilities as your own sends (Section 5). This mechanism can be disabled only by modifying the source code, which the licence permits. *(The former connection-request promotion and the one-time connect-to-the-author action have been removed with the move off the browser channel.)*

### 5. Email Enrichment and Cold Email Outreach
OpenOutreach resolves work email addresses for your qualified leads through a **third-party email-finder** (e.g. BetterContact) and sends outreach email from **sending infrastructure you own** (e.g. your own Gmail/Workspace/own-domain mailbox, or a cold-email provider such as IceMail). Both the finder and the sender are **paid third-party services you sign up for and configure yourself**. OpenOutreach **never sends email through its own servers on your behalf**: every message is sent from a mailbox **you** own and control, using **your** credentials.

- **Data protection**: resolving and storing a person's work email is processing of personal data. Where data-protection law applies (GDPR, UK GDPR, LGPD, etc.) **you are the data controller** and are responsible for a lawful basis, honouring access/erasure/objection requests, and any required disclosures. OpenOutreach provides the mechanism, not legal cover.
- **Anti-spam law**: unsolicited commercial email is regulated — CAN-SPAM (US), GDPR/ePrivacy (EU/EEA), CASL (Canada), the Spam Act (Australia), and others. Requirements commonly include truthful sender and subject lines, a valid physical postal address, and a working, honoured opt-out. **You are solely responsible** for ensuring every email you send complies with the laws applicable to you and to each recipient.
- **Deliverability and account risk**: cold email can get your domains and mailboxes throttled, blacklisted, or suspended. Sending from **secondary/lookalike domains** and warming mailboxes mitigates but does not eliminate this. The risk is yours.
- **Accuracy**: finder results may be wrong, stale, or belong to a different person. You are responsible for whom you contact and what you send.

### 6. Central Contacts Store (Contribution and Resolution)
OpenOutreach connects to an optional **central contacts store operated by the project maintainer** (`hub.openoutreach.app`). It pools work email addresses across the OpenOutreach network so a contact one operator has already paid to resolve can be served — for free — to another, lowering everyone's email-finder spend as coverage grows. By running the software with contribution enabled you participate as described here.

- **What is contributed, and when**: at the **one** moment a real contact comes into existence — **after a paid email-finder returns a verified work email** — OpenOutreach sends a minimal record: the person's **profile identifier** (the stored, never-fetched profile URL), their **country code**, and the **work email address(es)** resolved. No name, headline, company, title, phone, or profile text is sent. *(The store is now sourced only from paid finder results; the earlier contribution path that captured a 1st-degree connection's contact info has been removed with the browser channel.)* Where you have left **profile-vector contribution** enabled and a vector for the person is already cached locally, the record also carries a **384-dimension numeric profile vector** computed on your own machine — the raw profile text never leaves your machine.
- **The give-back is opt-in (`contribute_to_hub`)**, set at onboarding and editable in the Django Admin (on `SiteConfig`). Turned off, OpenOutreach contributes nothing — and, under the give-to-get model, earns no lookup credits and cannot resolve.
- **On by default, and forced where the law allows.** If your operation is **not** based in the EU/EEA, UK, or Switzerland, contribution is enabled and can be disabled only by modifying the source (permitted under the licence). If it **is** based there, you keep a genuine opt-out (unknown location defaults to protected). This mirrors the newsletter mechanism in Section 2.
- **Geo-gate on the people in the store**: independently of where *you* are, a contact located in the **EU/EEA, UK, or Switzerland — or whose location cannot be determined — is never written to the store.** This gate runs authoritatively **server-side**; the client's pre-filter is only a bandwidth optimisation.
- **Resolution is a disclosure to third parties.** OpenOutreach reads the store *first*, before spending a paid finder credit. A hit is served free. So an email you contribute **may be disclosed to other operators** to contact that person, and emails others contributed may be disclosed to you. This is a disclosure of personal data to a third party — in substance the commercial-contact-data model (Apollo, Cognism, Dropcontact). It is **not** a sale of data, but it **is** a separate processing purpose from your own outreach.
- **Similarity search (profile vector).** Contributed vectors additionally power a **similarity-search service**: an operator can ask the store for the stored contacts most similar to a given profile. This is a further **disclosure** purpose — it returns *which existing contacts resemble a query*, not a score or prediction about a person — over the store's non-EU/EEA/UK/CH contacts only. The maintainer relies on **legitimate interest**, honours the **objection right** and store-wide suppression, and **never sends on your behalf**.
- **Your role and responsibilities.** Where data-protection law applies, contributing and resolving personal data is processing for which you may be a controller or joint controller alongside the maintainer. **You remain responsible** for a lawful basis (the project relies on legitimate interest for B2B professional contact data only), for honouring access/erasure/objection requests, and for any required notices.
- **Suppression / opt-out.** Any person whose email is in the store can be removed and blocked from re-entry via the store's suppression mechanism (`POST /api/v2/suppress/`), honoured across the whole store. The store publishes a separate **Privacy Notice** for those people at <https://hub.openoutreach.app/privacy/>.

### 7. Your Responsibility
By downloading, installing, configuring, or running OpenOutreach, you:
- Confirm you are of legal age and have authority to accept these terms
- Agree to use the tool only in compliance with all applicable laws (data-protection/privacy law such as GDPR, anti-spam law such as CAN-SPAM/CASL) and with the terms of every third-party service you connect
- Accept full responsibility for the emails you send and the contacts you process
- Understand that modifying the code to disable the freemium promotional campaign or the forced hub contribution is permitted under the licence, but remains your responsibility

If you do **not** agree with any part of this notice — especially the freemium promotional campaign or the central contacts store — **do not use this software**. Delete it immediately.

Questions or concerns? Open an issue on the repository or contact the maintainer(s).

**Continued use constitutes acceptance of this Legal Notice.**
