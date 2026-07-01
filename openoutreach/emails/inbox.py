# openoutreach/emails/inbox.py
"""IMAP reply-reader — the email analog of ``linkedin/db/chat.py:sync_conversation``.

Reads replies to a deal's email thread over IMAP, upserts them as incoming
``ChatMessage`` rows, and folds the new ones into the Deal's ``chat_summary``.
The follow-up agent then reads the same ChatMessage rows it always has — only the
source of inbound messages moved from Voyager to the mailbox.

Threading: the opener's Message-ID (``Deal.email_message_id``) is the immutable
thread root; a reply carries it in ``References``/``In-Reply-To``, so a single
IMAP header search over the deal's own box finds this thread's replies. Dedup is
by the reply's own Message-ID (``ChatMessage.external_id``), idempotent without
trusting IMAP ``\\Seen`` flags.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

logger = logging.getLogger(__name__)

IMAP_TIMEOUT_SECONDS = 30


def sync_inbox(session, deal) -> None:
    """Read this deal's email replies over IMAP and fold new ones into its summary.

    No-op until the deal has been emailed (a bound ``mailbox`` + a thread root):
    an un-emailed deal has no thread to read. Side-effects only — the agent reads
    the resulting ChatMessage rows, mirroring ``sync_conversation``.
    """
    if not deal.mailbox_id or not deal.email_message_id:
        return

    new_messages = _fetch_replies(session, deal)
    if not new_messages:
        return

    from openoutreach.core.db.summaries import seller_name_from, update_chat_summary

    update_chat_summary(deal, new_messages, seller_name=seller_name_from(session))


# ── IMAP transport ────────────────────────────────────────────────


def _fetch_replies(session, deal) -> list:
    """Fetch this thread's replies from the deal's box and upsert incoming rows.

    Returns the newly-created ``ChatMessage`` rows in chronological order, so the
    caller can incrementally update ``chat_summary``.
    """
    mailbox = deal.mailbox
    root_id = deal.email_message_id

    imap = imaplib.IMAP4_SSL(mailbox.imap_host, mailbox.imap_port, timeout=IMAP_TIMEOUT_SECONDS)
    try:
        imap.login(mailbox.username, mailbox.password)
        imap.select("INBOX")
        nums = _search_thread(imap, root_id)
        new_messages = [
            row
            for num in nums
            if (row := _upsert_reply(session, deal, mailbox, _fetch_message(imap, num))) is not None
        ]
    finally:
        _logout(imap)

    new_messages.sort(key=lambda m: m.creation_date or m.pk)
    logger.debug("inbox: %d reply(ies) matched thread %s (%d new)",
                 len(nums), root_id, len(new_messages))
    return new_messages


def _search_thread(imap, root_id: str) -> list:
    """Message sequence numbers of INBOX replies that reference the thread root.

    Matches the root Message-ID in either ``References`` or ``In-Reply-To`` (a
    direct reply to the opener carries it in both). Searches on the id core
    without angle brackets, which HEADER substring-matches ``<core>`` in the
    reply's header regardless of surrounding ids.
    """
    core = root_id.strip("<>")
    status, data = imap.search(
        None, "OR", "HEADER", "References", core, "HEADER", "In-Reply-To", core,
    )
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _fetch_message(imap, num) -> Message:
    """Fetch and parse one message by sequence number."""
    status, data = imap.fetch(num, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        return email.message_from_bytes(b"")
    return email.message_from_bytes(data[0][1])


def _logout(imap) -> None:
    """Best-effort IMAP teardown; a failed logout must not mask the real work."""
    try:
        imap.close()
    except Exception:
        pass
    try:
        imap.logout()
    except Exception:
        pass


# ── Message → ChatMessage ─────────────────────────────────────────


def _upsert_reply(session, deal, mailbox, msg: Message):
    """Upsert an inbound reply as a ChatMessage; return the row only if newly created.

    Skips our own outbound copies (From == the sending box) and messages with no
    Message-ID or empty body. Dedup key is ``(deal, external_id=reply Message-ID)``.
    """
    from openoutreach.chat.models import ChatMessage

    message_id = (msg.get("Message-ID") or "").strip()
    from_addr = parseaddr(msg.get("From", ""))[1].lower()
    if not message_id or from_addr == (mailbox.from_address or "").lower():
        return None

    body = _plain_text_body(msg)
    if not body:
        return None

    sent_at = _sent_at(msg)
    obj, created = ChatMessage.objects.update_or_create(
        deal=deal,
        external_id=message_id,
        defaults={
            "content": body,
            "is_outgoing": False,
            "owner": session.django_user,
            **({"creation_date": sent_at} if sent_at else {}),
        },
    )
    return obj if created else None


def _sent_at(msg: Message):
    """Timezone-aware send time from the Date header, or None if unparseable."""
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def _plain_text_body(msg: Message) -> str:
    """Extract the text/plain body, stripped of the quoted reply history.

    Prefers the first ``text/plain`` part (skipping attachments); the whole
    payload is the fallback for a non-multipart message. Quoted history is
    trimmed so ``chat_summary`` and the agent see only the lead's new words.
    """
    raw = _first_text_plain(msg)
    return _strip_quoted(raw)


def _first_text_plain(msg: Message) -> str:
    """The decoded text/plain payload, or the bare payload for a simple message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() != "text/plain":
                continue
            if "attachment" in (part.get("Content-Disposition") or "").lower():
                continue
            return _decode(part)
        return ""
    return _decode(msg)


def _decode(part: Message) -> str:
    """Decode a part's payload to text, tolerating a missing/wrong charset."""
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


# Common reply-quote openers: "On <date>, <name> wrote:" and its localized kin,
# plus Outlook's "-----Original Message-----" divider.
_QUOTE_MARKERS = re.compile(
    r"^\s*(on .+wrote:|-{2,}\s*original message\s*-{2,}|_{5,})\s*$",
    re.IGNORECASE,
)


def _strip_quoted(text: str) -> str:
    """Drop everything from the first quote marker or the trailing ``>`` block.

    Conservative: cuts at the first recognized "On … wrote:" / "Original Message"
    divider, else at the first line of a contiguous run of ``>``-quoted lines that
    continues to the end. Leaves inline text untouched when there is no clear
    boundary.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _QUOTE_MARKERS.match(line):
            return "\n".join(lines[:i]).strip()
    for i, line in enumerate(lines):
        if line.lstrip().startswith(">") and all(
            l.lstrip().startswith(">") or not l.strip() for l in lines[i:]
        ):
            return "\n".join(lines[:i]).strip()
    return text.strip()
