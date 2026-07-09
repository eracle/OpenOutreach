# openoutreach/core/onboarding_wizard.py
"""Minimal, robust console prompts for onboarding.

First principles
----------------
* **A prompt returns a value, or ``None`` when the user cancels** (Ctrl+C / EOF).
  Callers treat ``None`` as "abort this step" — there is no third state and no
  shared sentinel to leak.
* **Validation loops live *inside* the prompt.** A bad value re-asks the *same*
  field with what you typed still in place; it never throws you back to an
  earlier question. The old design had a cross-question "back" stack and a
  ``Question`` dataclass hierarchy whose overridden field-defaults were silently
  dropped unless every subclass was re-decorated — the source of the
  "can't answer no, loops to the start" bug. Both are gone.
* **No module-level mutable state.** Each call is independent.

These are deliberately thin wrappers over ``questionary``; the ordering and
idempotency of onboarding lives in ``onboarding.py``.
"""
from __future__ import annotations

from typing import Callable

import questionary

Validator = Callable[[str], "bool | str"]


def _error(message: str) -> None:
    questionary.print(f"  {message}", style="fg:red")


def text(
    message: str,
    *,
    default: str = "",
    required: bool = True,
    secret: bool = False,
    validate: Validator | None = None,
) -> str | None:
    """Ask for a line of text. Returns the stripped value, or ``None`` on cancel.

    Empty input re-asks when ``required``; an optional field returns ``""``.
    ``secret=True`` masks the input. ``validate(value) -> True | str`` adds a
    field-specific check whose error string is shown before re-asking.
    """
    prompt = questionary.password if secret else questionary.text
    while True:
        raw = prompt(
            message,
            default=default,
            instruction=None if required else "(optional — Enter to skip)",
        ).ask()
        if raw is None:
            return None
        value = raw.strip()
        if not value:
            if required:
                _error("This field is required.")
                continue
            return ""
        if validate is not None:
            verdict = validate(value)
            if verdict is not True:
                _error(verdict if isinstance(verdict, str) else "Invalid value.")
                continue
        return value


def integer(message: str, *, default: int) -> int | None:
    """Ask for a whole number, re-asking until valid. Returns ``int`` or ``None``."""
    while True:
        raw = questionary.text(message, default=str(default)).ask()
        if raw is None:
            return None
        try:
            return int(raw.strip())
        except ValueError:
            _error("Please enter a whole number.")


def confirm(message: str, *, default: bool) -> bool | None:
    """Yes/no prompt. Returns ``bool`` or ``None`` on cancel."""
    return questionary.confirm(message, default=default).ask()


def multiline(message: str, *, default: str = "", required: bool = True) -> str | None:
    """Multi-line text (Enter inserts a newline, Ctrl+D submits).

    Returns the stripped text, or ``None`` on cancel (Ctrl+C). Required input
    re-asks on empty.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings

    bindings = KeyBindings()

    @bindings.add("c-d", eager=True)
    def _submit(event):
        event.current_buffer.validate_and_handle()

    session = PromptSession(key_bindings=bindings)
    prompt = f"? {message}\n  (Enter = new line · Ctrl+D on its own line = submit)\n> "
    while True:
        try:
            raw = session.prompt(prompt, default=default, multiline=True)
        except (KeyboardInterrupt, EOFError):
            return None
        value = raw.strip()
        if not value and required:
            _error("This field is required.")
            continue
        return value
