# LEGAL NOTICE – OpenOutreach

**Effective upon use of this software**

OpenOutreach is an open-source tool that automates actions on LinkedIn using **your** personal LinkedIn account credentials and browser session. By running this software, you acknowledge and accept the following important facts, risks, and terms.

### 1. LinkedIn Automation Is Prohibited by LinkedIn
LinkedIn's User Agreement (Section 8.2 and related Help articles) **explicitly prohibits**:
- Using bots, scripts, software, browser extensions, or any other automated means to access the service, send messages, add connections, visit profiles, or perform other actions.
- Developing, supporting, or using unauthorized automated methods for any activity on LinkedIn.

Running OpenOutreach (or any similar tool) **violates LinkedIn's Terms of Service**. This can result in:
- Temporary or permanent restriction/suspension/ban of your LinkedIn account
- Loss of connections, messages, leads, or professional network
- In extreme cases, legal action from LinkedIn against users or tool providers

LinkedIn actively detects and penalizes automation. **There is no "safe" level of automation** — even low-volume use carries risk.

### 2. Automatic Newsletter Subscription (Non-GDPR Jurisdictions)
On first run, OpenOutreach detects the country associated with your LinkedIn account. If your account is located in a jurisdiction **not** covered by opt-in email marketing laws (e.g. GDPR, CASL, LGPD, Spam Act, etc.), the software **automatically enables** subscription to the OpenOutreach newsletter on your behalf.

- **What happens**: Your `subscribe_newsletter` setting is set to `True` without explicit opt-in. This means the email address associated with your LinkedIn account may be added to the OpenOutreach mailing list.
- **Protected jurisdictions**: Accounts in the EU/EEA, UK, Switzerland, Canada, Brazil, Australia, Japan, South Korea, and New Zealand are **not** auto-subscribed (existing preference is preserved).
- **Unknown location**: If your country cannot be determined, the software defaults to treating you as protected (no auto-subscription).
- **Opting out**: You can disable this at any time by setting `subscribe_newsletter` to `False` in the Django Admin under your LinkedInProfile, or during the initial onboarding prompt.

### 3. No Warranty – Use at Your Own Risk
OpenOutreach is provided **AS IS**, without any warranties of any kind (express or implied), including but not limited to fitness for a particular purpose, non-infringement, or that it will not cause harm to your accounts or data.

The developer(s):
- Do not guarantee any results from using the tool
- Are not responsible for any account bans, lost business, legal consequences, or other damages
- Recommend you **review LinkedIn's current User Agreement** and automation policies yourself before use

### 4. Freemium Promotional Actions
OpenOutreach is free to use under a **freemium model**. In exchange for free access, the software periodically performs promotional actions using **your** LinkedIn account. These actions are the primary mechanism that funds and sustains the project. Paid plans that reduce or remove them may be offered in the future.

- **What happens**: Alongside your own campaign, the tool automatically selects LinkedIn profiles (unrelated to your campaign targets — your own qualified leads are never affected) and sends connection requests. On acceptance it runs the same agentic conversation it runs for your own leads; where the profile turns out to be a fit, that conversation leads to a tailored message about OpenOutreach. This volume is a fraction of your normal connect activity.
- **Connecting you to the author**: Once, early in your use of the tool, it also sends a single connection request to the project's author (linkedin.com/in/eracle) from your account. This is a one-time action — it never repeats, and the author is excluded from any follow-up messaging.
- **Remote configuration**: The fraction of activity these actions consume, the model used to rank targets, and the campaign content (product description, objective, booking link, and the set of seed profiles) are **retrieved from a remote server** at startup. These values are controlled by the project maintainer and may change between versions or runs without notice.
- **Impact on you**: These connection requests and messages appear as sent from **your** account.
- **Opting out**: These actions cannot be disabled without modifying the source code yourself, which is permitted under the licence.

### 5. Email Enrichment and Cold Email Outreach
OpenOutreach can resolve work email addresses for your qualified leads through a **third-party email-finder service** (e.g. BetterContact) and send outreach email from **sending infrastructure you own** (e.g. IceMail mailboxes). Both the finder and the sender are **paid third-party services you sign up for and configure yourself** — OpenOutreach may earn an affiliate commission when you sign up through a link it surfaces. OpenOutreach never sends email through its own servers on your behalf: every message is sent from a mailbox **you** own and control, using **your** credentials.

- **Data protection**: Resolving and storing a person's work email is processing of personal data. In jurisdictions with data-protection law (GDPR, UK GDPR, LGPD, etc.) **you are the data controller** and are responsible for having a lawful basis, honoring access/erasure/objection requests, and any required disclosures. OpenOutreach provides the mechanism, not legal cover.
- **Anti-spam law**: Sending unsolicited commercial email is regulated — CAN-SPAM (US), GDPR/ePrivacy (EU/EEA), CASL (Canada), the Spam Act (Australia), and others. Requirements commonly include truthful sender and subject lines, a valid physical postal address, and a working, honored opt-out/unsubscribe mechanism. **You are solely responsible** for ensuring every email you send through this tool complies with the laws applicable to you and to each recipient.
- **Deliverability and account risk**: Cold email can get your domains and mailboxes throttled, blacklisted, or suspended by your provider. Sending from **secondary/lookalike domains** and warming mailboxes (as the recommended providers do) mitigates but does not eliminate this. The risk is yours.
- **Accuracy**: Finder results may be wrong, stale, or belong to a different person. You are responsible for whom you contact and what you send.

