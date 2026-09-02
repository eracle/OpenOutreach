# LEGAL NOTICE – OpenOutreach

**Effective upon use of this software**

OpenOutreach is a self-hosted, open-source **lead finder and sender**. It discovers B2B leads from a **licensed third-party data provider**, qualifies them against your described ICP on your own machine, optionally resolves a work email for the best-fit leads through a **paid third-party email-finder**, and then either hands the result to you as a file or writes an opener and sends it **from a mailbox you supply and control**. It is **browserless: it does not use, log into, scrape, or automate any social network or professional-network account, and it stores no such credentials.** By running this software, you acknowledge and accept the following facts, risks, and terms.

> **It sends email from your mailbox, and that is a liability you take on.** The mail leaves your address, under your identity, to people you found with this tool. The software supplies an opt-out line, a `List-Unsubscribe` header and a suppression list; **you** remain responsible for anti-spam compliance — see Section 5. Stopping at the exported file, and sending with a tool of your own, is fully supported and is the only way to avoid taking this on.

> **A promotional campaign of the maintainer's own once rode along in your sending rotation. It is deleted** and nothing replaced it — see Section 4.

> This notice describes how the software behaves and is **not legal advice**. You are responsible for your own compliance; where the stakes warrant it, consult a lawyer. Material aspects of the data model below are still pending a formal legal review.

### 1. No Platform Scraping or Automation
OpenOutreach performs **no** automated access to any social or professional network — no login, no browser session, no bot, no scraping, no messaging on such a platform. Lead **discovery** comes from a licensed data provider (currently BetterContact **Lead Finder**), and **enrichment** (resolving a work email) comes from a paid email-finder — both third-party services **you** sign up for and configure with **your own** API key, used under **that provider's** terms.

- **Profile URLs are identifiers, not fetch targets.** A discovered lead may carry a professional-network profile URL as an opaque identifier. OpenOutreach **stores it and never visits it** — it is a lookup/dedup key, nothing more.
- **You accept the third-party terms.** You are responsible for using the data provider and email-finder in line with each provider's terms of service and acceptable-use policy.

### 2. Newsletter Subscription (Asked at Onboarding, Default Set by Jurisdiction)
During onboarding you enter the **country** your operation is based in, and you are then **asked** whether to subscribe the email address you provided to the OpenOutreach newsletter. The question is always asked; only its **default answer** depends on your jurisdiction.

- **Protected jurisdictions**: for operators based in the EU/EEA, UK, Switzerland, Canada, Brazil, Australia, Japan, South Korea, or New Zealand, the default is **no**. An explicit yes is lawful consent anywhere.
- **Elsewhere**: the default is **yes** — so accepting the prompt without changing it subscribes you.
- **Unknown location**: if the country cannot be read, the software treats you as protected (default no).
- **Opting out later**: the choice is made once, at onboarding, and is acted on immediately (a single subscription request); **there is no stored setting to change afterwards**. To leave the list later, use the unsubscribe link in any newsletter email.

### 3. No Warranty – Use at Your Own Risk
OpenOutreach is provided **AS IS**, without warranties of any kind (express or implied), including fitness for a particular purpose, non-infringement, or that it will not cause harm to your accounts, mailboxes, domains, or data.

The developer(s):
- Do not guarantee any results from using the tool
- Are not responsible for account/domain/mailbox suspensions, deliverability harm, lost business, legal consequences, or other damages
- Recommend you review the terms of every third-party service you connect (data provider, email-finder) before use

### 4. How the Project Is Funded (Affiliate Links)
OpenOutreach is free and open-source. It sustains itself through **affiliate links**: the unavoidably-paid third-party service the tool relies on — the email-finder, which powers both lead discovery and address resolution — is surfaced during onboarding through an affiliate link. If you sign up through one, the project may earn a commission **at no markup to you**. You are free to sign up any other way.

**The attribution line is the one thing the project takes out of your sending.** Every message this software sends — opener and follow-up alike — ends with the fixed line **"Sent with OpenOutreach"**. It is always on and **there is no setting that removes it**. It carries **no URL, no tracking pixel and no per-install identifier**: nothing reports back to the maintainer about whether you sent, to whom, or with what result.

**One funding mechanism has been removed, and it matters that you know it is gone**, because earlier versions of this notice disclosed it and very old installs may still run it:

- **The freemium promotional campaign.** The tool used to ship with a promotional campaign of its own, imported when the daemon started, which took its turn in the sending rotation alongside your own campaigns — so a share of the tool's sending advertised **OpenOutreach**, from **your own mailbox**, to recipients unrelated to your targets. **It is deleted, and nothing replaced it.** No message the current software sends goes to anyone but the leads in your own campaign.

Any hosted service operated by the maintainer is **not** covered by this notice and states its own terms at sign-up.

### 5. Lead Discovery, Email Enrichment, and Sending
**OpenOutreach sends email from your own mailbox.** You supply the SMTP credentials during onboarding; they are verified by a real login before they are stored, and the mail goes out over your connection, from your address, under your identity. The maintainer operates no relay and sees no message. **You may also stop at the exported file** and send with a tool of your own — the software is built so that half is a complete deliverable.

Address resolution runs through a **third-party email-finder** (e.g. BetterContact) — a paid service you sign up for and configure yourself. It is optional: leads export with their qualification reason whether or not an address was resolved.

