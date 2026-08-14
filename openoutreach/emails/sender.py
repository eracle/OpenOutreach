# openoutreach/emails/sender.py
"""Send one outbound email through a Mailbox's SMTP credentials.

No error handling by design: a failed send raises and the EMAIL task is marked
FAILED by the daemon, then retried on the next cycle. The mailbox is left
untouched — re-import with fixed credentials to repair a dead box.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from django.utils import timezone

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 30


def send_email(
    mailbox,
    to_address: str,
    subject: str,
    body: str,
    *,
    campaign=None,
    bcc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    thread=None,
):
    """Send ``body`` from ``mailbox`` to ``to_address``; return its ``Message`` row.

    The mailbox's signature, the opt-out block and the product attribution line
    are appended to ``body`` here rather than at the call sites, so every send —
    opener and follow-up — carries all three, in the order body → signature →
    opt-out → attribution. The matching ``List-Unsubscribe`` header goes on the
    same message: header and body line are two halves of one opt-out, and only
    building them together makes it impossible to ship one without the other.

    **The send joins the mail log before it is attempted**, and what the receiver
    answers is recorded against it: an ``accepted`` event carrying the SMTP queue
    id, or a ``rejected``/``deferred``/``error`` one carrying the refusal. A send
    that is never accepted therefore leaves a row saying so, which is what makes
    "what is my bounce rate?" arithmetic rather than an opinion.

    ``bcc`` (when set) blind-copies the operator's own address so they keep a
    private record of every send; ``send_message`` strips the Bcc header before
    transmission, so the To recipient never sees it. Call sites get it from
    ``operator_bcc`` rather than deciding for themselves.

    ``in_reply_to``/``references`` thread a reply onto an existing email thread
    (both are prior Message-IDs); ``thread`` puts the row in that conversation
    directly, rather than making the log re-derive what the caller already knows.

    ``campaign`` decides whether the message *text* is logged as well as its
    metadata — see ``_sent_block``. It is passed rather than inferred from ``bcc``,
    which is ``None`` both on freemium campaigns and on an operator who never set
    an address; keying off it would silently drop the log for the second.
    """
    email_message = _build_message(
        mailbox, to_address, subject, body, bcc, in_reply_to, references)
    row = _record_send(mailbox, email_message, body, thread)
    _deliver(mailbox, email_message, row)
    logger.info("email sent from %s to %s: %s [%s]",
                mailbox.from_address, to_address, subject, email_message["Message-ID"])
    if campaign is not None and not campaign.is_freemium:
        logger.info("%s", _sent_block(subject, body))
    return row


def suppressed(lead) -> bool:
    """True when *lead* may not be emailed — re-read from the DB at send time.

    The eleven candidate queries that filter ``disqualified`` are convention, not
    a DB constraint (``core/pipeline/qualify.py``), and an unsubscribe can land
    between the pool query and the send: the outreach agent runs for seconds and
    reads the inbox on its way past. This is the last gate before a message is
    built, so the answer has to come from the row, not the in-memory copy the
    caller selected earlier.
    """
    from openoutreach.crm.models import Lead

    return Lead.objects.filter(pk=lead.pk, disqualified=True).exists()


def unsubscribe_address(from_address: str) -> str:
    """The plus-addressed alias a client-generated unsubscribe is sent to.

    Plus-addressing means no second mailbox, no DNS and no web surface: the alias
    delivers to the same INBOX the daemon already reads over IMAP, so the opt-out
    path is the box's own mail. The mail pass matches on this exact string.

    **This assumes the provider routes ``+`` tags to the base mailbox.** Gmail and
    Google Workspace do, and they are both the documented default (``smtp.gmail.com``
    / ``imap.gmail.com``) and what IceMail provisions, so it holds for the boxes
    this actually runs on. A provider that *rejects* plus-addressing would bounce
    the opt-out, and the recipient — believing they unsubscribed — would get the
    next follow-up and reasonably hit "spam". Nothing verifies the alias round-trips
    at onboarding; connecting a non-Gmail box is the case to check by hand.
    """
    local, _, domain = from_address.partition("@")
    return f"{local}+unsub@{domain}"


def operator_bcc(user, campaign) -> str | None:
    """The address to blind-copy on this campaign's sends, or None for no copy.

    The operator gets a private copy of every send on **their own** campaigns —
    it is their outreach, from their mailbox, and they need the thread in their
    inbox. On a **freemium** campaign the outreach is OpenOutreach's own, so
    there is no copy to give: the operator is not a party to that conversation
    and their inbox is not a log for it.
    """
    if campaign.is_freemium:
        return None
    return user.email or None


def _sent_block(subject: str, body: str) -> str:
    """What the agent wrote, indented under its subject.

    The ``body`` argument, deliberately — not the assembled message. The signature,
    opt-out line and attribution are fixed text appended to every send, so logging
    them repeats what the operator already knows on every line and buries the one
    part that differs: what the agent actually composed for this lead.

    **The operator's own campaigns only.** They already receive every one of these
    in full by BCC, so the log discloses nothing they do not already hold. Freemium
    outreach is OpenOutreach's own conversation with someone who is not their
    contact, and it stays metadata-only for the same reason it gets no BCC.
    """
    indented = "\n".join(f"    {line}" for line in body.rstrip().splitlines())
    return f"    Subject: {subject}\n\n{indented}"


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
    message["List-Unsubscribe"] = _list_unsubscribe(mailbox.from_address)
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = references or in_reply_to
    message.set_content(_attribute(_opt_out(_sign(body, mailbox.signature))))
    return message


def _list_unsubscribe(from_address: str) -> str:
    """The RFC 2369 ``List-Unsubscribe`` value — a ``mailto:``, never an ``https:``.

    RFC 8058 one-click needs an HTTPS endpoint accepting POST; the self-hosted
    daemon has no web surface, and routing unsubscribes through the hub would make
    every install call home. A ``mailto:`` URI needs no infrastructure and the
    daemon already speaks IMAP, so the opt-out lands where it can be read.
    ``List-Unsubscribe-Post`` is deliberately absent — it is only valid alongside
    an ``https:`` URI, and one-click binds bulk senders at 5,000+ messages/day.
    """
    return f"<mailto:{unsubscribe_address(from_address)}?subject=unsubscribe>"


def _sign(body: str, signature: str | None) -> str:
    """Append the mailbox's sign-off, separated by a blank line.

    Declined ("") or never asked (None) ⇒ body unchanged.
    """
    signature = (signature or "").strip()
    if not signature:
        return body
    return f"{body.rstrip()}\n\n{signature}\n"


OPT_OUT_LINE = "Don't want to hear from me? Reply with \"unsubscribe\" and I'll stop."


def _opt_out(body: str) -> str:
    """Append the visible opt-out line, after the signature and before the attribution.

    Plain text rather than a link: a typed reply works in every client, including
    the ones where Gmail declines to render its own unsubscribe button, so it
    covers the recipients the ``List-Unsubscribe`` header alone does not reach.
    Its job is the same as the header's — put an exit in front of someone who
    wants one, so they take it instead of the spam button.
    """
    return f"{body.rstrip()}\n\n{OPT_OUT_LINE}\n"


ATTRIBUTION = "Sent with OpenOutreach"


def _attribute(body: str) -> str:
    """Append the product attribution line, separated by a blank line.

    Always on, last in the message (after the signature): every recipient of an
    outbound email is a plausible future operator. It names the product without
    linking it — a bare name reads as a footer, a URL reads as an ad, and anyone
    curious enough to act on it can search. The separator matches every other
    block's, so the tail of the message reads as evenly spaced.
    """
    return f"{body.rstrip()}\n\n{ATTRIBUTION}\n"


def _mint_message_id(from_address: str) -> str:
    """A unique RFC-5322 Message-ID anchored to the sending domain.

    Anchoring to the From domain (rather than ``make_msgid``'s default local
    hostname) keeps the Message-ID aligned with the sender and avoids leaking
    the container hostname.
    """
    domain = from_address.rsplit("@", 1)[-1]
    return make_msgid(domain=domain)


# ── Transport ─────────────────────────────────────────────────────


def _record_send(mailbox, email_message: EmailMessage, body: str, thread):
    """Write the outbound row *before* the transport runs, and thread it.

    Outbound is authoritative in a way inbound never is: we composed the body, so
    ``body_text`` holds what the agent wrote (before the signature, opt-out and
    attribution, which are identical on every send) and only the headers are kept
    as bytes. It is stored classified and processed — there is nothing to work out
    later about a message we wrote ourselves.
    """
    from openoutreach.core.operator import get_active_user
    from openoutreach.emails import parsing, threads
    from openoutreach.emails.classify import CLASSIFIER_VERSION
    from openoutreach.emails.models import Direction, Kind, Message

    now = timezone.now()
    row = Message.objects.create(
        mailbox=mailbox,
        thread=thread,
        direction=Direction.OUTBOUND,
        message_id=parsing.normalize_id(email_message["Message-ID"]),
        from_address=(email_message["From"] or "").lower()[:320],
        to_address=(email_message["To"] or "").lower()[:320],
        subject=email_message["Subject"] or "",
        in_reply_to=parsing.normalize_id(email_message["In-Reply-To"] or ""),
        references_ids=parsing.referenced_ids(email_message),
        raw=_headers_of(email_message),
        body_text=body,
        sent_at=now,
        kind=Kind.OUTBOUND,
        classified_at=now,
        classifier_version=CLASSIFIER_VERSION,
        processed_at=now,
        owner=get_active_user(),
    )
    threads.assign(row)
    return row


def _headers_of(email_message: EmailMessage) -> bytes:
    """The message's headers as bytes — everything above the body it already stores."""
    header = EmailMessage()
    for name, value in email_message.items():
        header[name] = value
    return header.as_bytes()


