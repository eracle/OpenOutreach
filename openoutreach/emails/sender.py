# openoutreach/emails/sender.py
"""Send one outbound email through a Mailbox's SMTP credentials.

No error handling by design: a failed send raises and the EMAIL task is marked
FAILED by the daemon, then retried on the next cycle. The mailbox is left
untouched — re-import with fixed credentials to repair a dead box.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

SMTP_TIMEOUT_SECONDS = 30


def send_email(
    mailbox,
    to_address: str,
    subject: str,
    body: str,
    *,
    bcc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    """Send ``body`` from ``mailbox`` to ``to_address``; return the Message-ID.

    The mailbox's signature is appended to ``body`` here rather than at the call
    sites, so every send — opener and follow-up — carries it.

    ``bcc`` (when set) blind-copies the operator's own address so they keep a
    private record of every send; ``send_message`` strips the Bcc header before
    transmission, so the To recipient never sees it. The call sites pass it only
    when ``conf.BCC_OPERATOR_ON_SEND`` is enabled (off by default), else ``None``.

    ``in_reply_to``/``references`` thread a reply onto an existing email thread
    (both are prior Message-IDs). The returned Message-ID is stored on the
    outgoing ChatMessage so the next touch can thread onto it.
    """
    message = _build_message(mailbox, to_address, subject, body, bcc, in_reply_to, references)
    _deliver(mailbox, message)
    return message["Message-ID"]


# ── Message assembly ──────────────────────────────────────────────


def _build_message(mailbox, to_address, subject, body, bcc, in_reply_to, references) -> EmailMessage:
    """Assemble the email with threading headers and a domain-anchored Message-ID."""
    message = EmailMessage()
    message["Message-ID"] = _mint_message_id(mailbox.from_address)
    message["From"] = mailbox.from_address
    message["To"] = to_address
    if bcc:
        message["Bcc"] = bcc
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = references or in_reply_to
    message.set_content(_sign(body, mailbox.signature))
    return message


def _sign(body: str, signature: str | None) -> str:
    """Append the mailbox's sign-off, separated by a blank line.

    Declined ("") or never asked (None) ⇒ body unchanged.
    """
    signature = (signature or "").strip()
    if not signature:
        return body
    return f"{body.rstrip()}\n\n{signature}\n"


def _mint_message_id(from_address: str) -> str:
    """A unique RFC-5322 Message-ID anchored to the sending domain.

    Anchoring to the From domain (rather than ``make_msgid``'s default local
    hostname) keeps the Message-ID aligned with the sender and avoids leaking
    the container hostname.
    """
    domain = from_address.rsplit("@", 1)[-1]
    return make_msgid(domain=domain)


# ── Transport ─────────────────────────────────────────────────────


def _deliver(mailbox, message: EmailMessage) -> None:
    """Log into the mailbox over SMTP+STARTTLS and send one message."""
    with smtplib.SMTP(mailbox.host, mailbox.port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.starttls()
        smtp.login(mailbox.username, mailbox.password)
        smtp.send_message(message)
