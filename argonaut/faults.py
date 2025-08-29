"""
Argonaut faults (errors and warnings) and rendering.

Scope
- FaultCode: canonical, stable numeric identifiers for all user-facing issues
  (errors and warnings). Codes are grouped by domain to keep copy consistent
  and make logs/searches predictable.
- CommandException / CommandWarning: base types that carry message + options and
  know how to render themselves in a friendly, lowercased, and actionable way.
- trigger(): central entry point to surface any fault (respecting shell/deferred/fancy/colorful).
- getdoc(): optional description lookup for a code from the host application.

UX goals
- Position-first messages: every message includes the ordinal position so users
  can learn by trying (“from third position”, etc.).
- Soft but technical language: short titles, one-sentence bodies, a single clear hint.
- Lowercased tone with readable styling (configurable via __styles__ in __main__).

Integration
- CLI code collects faults during parsing/handling and calls trigger(fault, **ctx).
- In non-shell mode, exceptions are raised; in shell mode, they are rendered via rich.
"""
import copy
import inspect
import sys
import warnings
from abc import ABC
from collections import defaultdict
from enum import IntEnum
from types import MappingProxyType

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from .utils import Unset

console = Console(stderr=True)


class FaultCode(IntEnum):
    """
    canonical fault codes used across the cli (stable identifiers).

    this enumeration is based on the Seralix Fault Codes convention:
    - numeric ranges encode domains (routing, switches, positionals, delegated, warnings).
    - spacing leaves room for future additions without reshuffling existing codes.
    - normalize() allows host remapping to custom labels while keeping code-stability.

    grouping (by high-level domain)
    - routing (1110x/1110y)
      • UNKNOWN_COMMAND, UNKNOWN_SUBCOMMAND
    - switches (options/flags) (1111x/1112x/1114x)
      • MALFORMED_TOKEN, UNKNOWN_SWITCH, FLAG_ASSIGNMENT, MISSING_INLINE_VALUE,
        DUPLICATED_SWITCH, STANDALONE_SWITCH, OPTION_VALUE_REQUIRED,
        INLINE_EXTRA_VALUES, AT_LEAST_ONE_VALUE_REQUIRED, NOT_ENOUGH_VALUES,
        EMPTY_VALUE, INVALID_CHOICE, MISSING_CARDINALS, UNPARSED_TOKENS
    - positionals (cardinals) (11121)
      • UNEXPECTED_CARDINAL
    - delegated errors/warnings (11131 / 12131)
      • DELEGATED_ERROR, DELEGATED_WARNING
    - deprecations (12112)
      • DEPRECATED_ARGUMENT

    rationale
    - codes are discoverable (searchable in logs and docs) and normalized to a string
      via normalize() so hosts can remap them if desired (e.g., to shorter labels).
    """
    # --- routing errors (11xxx) ---
    UNKNOWN_COMMAND             = 11101
    UNKNOWN_SUBCOMMAND          = 11102

    # --- switch/flag/option errors (11xxx) ---
    MALFORMED_TOKEN             = 11111
    UNKNOWN_SWITCH              = 11112
    FLAG_ASSIGNMENT             = 11113
    MISSING_INLINE_VALUE        = 11114
    DUPLICATED_SWITCH           = 11115
    STANDALONE_SWITCH           = 11116
    OPTION_VALUE_REQUIRED       = 11117
    INLINE_EXTRA_VALUES         = 11118
    AT_LEAST_ONE_VALUE_REQUIRED = 11119
    NOT_ENOUGH_VALUES           = 11122
    EMPTY_VALUE                 = 11123
    INVALID_CHOICE              = 11124
    MISSING_CARDINALS           = 11125
    UNPARSED_TOKENS             = 11141

    # --- positional/cardinal errors (11xxx) ---
    UNEXPECTED_CARDINAL         = 11121

    # --- delegated errors (11xxx) ---
    DELEGATED_ERROR             = 11131

    # --- warnings (12xxx) ---
    EMPTY_INLINE_VALUE          = 12111
    DEPRECATED_ARGUMENT         = 12112
    DELEGATED_WARNING           = 12131

    def normalize(self):
        """
        return a host-normalized string for this code.

        the host application can provide a __codes__ mapping in __main__
        to override numeric ids with friendlier labels. when no mapping
        is present, the numeric value is returned as a string.
        """
        return str(getattr(__import__("__main__"), "__codes__", {}).get(self, self.value))


