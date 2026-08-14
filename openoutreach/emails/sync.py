# openoutreach/emails/sync.py
"""**sync** — mirror a mailbox's inbound mail into the log. Transport only.

The one job that touches the network, and the only one that does. It stores bytes
and says nothing about what they mean: a message it keeps lands as a row with no
``kind`` and no ``processed_at``, because the state that did not exist — *fetched,
not yet interpreted* — is what let nine days of lost mail read as nine quiet days.
Deciding what a message *is* happens later, over stored bytes, and can be redone.

Two rules carry the correctness here, and neither is the cursor:

- **Identity is the Message-ID**, never the UID. A re-walk stores nothing new, so
  ``FolderCoverage`` is a fetch optimisation in front of a unique key rather than
  the record of what was read. Reset it to zero and the box is simply re-read.
- **A message that cannot be fetched is not stepped over.** The walk stops in
  front of it and the next pass retries. The cursor advances to what was
  *stored*, one message at a time — never to ``UIDNEXT`` or to a ``max()`` over
  what was merely seen, which is the shape that lost UID 27 and 28 for good.

**Only our own conversation is stored.** A mailbox is a person's real mailbox, and
mirroring everything that lands in it would make a CRM out of their private mail —
and a large one, on a box with years of history. A message is recorded when — and
only when — one of four things is true:

- it names a Message-ID this box minted (``References``/``In-Reply-To``), which is
  the hidden id every send carries;
- it is addressed to the ``+unsub`` alias, which is how a client's unsubscribe
  button arrives and carries no threading headers at all;
- it comes from an address this box has written to;
- it is a non-delivery report. A bounce arrives from the receiver's own daemon,
  quotes our headers in a body part rather than in the envelope, and is the one
  piece of mail that is unambiguously about a send of ours even when nothing in
  its headers threads.

The rule about *addresses we have written to* is what keeps the repair property
that matters. A reply whose threading headers we mis-read is still stored — it
came from someone we mailed — so a corrected rule re-reads it and recovers the
conversation. What is skipped is mail from people this box has never written to,
which no threading fix could have turned into one of our replies.

Skipped messages are counted and logged, never silently passed: the walk says how
much of the box it declined to keep, so "we hold nothing about this" and "nothing
arrived" stay different statements.
"""
from __future__ import annotations

import logging
import ssl

from django.utils import timezone
from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

from openoutreach.emails import parsing, threads
from openoutreach.emails.models import Direction, FolderCoverage, Message

logger = logging.getLogger(__name__)

IMAP_TIMEOUT_SECONDS = 30

# The folder mirrored today. On Gmail this is a *label*, so mail filed as spam or
# archived before a pass runs never carried it — a known gap, and a cheap one to
# close later: identity is the Message-ID, so adding a folder migrates nothing and
# re-walking one stores only what INBOX never saw.
INBOX = "INBOX"


def mirror(mailbox, folder: str = INBOX) -> int:
    """Fetch this box's unmirrored mail into ``Message`` rows. Returns rows stored.

    Best-effort: an unreachable box logs and returns 0 with its coverage untouched,
    so an outage is never mistaken for an empty mailbox.
    """
    coverage, _ = FolderCoverage.objects.get_or_create(mailbox=mailbox, folder=folder)
    try:
        with _connect(mailbox) as client:
            uidvalidity, uidnext = _folder_state(client, folder)
            start = _resume_from(coverage, mailbox, uidvalidity, uidnext)
            client.select_folder(folder, readonly=True)
            uids = _new_uids(client, start)
            # A new epoch (or a first sight) re-bases the cursor on where this walk
            # begins, so coverage never keeps a UID from a numbering that is gone.
            if coverage.uidvalidity != uidvalidity:
                coverage.last_uid = start
            stored, walked_to_the_end = _walk(
                client, mailbox, coverage, folder, uidvalidity, uids)
    except (IMAPClientError, OSError) as exc:
        logger.warning("sync: could not read %s (%s)", mailbox.from_address, exc)
        return 0

    _record_coverage(coverage, uidvalidity, complete=walked_to_the_end)
    if stored:
        logger.info("sync: %s mirrored %d message(s) above UID %d",
                    mailbox.from_address, stored, start)
    return stored


def _walk(client, mailbox, coverage, folder, uidvalidity, uids) -> tuple[int, bool]:
    """Store each UID in turn. Returns ``(rows stored, reached the end)``.

    The cursor moves **behind** the walk, message by message and only over what
    was actually stored, so an interruption anywhere leaves a coverage row that
    understates our knowledge rather than overstating it. Overstating it is the
    only failure mode that loses mail.
    """
    stored = skipped = 0
    for uid in uids:
        headers = _fetch(client, uid, "BODY.PEEK[HEADER]", "BODY[HEADER]")
        if headers is None:
            logger.warning("sync: %s UID %d could not be fetched — stopping before it",
                           mailbox.from_address, uid)
            return stored, False
        outcome = _store(client, mailbox, folder, uid, uidvalidity, headers)
        stored += outcome is True
        skipped += outcome is None
        coverage.last_uid = uid
    if skipped:
        logger.info("sync: %s left %d message(s) unstored — not our conversation",
                    mailbox.from_address, skipped)
    return stored, True


