"""
argonaut.triggers
~~~~~~~~~~~~~~~~~

Lightweight signaling for errors and warnings during command parsing/execution.

What this module provides
- Exceptions and warnings that know how to render themselves in both shell-style
  (colored/fancy Rich output) and non-shell contexts (raise/emit normally).
- trigger(x, **options): a small dispatcher that calls x.__trigger__(**options).
- customize(cls): decorator to replace a Triggerable class’s __trigger__ at runtime.

How rendering works
- In shell mode (options["shell"] is True), exceptions are printed instead of raised,
  using Rich for color/fancy output if options["colorful"] or options["fancy"].
- Outside shell mode, exceptions are raised and warnings are emitted via warnings.warn.
- The following common options are recognized by built-in triggerables:
  • cmd: the current Command instance (used for headers/titles).
  • shell: bool that switches between printing vs raising.
  • colorful: bool to enable colorized output.
  • fancy: bool to render a Panel with a title instead of plain lines.
  • styles: dict[str, str|Style] with optional keys like:
      - "panel-title", "panel-border", "panel-background", "hint"
  • soft: bool that prevents sys.exit in error flows (used for aggregation).

CommandExit aggregation
- When multiple CommandExceptions were captured, they are bundled into CommandExit.
  The trigger receives a "specifics" mapping from each exception to its merged options,
  allowing per-exception rendering. CommandExit respects "soft" and will call sys.exit
  only when soft is False.

Typical usage
- From within a Command, prefer Command.trigger(x, **overrides) so that standard
  shell/fancy/colorful/styles options are merged automatically.

Examples
- Emit a warning (shell or not):
    trigger(DeprecatedArgumentWarning("deprecated", argument=...), shell=True)
- Raise an error (non-shell):
    trigger(InvalidFormatError("bad token", token="--bad"), shell=False)
"""

import functools
import inspect
import sys
import warnings
from typing import final

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .void import void

console = Console(stderr=True)


# Update the name of a callable and return itself to avoid exposing names like "_get_call.<locals>._decorator"
def _update_name(func, newname=None):  # was: callable
    if not isinstance(func, str):
        func.__qualname__ = newname
        func.__name__ = newname
        return func
    return functools.partial(_update_name, newname=func)


def shell(trigger, message, hint, *, options):
    # Base color per kind
    base_color = "red" if trigger == "error" else "yellow"
    title_kind = "ERROR" if trigger == "error" else "WARNING"

    # Resolve command name (uppercased for the title)
    cmd = getattr(options.get("cmd"), "patriarch", None)
    cmd_name = str(getattr(cmd, "name", "<argonaut>")).upper()

    colorful = bool(options.get("colorful"))
    fancy = bool(options.get("fancy"))
    styles = options.get("styles") or {}

    # Build colored or plain message/hint
    if colorful:
        # Use styled prefix
        if cmd and not fancy:
            message = Text.assemble(
                Text(str(cmd.name)),
                f": {trigger}: ",
                Text(str(message)),
                style=base_color
            )
        else:
            message = Text.assemble(
                f"{trigger}: ",
                Text(str(message)),
                style=base_color
            )
        if hint:
            hint = Text.assemble(Text("hint: "), Text(str(hint)), style=styles.get("hint", "cyan"))
    else:
        # Plain strings
        if cmd and not fancy:
            message = f"{cmd.name}: {trigger}: {message}"
        else:
            message = f"{trigger}: {message}"
        if hint:
            hint = f"hint: {hint}"

    if fancy:
        # Title: " [ <CMD-NAME> ERROR|WARNING ] "
        title_text = f" [ {cmd_name} {title_kind} ] "
        title = Text(title_text, style=styles.get("panel-title")) if colorful else title_text

        # Body: single object (Text or str), include hint on a new line if present
        if colorful:
            body = message if not hint else Text.assemble(message, Text("\n"), hint)
        else:
            body = message if not hint else f"{message}\n{hint}"

        # Panel styles (only colorize when colorful is True)
        panel_kwargs = {
            "title": title,
            "title_align": "left",
        }
        if colorful:
            panel_kwargs["border_style"] = styles.get("panel-border", base_color)
            # background can be provided via styles mapping as a style name
            if bg := styles.get("panel-background"):
                panel_kwargs["style"] = bg

        console.print(Panel.fit(body, **panel_kwargs))
    else:
        console.print(message, hint, sep="\n" if bool(hint) else "")


class CommandException(Exception):
    def __init__(self, message, *, hint=void):
        super().__init__(message)
        self.message = message
        self.hint = void.nullify(hint)

    def __trigger__(self, **options):
        if not options.get("shell"):
            raise self from None
        shell("error", self.message, self.hint, options=options)
        if options.get("soft"):
            return
        sys.exit(1)


class InvalidFormatError(CommandException):
    def __init__(self, message, token, *, hint=void):
        super().__init__(message, hint=hint)
        self.token = token


class UnrecognizedOptionError(CommandException):
    def __init__(self, message, input, *, hint=void):
        super().__init__(message, hint=hint)
        self.input = input


class ParameterWrongUsageError(CommandException):
    def __init__(self, message, switcher, input, *, hint=void):
        super().__init__(message, hint=hint)
        self.switcher = switcher
        self.input = input


class UnknownCommandError(CommandException):
    def __init__(self, message, input, *, hint=void):
        super().__init__(message, hint=hint)
        self.input = input