class CommandException(Exception):
    def __init__(self, message=Unset, /, **options):
        assert isinstance(message, str | Unset)
        self.message = message
        self.options = MappingProxyType(options)

    def __rich__(self):
        main = __import__("__main__")

        styles = defaultdict(str, {
            # header parts
            "prog-name": "bold #E6E6F0",  # near-white program name
            "code": "bold #00E5FF",  # neon cyan fault code
            "error-title": "bold #FF4DA6",  # friendly pinky title

            # body
            "error-message": "#C8C8D0",  # soft light gray message
            "hint-arrow": "#9CE19C dim",  # gentle green arrow
            "hint": "italic #9CE19C",  # gentle green hint text
        } | getattr(main, "__styles__", {}))

        def styler(style):
            if "deprecated" in style and not self.options["colorful"]:
                return "strike"
            return styles[style] if self.options["colorful"] else ""

        def text(fragment, style=""):
            if not fragment:
                return Text("")
            if not self.options["colorful"]:
                return Text(str(fragment))
            if isinstance(fragment, Text):
                return fragment
            return Text(str(fragment), style)

        width = console.width - 4 * self.options["fancy"]

        prog = text(getattr(main, "__prog__", self.options["tool"].root.name), styler("prog-name"))

        header = Text.assemble(
            "[ ",
            prog,
            " — ",
            text(self.options["code"].normalize(), styler("code")),
            " | ",
            text(self.options["title"].title(), styler("error-title")),
            " ]"
        )
        message = text(self.message, styler("error-message"))
        hint = Text.assemble(text(" → ", styler("hint-arrow")) , text(self.options["hint"], styler("hint")))

        if self.options["fancy"]:
            try:
                width = int(width * self.options["ratio"])
            except KeyError:
                width = None
            return Panel(Group(message, hint), title=header, title_align="left", width=width)


        return Group(header, message, hint)

    def __trigger__(self) -> None:
        if not self.options["shell"]:
            raise self from None
        console.print(self)
        if self.options["deferred"]:
            return
        sys.exit(1)

    def __replace__(self, *unused, **overrides):
        assert not unused, "unused arguments are not allowed"
        return type(self)(self.message, **{**self.options, **overrides})


class MalformedTokenError(CommandException): ...
class UnknownSwitchError(CommandException): ...
class FlagAssignmentError(CommandException): ...
class UnknownCommandError(CommandException): ...
class UnknownSubcommandError(CommandException): ...
class UnexpectedCardinalError(CommandException): ...
class DuplicatedSwitchError(CommandException): ...
class MissingInlineValueError(CommandException): ...
class OptionValueRequiredError(CommandException): ...
class InlineExtraValuesError(CommandException): ...
class AtLeastOneValueRequiredError(CommandException): ...
class NotEnoughValuesError(CommandException): ...
class DelegatedCommandError(CommandException): ...
class EmptyValueError(CommandException): ...
class InvalidChoiceError(CommandException): ...
class StandaloneSwitchError(CommandException): ...
class MissingCardinalsError(CommandException): ...
class UnparsedTokensError(CommandException): ...


class CommandWarning(ABC, Warning):
    def __init__(self, message=Unset, /, **options):
        assert isinstance(message, str | Unset)
        self.message = message
        self.options = MappingProxyType(options)

    def __rich__(self):
        main = __import__("__main__")

        styles = defaultdict(str, {
            # header parts
            "prog-name": "bold #E6E6F0",  # near-white program name
            "code": "bold #FFB400",  # amber fault code for warnings
            "warning-title": "bold #FFC2E0",  # softer pinky title for warnings

            # body
            "warning-message": "#D6D6DE",  # slightly lighter gray body
            "hint-arrow": "#B8EFAF dim",  # softer green arrow
            "hint": "italic #B8EFAF",  # softer green hint text
        } | getattr(main, "__styles__", {}))

        def styler(style):
            if "deprecated" in style and not self.options["colorful"]:
                return "strike"
            return styles[style] if self.options["colorful"] else ""

        def text(fragment, style=""):
            if not fragment:
                return Text("")
            if not self.options["colorful"]:
                return Text(str(fragment))
            if isinstance(fragment, Text):
                return fragment
            return Text(str(fragment), style)

        width = console.width - 4 * self.options["fancy"]

        prog = text(getattr(main, "__prog__", self.options["tool"].root.name), styler("prog-name"))

        header = Text.assemble(
            "[ ",
            prog,
            " — ",
            text(self.options["code"].normalize(), styler("code")),
            " | ",
            text(self.options["title"].title(), styler("warning-title")),
            " ]"
        )
        message = text(self.message, styler("warning-message"))
        hint = Text.assemble(text(" → ", styler("hint-arrow")), text(self.options["hint"], styler("hint")))

        if self.options["fancy"]:
            try:
                width = int(width * self.options["ratio"])
            except KeyError:
                width = None
            return Panel(Group(message, hint), title=header, title_align="left", width=width)

        return Group(header, message, hint)

    def __trigger__(self) -> None:
        if not self.options["shell"]:
            return warnings.warn(self, stacklevel=len(inspect.stack()))
        console.print(self)

    def __replace__(self, *unused, **overrides):
        assert not unused, "positional arguments are not allowed"
        return type(self)(self.message, **{**self.options, **overrides})


