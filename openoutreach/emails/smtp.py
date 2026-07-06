# openoutreach/emails/smtp.py
"""Auth-only SMTP check, run when a mailbox is connected.

No test send — boxes are mid-warmup; we only confirm the credentials log in.
The transport is chosen by port so real providers all pass the gate:

  * 465        → implicit TLS (``SMTP_SSL``) — Google, Fastmail, most hosts
  * 587 / 25   → plaintext connect, then ``STARTTLS`` *if the server advertises
                 it* (it always does on 587), else stay plaintext (rare relays)

Hard-coding ``starttls()`` was the old bug: a 465-only box could never connect,
so onboarding rejected working credentials and re-asked the mailbox forever.
"""
from __future__ import annotations

import smtplib
import ssl


def verify_auth(host: str, port: int, username: str, password: str) -> tuple[bool, str]:
    """Connect with the right transport for *port*, log in, quit. Return ``(ok, message)``.

    A Google/IceMail box rejects its *login* password with 534/535 — the message
    surfaces the "use the app password" hint for that case. Any connection-level
    failure (wrong host, TLS mismatch, no route) is reported, never raised.
    """
    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                smtp.login(username, password)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(username, password)
        return True, "ok"
    except smtplib.SMTPAuthenticationError as e:
        hint = (
            " — paste the app password, not the mailbox login password"
            if e.smtp_code in (534, 535) else ""
        )
        return False, f"auth rejected ({e.smtp_code}){hint}"
    except (smtplib.SMTPException, OSError) as e:
        return False, f"connection failed: {e}"
