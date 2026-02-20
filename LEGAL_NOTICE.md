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

- **What happens**: On average every N of your campaign actions, the tool automatically selects a LinkedIn profile (unrelated to your campaign targets — your own qualified leads are never affected), sends a connection request (if not already connected), and delivers a promotional message on behalf of OpenOutreach.
- **Remote configuration**: N (the average interval), the ranking model used to select targets, and the message content are **retrieved from a remote server** at startup. These values are controlled by the project maintainer and may change between versions or runs without notice.
- **Impact on you**: These messages appear as sent from **your** account.
- **Opting out**: These actions cannot be disabled without modifying the source code yourself, which is permitted under the licence.

### 5. Your Responsibility
By downloading, installing, configuring, or running OpenOutreach, you:
- Confirm you are of legal age and have authority to accept these terms
- Agree to use the tool only in compliance with all applicable laws (including data protection/privacy laws like GDPR if relevant)
- Accept full responsibility for any consequences of automation on your LinkedIn account(s)
- Understand that modifying the code to remove/disable freemium promotional actions is permitted under the licence, but doing so remains your responsibility

If you do **not** agree with any part of this notice — especially the freemium promotional actions or the violation of LinkedIn's terms — **do not use this software**. Delete it immediately.

Questions or concerns? Open an issue on the repository or contact the maintainer(s).

**Continued use constitutes acceptance of this Legal Notice.**
