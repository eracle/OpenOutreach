"""Reusable step-by-step prompt wizard with Ctrl+B-to-go-back support.

Navigation: Ctrl+B goes back one step, Ctrl+C cancels the wizard,
Ctrl+D skips optional fields (cancels on required ones).
Each question type implements ``_prompt()`` which returns the raw user
input, the sentinel ``_BACK``, or ``None`` (cancel).  The base class
``ask()`` handles navigation, EOF handling, and cleaning uniformly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import questionary
from prompt_toolkit.key_binding import KeyBindings

# -- Sentinels & shared state ------------------------------------------------

_BACK = "__BACK__"
_CONTROLS = "Ctrl+B: back | Ctrl+D: skip optional | Ctrl+C: cancel"

# -- Key bindings (shared by all inline question types) -----------------------

_kb = KeyBindings()


@_kb.add("c-b")
def _go_back(event):
    event.app.exit(result=_BACK)


# -- Validators ---------------------------------------------------------------

def _required(val: str) -> bool | str:
    return True if val.strip() else "This field is required."


def _integer(val: str) -> bool | str:
    try:
        int(val)
        return True
    except ValueError:
        return "Please enter a valid integer."


# -- Helpers ------------------------------------------------------------------

def _clear():
    os.system("clear" if os.name != "nt" else "cls")


# -- Question types -----------------------------------------------------------


@dataclass
class Question:
    """Base for all question types.

    Subclasses implement ``_prompt(default, answers)`` and return the raw
    user input, ``_BACK`` to go back, or ``None`` to cancel.
    """

    key: str
    message: str
    default: str | int | bool = ""
    required: bool = True

    def ask(self, default, *, answers: dict | None = None):
        """Prompt the user.  Returns cleaned answer, ``_BACK``, or ``None``.

        Ctrl-D (EOF) skips optional fields and cancels required ones, so
        every question type behaves the same whether the underlying
        prompt is questionary or raw prompt_toolkit.
        """
        try:
            raw = self._prompt(default, answers=answers)
        except EOFError:
            return None if self.required else self._empty_value(default)
        if raw is None or raw == _BACK:
            return raw
        return self._clean(raw)

    def _prompt(self, default, *, answers: dict | None = None):
        raise NotImplementedError

    def _clean(self, raw):
        return raw.strip() if isinstance(raw, str) else raw

    def _empty_value(self, default):
        """Value returned when Ctrl-D skips an optional question."""
        return ""

    @property
    def _instruction(self) -> str | None:
        return "(optional)" if not self.required else None


class Text(Question):
    def _prompt(self, default, **_):
        return questionary.text(
            self.message, default=default, key_bindings=_kb,
            validate=_required if self.required else None,
            instruction=self._instruction,
        ).ask()


class Password(Question):
    def _prompt(self, default, **_):
        return questionary.password(
            self.message, default=default, key_bindings=_kb,
            validate=_required if self.required else None,
        ).ask()


# NOTE: any Question subclass that overrides a dataclass field default
# (``default``/``required``) MUST be re-decorated with ``@dataclass`` — otherwise
# it inherits Question's generated __init__ and the overrides are silently ignored
# (e.g. a Confirm would come out required=True).
@dataclass
class Confirm(Question):
    """Yes/no confirmation prompt.

    When ``required=True`` the user **must** answer yes; answering no
    cancels the wizard (returns ``None``).
    """

    default: bool = True
    required: bool = False

    def _prompt(self, default, **_):
        while True:
            result = questionary.confirm(
                self.message,
                default=default if isinstance(default, bool) else self.default,
            ).ask()
            if result is None:
                return None
            if not self.required or result:
                return result
            questionary.print("  You must accept to continue.", style="fg:red")

    def _empty_value(self, default):
        return bool(default) if isinstance(default, bool) else self.default


@dataclass
class IntText(Question):
    """Integer text input."""

    default: int = 0
    required: bool = False

    def _prompt(self, default, **_):
        return questionary.text(
            self.message, default=str(default), key_bindings=_kb,
            validate=_integer,
        ).ask()

    def _clean(self, raw):
        return int(raw)

    def _empty_value(self, default):
        try:
            return int(default)
        except (TypeError, ValueError):
            return self.default


class Autocomplete(Question):
    """Type-to-filter searchable select with lazily resolved choices."""

    def __init__(self, key: str, message: str, *, resolver: callable, default: str = ""):
        super().__init__(key=key, message=message, default=default)
        self.resolver = resolver

    def _prompt(self, default, *, answers: dict | None = None):
        choices = self.resolver(answers or {})
        if not choices:
            return questionary.text(
                self.message, default=default, key_bindings=_kb,
            ).ask()
        return questionary.autocomplete(
            self.message, choices=choices, default=default, key_bindings=_kb,
            validate=lambda val: val in choices or "Please select a valid option",
        ).ask()


class MultilineText(Question):
    """Inline multiline text input (Enter for newline, Ctrl+D to submit).

    Required fields prompt directly.
    Optional fields first ask a yes/no gate — answer "no" to skip.
    """

    def _prompt(self, default, **_):
        if self.required:
            return self._inline_prompt(default)
        proceed = questionary.confirm(
            self.message, default=bool(default),
        ).ask()
        if proceed is None:
            return proceed
        if not proceed:
            return ""
        return self._inline_prompt(default)

    def _inline_prompt(self, default):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings as PTKeyBindings

        bindings = PTKeyBindings()

        @bindings.add("c-b")
        def _back(event):
            event.app.exit(result=_BACK)

        @bindings.add("c-d", eager=True)
        def _submit(event):
            event.current_buffer.validate_and_handle()

        session = PromptSession(key_bindings=bindings)
        hint = "(optional) " if not self.required else ""
        while True:
            try:
                text = session.prompt(
                    f"? {self.message} {hint}(Ctrl+D to submit):\n",
                    default=default or "",
                    multiline=True,
                )
            except KeyboardInterrupt:
                return None
            if text == _BACK:
                return _BACK
            text = text.strip()
            if self.required and not text:
                questionary.print("  This field is required.", style="fg:red")
                continue
            return text


# -- Wizard runner ------------------------------------------------------------


def ask(questions: list[Question]) -> dict | None:
    """Walk through *questions* collecting answers.

    Returns a ``dict`` of answers keyed by ``question.key``,
    or ``None`` if the user cancels (Ctrl+C).
    """
    answers: dict = {}
    i = 0
    while i < len(questions):
        q = questions[i]
        default = answers.get(q.key, q.default)

        _clear()
        print(f"  {_CONTROLS}  ({i + 1}/{len(questions)})\n")

        result = q.ask(default, answers=answers)

        if result is None:
            return None
        if result == _BACK:
            i = max(0, i - 1)
            continue

        answers[q.key] = result
        i += 1

    return answers
