# openoutreach/settings.py
"""Django settings for the orchestrator: one registry hosting both children's apps.

`openoutfind` and `cold_outreach` are Django projects in their own right, and each keeps
its own settings module — `uvx --from openoutfind outfind find 10` runs with nothing from
this package anywhere near it. They are also *reusable apps*, and this is a third host for
them: one `INSTALLED_APPS`, one SQLite file, one migration graph, one process — so a lead
the finder wrote and the mail the sender sent it are rows in the same store, and one
`migrate` brings the whole install current.

**What each child requires of a host lives in that child's `defaults.py`** and is splatted
here, so a name the apps start reading arrives from one definition rather than drifting
between three settings modules.

**There is no web surface.** No URLconf, no Admin, no sessions, no templates — the verbs
are `find`, `send`, `status` and `run`, and the config surface is the wizard. `SECRET_KEY`
exists because Django insists on one; naming that in the value is more honest than
generating a secret nobody uses.

**This project has exactly one app and one model** — `openoutreach.config.SiteConfig`, the
answers a person gave. It is here because this is the program with a person in front of
it: the children are agent-first and read their configuration from the environment on
every run, keeping none of it, so somebody has to remember what was typed, and it is not
them. Nothing else belongs here — no pipeline model, no management command. That work
belongs in a child repo, released, then re-pinned.
"""
from __future__ import annotations

import os
from pathlib import Path

from openoutfind import defaults as find_defaults

# Before `django.setup()`, which is why it is a call here and not a name in a dict.
find_defaults.allow_async_unsafe()

ROOT_DIR = Path(__file__).resolve().parent.parent

BASE_DIR = ROOT_DIR


def state_dir(root: Path) -> Path:
    """Where the operator's own files live: the checkout, or `~/.openoutreach` installed.

    Both children make the same choice for the same reason — from a wheel the package
    directory is inside site-packages, which is no place for a CRM, a mail history or a
    model cache, and may not be writable.
    """
    return root if (root / "manage.py").exists() else Path.home() / ".openoutreach"


STATE_DIR = state_dir(ROOT_DIR)

# `--db PATH` sets OPENOUTREACH_DB. One file: the finder's leads and the sender's mail log
# are rows in the same store, which is the whole point of hosting both app sets here.
DATABASE_PATH = Path(os.environ.get("OPENOUTREACH_DB") or STATE_DIR / "data" / "db.sqlite3").expanduser()
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

# The sender resolves its own root at import time, and left alone it would answer
# `~/.openoutsend` — a second home appearing behind the operator's back for prompt lines
# nobody would think to look for. Set before `cold_outreach.defaults` is imported below,
# which is why that import is not at the top of the file.
os.environ.setdefault("OUTSEND_HOME", str(STATE_DIR))

from cold_outreach import defaults as send_defaults  # noqa: E402

# Spelled out rather than splatted from each child's `defaults.APPS`: this is the list
# that says what one process is, and reading it should not mean opening two other
# packages. The children's own lists stay the source of truth for *order* — both are in
# dependency order, and the sender's `emails` must follow its `leads`.
INSTALLED_APPS = [
    # `django.contrib.sites` is the finder's (`setup_crm` seeds Site 1); `auth` is the
    # sender's (`emails.Message` points at `AUTH_USER_MODEL`) and holds the one operator
    # both children read.
    "django.contrib.sites",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    # This host's one model: the answers a person gave, which both children then read as
    # their environment. First, because the wizard writes it before either child runs.
    "openoutreach.config.apps.ConfigAppConfig",
    "openoutfind.crm.apps.CrmConfig",
    "openoutfind.core.apps.CoreConfig",
    "cold_outreach.core",
    "cold_outreach.leads",
    "cold_outreach.emails",
]

SECRET_KEY = "openoutreach-has-no-web-surface"

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(DATABASE_PATH),
        # WAL lets `openoutreach status` read while a find or send pass holds a write
        # lock; without it a concurrent read fails with "database is locked".
        "OPTIONS": {
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "transaction_mode": "IMMEDIATE",
        },
    }
}

# Splatted rather than spelled out — see the module docstring. The finder is handed this
# host's database path because this host owns it; the sender reads its own names from the
# environment, `OUTSEND_HOME` included, which is set above.
globals().update(find_defaults.app_settings(STATE_DIR, DATABASE_PATH))
globals().update(send_defaults.app_settings())

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_ID = 1

# Stored UTC, rendered in the operator's own zone wherever a day matters — the sender's
# send cap and sending window both count from *their* midnight, not the server's.
USE_TZ = True
TIME_ZONE = "UTC"
