"""Enrichment — the finder's one paid step: a profile URL in, a work email out.

The split is between *the providers*, *the seam* and *the pipeline step*:

- ``provider`` is the seam: one interface, and ``active()`` names which vendor an
  install resolves with, from whichever key is configured. Everything above this
  package talks to a provider, never to a vendor.
- ``bettercontact`` and ``apollo`` are the clients, interchangeable at this leg. They
  differ only in transport — BetterContact's waterfall is a submit-and-poll job,
  Apollo's ``people/match`` answers in one call — and ``provider.Lookup`` carries that
  difference so the pipeline does not have to know it.
- **Discovery is not interchangeable.** ``discovery.py`` pages BetterContact's free
  Lead Finder index through ``submit_and_poll`` on the same key that pays for its
  enrichment; Apollo replaces only the resolver, so an Apollo-only install can enrich
  but cannot discover.
- ``lookup`` is the step the cycle drives: ``buy_address`` resolves the free sources
  first and runs the finder only if they miss, ``check_lookup`` polls anything left
  in flight.

**This is deliberately not part of discovery, and no longer part of a mail package.**
It used to live under ``emails/`` because a resolved address existed to be written to;
that is no longer true, and the coupling it implied — resolve only what there is send
headroom for — was the single line that made a mailbox-less install produce nothing.
An address is now just a column in the export: nice to have, never a precondition, and
a lead with none still exports with its ``reason``.
"""
