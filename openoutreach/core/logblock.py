# openoutreach/core/logblock.py
"""Shared log-block grammar — one action renders as one aligned block.

A block is a bold ``▶`` (or ``▸``) *header* naming the action and its subject,
then indented *step* lines — each a status glyph + fixed-width label + message.
A run of blocks reads as an aligned column you can scan down by outcome (green
``✓`` succeeded, yellow ``✗`` came back empty). The subject (a profile URL, a
query) is named **once** in the header and never repeated in the steps.

One grammar for the whole daemon: the discovery walk (``discovery.query_header``)
and the email pipeline task handlers (``find_email`` / ``collect_email``) both
render through these primitives, so their output is one visual system.
"""
from termcolor import colored

# The longest step label ("bettercontact"); every message column starts here, so
# a stack of steps lines up regardless of which labels appear.
STEP_LABEL_WIDTH = 13


def block_header(title: str, color: str, meta: str = "") -> str:
    """The bold ``▶`` header naming one task block; optional ``· meta`` suffix.

    ``title`` carries the whole subject line (``action · campaign · url``) in the
    action's colour; ``meta`` (e.g. ``attempt 3``) trails it default-weight so the
    subject, not the bookkeeping, holds the eye.
    """
    line = colored(f"▶ {title}", color, attrs=["bold"])
    return line + (f"  · {meta}" if meta else "")


def step_line(label: str, message: str, glyph: str = "·", color: str | None = None) -> str:
    """One indented step under a block header: glyph · label · message.

    ``color`` tints the glyph and label together — the outcome colour. The message
    stays default-weight so the header's subject, not the plumbing below it, carries
    the eye.
    """
    tint = (lambda s: colored(s, color, attrs=["bold"])) if color else (lambda s: s)
    return f"  {tint(glyph)}  {tint(label.ljust(STEP_LABEL_WIDTH))}  {message}"