def _deliver(mailbox, email_message: EmailMessage, row) -> None:
    """Log into the mailbox over SMTP+STARTTLS and send one message.

    Both answers are recorded against *row*: the ``250`` on the way out — with the
    receiver's own queue id, which is the handle a provider asks for when a
    delivery is disputed and which used to be thrown away — and any refusal on the
    way past, before it is re-raised unchanged. The step still fails and is still
    retried; what changes is that the receiver's word survives the traceback.
    """
    from openoutreach.emails.delivery_policy import record_acceptance, record_failure

    try:
        with _SMTP(mailbox.host, mailbox.port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(mailbox.username, mailbox.password)
            smtp.send_message(email_message)
            record_acceptance(row, *smtp.accepted_response)
    except (smtplib.SMTPException, OSError) as exc:
        record_failure(row, exc)
        raise


class _SMTP(smtplib.SMTP):
    """``smtplib.SMTP`` that keeps the receiver's answer to DATA.

    ``sendmail`` returns only the refused recipients and drops the final
    ``250 2.0.0 OK <queue id>`` on the floor. That line is the receiver's own
    handle on the message — the one a provider asks for when a delivery is
    disputed — so the smallest possible override keeps it.
    """

    accepted_response: tuple[int | None, bytes] = (None, b"")

    def data(self, msg):
        code, response = super().data(msg)
        self.accepted_response = (code, response)
        return code, response