### 6. Central Contacts Store (Contribution and Resolution)
OpenOutreach connects to a **central contacts store operated by the project maintainer** (`hub.openoutreach.app`). This store pools work email addresses across the OpenOutreach user network so that a contact one operator has already resolved can be served — for free — to another, lowering everyone's email-finder spend as coverage grows. By running this software you participate in that store as described here.

- **What is contributed**: At the two moments a real contact comes into existence — after a paid email-finder hit, and after a 1st-degree LinkedIn connection's contact info is scraped — OpenOutreach sends a minimal record to the store: the person's **LinkedIn public identifier**, their **country code**, and the **work email address(es)** resolved for them. No name, headline, company, title, phone number, or raw profile text is sent. Where you have left **profile-vector contribution** enabled (below) and a vector for the person is already cached locally, the record also carries a **384-dimension numeric profile vector** computed on your own machine — the raw profile text itself never leaves your machine. By leaving profile-vector contribution enabled you declare the **similarity-search purpose** described below for the vectors you contribute; that purpose is part of what the contribution covers.
- **The whole give-back is opt-in (`contribute_to_hub`).** Contributing to the store at all — emails and, when present, the profile vector — is governed by a single switch, asked at onboarding and editable in the Django Admin under your LinkedInProfile. Turned off, OpenOutreach contributes nothing (and, under the give-to-get model, earns no lookup credits and cannot resolve). See the forcing rule next.
- **Contribution is on by default — and forced where the law allows it.** OpenOutreach detects the country associated with **your own** LinkedIn account. If your account is **not** located in the EU/EEA, UK, or Switzerland, contribution is enabled and **cannot be disabled** without modifying the source code (permitted under the licence). If your account **is** located in the EU/EEA, UK, or Switzerland, you keep a genuine opt-out (your existing preference is preserved; unknown location defaults to protected). This mirrors the newsletter mechanism in Section 2, using a narrower jurisdiction set.
- **Geo-gate on the people you contribute**: Independently of where *you* are, a contact located in the **EU/EEA, UK, or Switzerland — or whose location cannot be determined — is never written to the store.** This gate runs authoritatively **server-side** at the store boundary; the client's pre-filter is only a bandwidth optimisation and is not trusted.
- **Resolution is a disclosure to third parties.** OpenOutreach also reads the store *first*, before spending a paid finder credit: it asks whether the store already holds an email for a given person. A hit is served to you for free. This means **an email you contribute may be disclosed to other operators** so they can contact that person, and **emails other operators contributed may be disclosed to you**. This is a disclosure of personal data to a third party — in substance the same model as commercial contact-data providers (Apollo, Cognism, Dropcontact). It is **not** a sale of data, but it **is** a separate processing purpose from your own outreach.
- **Similarity search (profile vector).** Contributed profile vectors additionally power a **similarity-search service**: an operator can ask the store for the stored professional contacts most similar to a given profile and receive matching records to pursue their own B2B outreach. This is a further **disclosure** purpose beyond email resolution — it returns *which existing contacts resemble a query*, not a score or prediction about a person — operating only on the store's non-EU/EEA/UK/CH professional contacts. The maintainer relies on **legitimate interest**, honours the **objection right** and store-wide suppression against it, and (as with resolution) **never sends on your behalf**: every outreach email is sent by you, from your own infrastructure, and your anti-spam obligations in Section 5 apply. The data-subject **Privacy Notice** (below) sets out the full legitimate-interest assessment.
- **Your role and responsibilities.** Where data-protection law applies (GDPR, UK GDPR, LGPD, India's DPDP Act, etc.), contributing and resolving personal data is processing for which you may be a controller or joint controller alongside the project maintainer. **You remain responsible** for having a lawful basis (the project relies on legitimate interest for B2B professional contact data only), for honouring access/erasure/objection requests, and for any required notices to the people whose data you process.
- **Suppression / opt-out.** Any person whose email is in the store can be removed and blocked from re-entry via the store's suppression mechanism (`POST /api/v2/suppress/` on the hub), honoured across the whole store. The store publishes a separate **Privacy Notice** addressed to those people (the data subjects) at <https://hub.openoutreach.app/privacy/>; you do not need to act on it as an operator — it documents the compliance backing for the contribution described above.

### 7. Your Responsibility
By downloading, installing, configuring, or running OpenOutreach, you:
- Confirm you are of legal age and have authority to accept these terms
- Agree to use the tool only in compliance with all applicable laws (including data protection/privacy laws like GDPR and anti-spam laws like CAN-SPAM/CASL where relevant)
- Accept full responsibility for any consequences of automation on your LinkedIn account(s)
- Understand that modifying the code to remove/disable freemium promotional actions is permitted under the licence, but doing so remains your responsibility

If you do **not** agree with any part of this notice — especially the freemium promotional actions or the violation of LinkedIn's terms — **do not use this software**. Delete it immediately.

Questions or concerns? Open an issue on the repository or contact the maintainer(s).

**Continued use constitutes acceptance of this Legal Notice.**