class UnexpectedPositionalArgumentError(CommandException):
    def __init__(self, message, offset, hint=void):
        super().__init__(message, hint=hint)
        self.offset = offset


class GroupConflictError(CommandException):
    def __init__(self, message, group, *, hint=void):
        super().__init__(message, hint=hint)
        self.group = group


class DuplicateArgumentError(CommandException):
    def __init__(self, message, input, *, hint=void):
        super().__init__(message, hint=hint)
        self.input = input


class StandaloneUsageError(CommandException):
    def __init__(self, message, input, *, hint=void):
        super().__init__(message, hint=hint)
        self.input = input


class UnparsedTokensError(CommandException):
    def __init__(self, message, remaining, hint=void):
        super().__init__(message, hint=hint)
        self.remaining = tuple(remaining)


class MissingArgumentError(CommandException):
    def __init__(self, message, input, *, hint=void):
        super().__init__(message, hint=hint)
        self.input = input


class MissingParameterError(CommandException):
    def __init__(self, message, subject, *, expected_min=1, got=0, hint=void, offset=None):
        super().__init__(message, hint=hint)
        self.subject = str(subject)
        self.expected_min = int(expected_min)
        self.got = int(got)
        self.offset = offset


class ArityMismatchError(CommandException):
    def __init__(self, message, subject, *, expected, got, hint=void, offset=None):
        super().__init__(message, hint=hint)
        self.subject = str(subject)
        self.expected = int(expected)
        self.got = int(got)
        self.offset = offset


class ParameterConversionError(CommandException):
    def __init__(self, message, subject, value, *, hint=void, offset=None):
        super().__init__(message, hint=hint)
        self.subject = str(subject)
        self.value = value
        self.offset = offset


class CommandWarning(Warning):
    def __init__(self, message, *, hint=void):
        super().__init__(message)
        self.message = message
        self.hint = void.nullify(hint)

    def __trigger__(self, **options):
        if not options.get("shell"):
            return warnings.warn(self, stacklevel=len(inspect.stack()))
        shell("warning", self.message, self.hint, options=options)


class DeprecatedArgumentWarning(CommandWarning):
    def __init__(self, message, argument, *, hint=void):
        super().__init__(message, hint=hint)
        self.argument = argument


class ParameterCoercionWarning(CommandWarning):
    def __init__(self, message, subject, value, *, hint=void):
        super().__init__(message, hint=hint)
        self.subject = str(subject)
        self.value = value


@final
class CommandExit(ExceptionGroup[CommandException]):
    def __trigger__(self, **options):
        if not options.get("shell"):
            raise self from None
        for exception in self.exceptions:
            trigger(exception, **{**options["specifics"][exception], "soft": True})
        if options.get("soft"):
            return
        sys.exit(1)

    def __init_subclass__(cls, **options):
        raise TypeError("type 'CommandExit' is not an acceptable base type")


# Shortcut for x.__trigger__(**options)
def trigger(x, /, **options):
    """
    Dispatch a triggerable object.

    Parameters
    - x: positional-only. Any object implementing __trigger__(**options).
    - **options: rendering/behavior options forwarded to __trigger__.

    Behavior
    - Validates that x has a callable __trigger__ method and invokes it.
    - Common options include: shell, fancy, colorful, styles, cmd, soft.
    - When used from Command.trigger(...), standard options are pre-merged.

    Errors
    - Raises TypeError if x has no __trigger__ method.
    """
    if not hasattr(x, "__trigger__") or not callable(x.__trigger__):
        raise TypeError("trigger() argument must implement __trigger__ method")
    x.__trigger__(**options)


# For those users that want to inject their own handlers without rewriting the whole command class
def customize(x, /):
    """
    Replace a Triggerable class's __trigger__ with a custom implementation.

    Parameters
    - x: positional-only. A class whose instances implement __trigger__(**options).

    Usage
    - Apply @customize(MyTriggerable) to a function def triggerer(self, **options) -> None.
      The function is installed as MyTriggerable.__trigger__ (preserving the name).

        @customize(MyTrigger)
        def triggerer(self, **options):
            ...  # custom logic

    Returns
    - The decorator that installs the provided function on the target class.

    Errors
    - Raises TypeError if x is not a class or does not implement __trigger__.
    - Raises TypeError if the decorated object is not callable.
    """

    if not isinstance(x, type):
        raise TypeError("customize() argument must be a class")
    elif not hasattr(x, "__trigger__") and not callable(x.__trigger__):
        raise TypeError("customize() argument must implement __trigger__ method")

    @_update_name("customize")
    def decorator(triggerer):
        if not callable(triggerer):
            raise TypeError("@customize() must be decorating a callable")
        x.__trigger__ = _update_name(lambda self, **options: triggerer(self, **options), "__trigger__")
        return triggerer
    return decorator


__all__ = (
    "CommandException",
    "InvalidFormatError",
    "UnrecognizedOptionError",
    "ParameterWrongUsageError",
    "UnknownCommandError",
    "UnexpectedPositionalArgumentError",
    "GroupConflictError",
    "DuplicateArgumentError",
    "StandaloneUsageError",
    "UnparsedTokensError",
    "MissingArgumentError",
    "MissingParameterError",
    "ArityMismatchError",
    "ParameterConversionError",

    "CommandWarning",
    "DeprecatedArgumentWarning",
    "ParameterCoercionWarning",

    "CommandExit",

    "trigger",
    "customize"
)