def _store(client, mailbox, folder: str, uid: int, uidvalidity: int, headers: bytes):
    """Record one message. ``True`` stored · ``False`` already held · ``None`` skipped.

    Identity is read from the headers, so a message we later fetch in full keeps
    the id it was first stored under. A message already in the log — including one
    of our own sends, seen again from the server's side — is left exactly as it is.
    """
    msg = parsing.parse(headers)
    if not _is_our_conversation(mailbox, msg):
        return None

    message_id = parsing.message_id_of(msg, headers)
    if Message.objects.filter(mailbox=mailbox, message_id=message_id).exists():
        return False

    raw = _fetch(client, uid, "BODY.PEEK[]", "BODY[]") or headers

    message = Message.objects.create(
        mailbox=mailbox,
        direction=Direction.INBOUND,
        message_id=message_id,
        from_address=parsing.sender_address(msg)[:320],
        to_address=parsing.recipient_address(msg)[:320],
        subject=parsing.subject_of(msg),
        in_reply_to=parsing.in_reply_to(msg),
        references_ids=parsing.referenced_ids(msg),
        raw=raw,
        sent_at=parsing.sent_at(msg),
        received_at=timezone.now(),
        folder=folder,
        uid=uid,
        uidvalidity=uidvalidity,
    )
    threads.assign(message)
    return True


def _is_our_conversation(mailbox, msg) -> bool:
    """Whether this message belongs to outreach this box started.

    The four rules in the module docstring, cheapest first. All of them are
    answered from headers alone, so a message we are not keeping never costs a
    body fetch — and a stranger's mail costs one header read and nothing else.
    """
    from openoutreach.emails.classify import is_bounce
    from openoutreach.emails.sender import unsubscribe_address

    if parsing.addressed_to(msg, unsubscribe_address(mailbox.from_address)):
        return True
    if is_bounce(msg):
        return True

    named = parsing.referenced_ids(msg)
    if named and Message.objects.filter(
            mailbox=mailbox, direction=Direction.OUTBOUND, message_id__in=named).exists():
        return True

    sender = parsing.sender_address(msg)
    return bool(sender) and Message.objects.filter(
        mailbox=mailbox, direction=Direction.OUTBOUND, to_address=sender,
    ).exists()


# ── Coverage ──────────────────────────────────────────────────────


def _resume_from(coverage: FolderCoverage, mailbox, uidvalidity: int, uidnext: int) -> int:
    """The UID to resume above.

    Three cases:

    - **Same epoch** — resume above what we stored. The ordinary pass.
    - **A folder seen for the first time** — start at the box's high-water mark.
      A connected mailbox is usually a real one with years of unrelated mail in it,
      and mirroring a decade of a person's inbox to find replies to messages we
      have not sent yet is a large cost for no information. The log begins where
      the product does. Recorded as coverage rather than assumed, so what we hold
      and what exists stay distinguishable — and because identity is the
      Message-ID, an operator who *wants* the history sets ``last_uid`` to 0 and
      the next pass walks it in.
    - **A changed ``UIDVALIDITY``** — the server has reissued its UIDs, so the
      stored cursor points at unrelated mail. Re-walk from the start: it stores
      only what we never saw, and trusting the cursor would skip everything below
      it forever.
    """
    if uidvalidity == coverage.uidvalidity:
        return coverage.last_uid
    if coverage.uidvalidity:
        logger.info("sync: %s UIDVALIDITY %d → %d, re-reading from the start",
                    mailbox.from_address, coverage.uidvalidity, uidvalidity)
        return 0
    logger.info("sync: %s:%s first pass — starting at UID %d, not at its history",
                mailbox.from_address, coverage.folder, uidnext - 1)
    return max(0, uidnext - 1)


def _record_coverage(coverage: FolderCoverage, uidvalidity: int, *, complete: bool) -> None:
    """Persist what we now know we have read.

    ``synced_at`` moves only on a walk that reached the end, so a partial pass
    leaves coverage saying *we are behind* rather than *we are current* — the
    difference between "no reply" and "reply not read".
    """
    coverage.uidvalidity = uidvalidity
    if complete:
        coverage.synced_at = timezone.now()
    coverage.save(update_fields=["uidvalidity", "last_uid", "synced_at", "updated_at"])


# ── IMAP transport ────────────────────────────────────────────────


def _connect(mailbox) -> IMAPClient:
    """A logged-in IMAP session for *mailbox*, as a context manager."""
    client = IMAPClient(
        mailbox.imap_host,
        port=mailbox.imap_port,
        ssl=True,
        ssl_context=ssl.create_default_context(),
        timeout=IMAP_TIMEOUT_SECONDS,
    )
    client.login(mailbox.username, mailbox.password)
    return client


def _folder_state(client, folder: str) -> tuple[int, int]:
    """``(UIDVALIDITY, UIDNEXT)`` for *folder*, read before anything is selected."""
    status = client.folder_status(folder, ["UIDVALIDITY", "UIDNEXT"])
    return int(status[b"UIDVALIDITY"]), int(status[b"UIDNEXT"])


def _new_uids(client, start_uid: int) -> list[int]:
    """UIDs strictly above *start_uid*, oldest first.

    A server answering ``start:*`` when nothing is above ``start`` returns the
    newest message instead of an empty set, so the result is filtered rather than
    trusted — otherwise every quiet pass would re-read the same message.
    """
    found = client.search(["UID", f"{start_uid + 1}:*"])
    return sorted(int(uid) for uid in found if int(uid) > start_uid)


def _fetch(client, uid: int, spec: str, key: str) -> bytes | None:
    """One part of one message, or None if the server would not give it up."""
    try:
        response = client.fetch([uid], [spec])
    except IMAPClientError as exc:
        logger.warning("sync: FETCH %s on UID %d failed (%s)", spec, uid, exc)
        return None
    part = response.get(uid, {}).get(key.encode())
    return part if isinstance(part, bytes) else None