class EmptyOptionValueWarning(CommandWarning): ...
class DeprecatedArgumentWarning(CommandWarning): ...

class DelegatedCommandWarning(CommandWarning): ...

class CommandExit(ExceptionGroup[CommandException]):
    def __new__(cls, exceptions, **options):
        return super().__new__(cls, "bad exit", exceptions)

    def __init__(self, exceptions, **options):
        super().__init__("bad exit", tuple(exceptions))
        self.options = MappingProxyType(options)

    def __rich__(self):
        main = __import__("__main__")

        styles = defaultdict(str, {
            # header
            "prog-name": "bold #E6E6F0",  # near-white program name
            "title": "bold #FF4DA6",  # friendly pinky group title (Bad Exit)

            # optional niceties (safe to leave unused)
            "exit-border": "#6B6F7A",  # panel border if you later pass style=...
            "exit-docs": "underline #00E5FF dim",  # for any footer/link you might add
        } | getattr(main, "__styles__", {}))

        def styler(style):
            if "deprecated" in style and not self.options["colorful"]:
                return "strike"
            return styles[style] if self.options["colorful"] else ""

        def text(fragment, style=""):
            if not fragment:
                return Text("")
            if not self.options["colorful"]:
                return Text(str(fragment))
            if isinstance(fragment, Text):
                return fragment
            return Text(str(fragment), style)

        prog = text(getattr(main, "__prog__", self.options["tool"].root.name), styler("prog-name"))

        header = Text.assemble("[ ",prog, " — ", text(self.message.title(), styler("title")), " ]")

        renders = []

        for exception in self.exceptions:
            renders.append(copy.replace(exception, ratio=2/3))

        if self.options["fancy"]:
            return Panel(Group(*renders), title=header, title_align="left")

        return Group(header, *renders)

    def __trigger__(self) -> None:
        if not self.options["shell"]:
            raise self from None
        console.print(self)
        sys.exit(1)

    def __replace__(self, *unused, **overrides):
        assert not unused, "positional arguments are not allowed"
        return type(self)(self.exceptions, **{**self.options, **overrides})


def trigger(fault, /, **options):
    """
    surface a fault with the given runtime options.

    contract
    - fault must provide __trigger__ and __replace__ methods (see base classes).
    - options are merged into the fault via __replace__(**, options) before triggering.
    - in shell mode, rendering happens via rich console; otherwise, exceptions are raised.

    typical options
    - tool, shell, fancy, colorful, deferred, title, code, hint, docs, and any other
      context the reporter may want to show (e.g., input/index/subindex/argument).
    """
    if (
        not hasattr(fault, "__trigger__") or
        not callable(fault.__trigger__) or
        not hasattr(fault, "__replace__") or
        not callable(fault.__replace__)
    ):
        raise TypeError("trigger() argument must have a __trigger__ and __replace__ methods")
    copy.replace(fault, **options).__trigger__()


def getdoc(code, /):
    """
    optional documentation fetch for a fault code.

    lookup
    - the host application may expose a __docs__ mapping in __main__ where keys
      are FaultCode instances and values are short documentation strings.
    - when not found, returns None (renderers treat docs as optional).
    """
    if not isinstance(code, FaultCode):
        return TypeError("getdoc() argument must be an fault-code")
    try:
        return getattr(__import__("__main__"), "__docs__", {})[code]
    except KeyError:
        return None


__all__ = (
    "CommandException",
    "MalformedTokenError",
    "UnknownSwitchError",
    "FlagAssignmentError",
    "UnknownCommandError",
    "UnknownSubcommandError",
    "UnexpectedCardinalError",
    "DuplicatedSwitchError",
    "MissingInlineValueError",
    "OptionValueRequiredError",
    "InlineExtraValuesError",
    "AtLeastOneValueRequiredError",
    "NotEnoughValuesError",
    "DelegatedCommandError",
    "EmptyValueError",
    "InvalidChoiceError",
    "StandaloneSwitchError",
    "MissingCardinalsError",
    "UnparsedTokensError",
    "CommandWarning",
    "EmptyOptionValueWarning",
    "DeprecatedArgumentWarning",
    "DelegatedCommandWarning",
    "CommandExit",
    "FaultCode",
    "trigger",
    "getdoc",
)