- **Data protection**: resolving and storing a person's work email is processing of personal data. Where data-protection law applies (GDPR, UK GDPR, LGPD, etc.) **you are the data controller** and are responsible for a lawful basis, honouring access/erasure/objection requests, and any required disclosures. OpenOutreach provides the mechanism, not legal cover.
- **Anti-spam law applies to you as the sender.** Unsolicited commercial email is regulated — CAN-SPAM (US), GDPR/ePrivacy (EU/EEA), CASL (Canada), the Spam Act (Australia), and others. Requirements commonly include truthful sender and subject lines, a valid physical postal address, and a working, honoured opt-out. **The software supplies some of these and not all of them.** It is your mailbox and your message: you are the sender in law, and compliance is yours.
- **What the software does provide on every message it sends**: a `List-Unsubscribe` header pointing at a `+unsub` alias of your own sending address, a visible plain-text opt-out line in the body, and a suppression list checked before every send and again at ingest. **What it does not provide**: a physical postal address, or any verification that your subject lines and sender identity are truthful. Add the postal address to your configured signature if your jurisdiction requires one.
- **The `+unsub` alias must actually reach you.** The unsubscribe header points at a plus-addressed alias of your sending address. If your provider does not deliver plus-addressed mail to that mailbox, a recipient who unsubscribes will believe they have opted out while nothing receives it. Verify this before your first send.
- **If you export and send elsewhere instead, the whole duty moves with the sending.** Your sequencer becomes the only thing that can honour an opt-out. Instantly and Smartlead both block a suppressed address at import; confirm your own tool does the same, and **turn on its import deduplication** (opt-in on Smartlead, undocumented on Instantly) or exporting the same lead twice can contact the same person twice.
- **Accuracy**: finder results may be wrong, stale, or belong to a different person. You are responsible for whom you contact and what you send.

### 6. Central Contacts Store (Contribution and Resolution)
OpenOutreach connects to an optional **central contacts store operated by the project maintainer** (`hub.openoutreach.app`). It pools work email addresses across the OpenOutreach network so a contact one operator has already paid to resolve can be served — for free — to another, lowering everyone's email-finder spend as coverage grows. By running the software with contribution enabled you participate as described here.

- **What is contributed, and when**: at the **one** moment a real contact comes into existence — **after a paid email-finder returns a verified work email** — OpenOutreach sends a minimal record: the person's **profile identifier** (the stored, never-fetched profile URL), their **country code**, and the **work email address(es)** resolved. No name, headline, company, title, phone, or profile text is sent. *(The store is now sourced only from paid finder results; the earlier contribution path that captured a 1st-degree connection's contact info has been removed with the browser channel.)* Where a vector for that person is already cached on your machine, the record also carries a **384-dimension numeric profile vector** computed locally — the raw profile text never leaves your machine. (There is no separate switch for the vector: it is included whenever it is already in hand.)
- **Whether you contribute is derived from your country — it is not a setting.** If your operation is **not** based in the EU/EEA, UK, or Switzerland, contribution is **on**, and **there is no toggle to turn it off**: it can be disabled only by modifying the source, which the licence permits. If your operation **is** based there, the software contributes nothing at all (an unreadable country is treated as protected).
- **The consequence for protected operators.** The store works give-to-get: an operator's access token is minted by their **first contribution**. An EEA/UK/CH-based operator therefore never contributes, never earns a token, and so **never resolves from the store** — every lookup falls through to the paid finder. This is a structural consequence of the jurisdiction rule, not a penalty, and it means the store cannot lower your costs if you are based there.
- **Geo-gate on the people in the store**: independently of where *you* are, a contact located in the **EU/EEA, UK, or Switzerland — or whose location cannot be determined — is never written to the store.** This gate runs authoritatively **server-side**; the client's pre-filter is only a bandwidth optimisation.
- **Resolution is a disclosure to third parties.** OpenOutreach reads the store *first*, before spending a paid finder credit. A hit is served free. So an email you contribute **may be disclosed to other operators** to contact that person, and emails others contributed may be disclosed to you. This is a disclosure of personal data to a third party — in substance the commercial-contact-data model (Apollo, Cognism, Dropcontact). It is **not** a sale of data, but it **is** a separate processing purpose from your own outreach.
- **Your role and responsibilities.** Where data-protection law applies, contributing and resolving personal data is processing for which you may be a controller or joint controller alongside the maintainer. **You remain responsible** for a lawful basis (the project relies on legitimate interest for B2B professional contact data only), for honouring access/erasure/objection requests, and for any required notices.
- **Suppression / opt-out.** Any person whose email is in the store can be removed and blocked from re-entry via the store's suppression mechanism (`POST /api/v2/suppress/`), honoured across the whole store. The store publishes a separate **Privacy Notice** for those people at <https://hub.openoutreach.app/privacy/>.

### 7. Your Responsibility
By downloading, installing, configuring, or running OpenOutreach, you:
- Confirm you are of legal age and have authority to accept these terms
- Agree to use the tool only in compliance with all applicable laws (data-protection/privacy law such as GDPR, anti-spam law such as CAN-SPAM/CASL) and with the terms of every third-party service you connect
- Accept full responsibility for the contacts you process, and for every email sent to them from your mailbox — whether this software sent it or a tool of your own did
- Understand that modifying the code to disable the hub contribution is permitted under the licence, but remains your responsibility

If you do **not** agree with any part of this notice — especially the central contacts store — **do not use this software**. Delete it immediately.

Questions or concerns? Open an issue on the repository or contact the maintainer(s).

**Continued use constitutes acceptance of this Legal Notice.**
