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
from collections.abc import Callable, Iterable
from typing import Protocol, Never, Any, final
from typing import type_check_only  # NOQA: Needed

from rich.text import Text

from .arguments import Cardinal, Option, Flag


@type_check_only
class Triggerable(Protocol):
    def __trigger__(self, *unused: Never, **options: Any) -> None: ...

class CommandException(Exception):
    message: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...
    def __trigger__(self, *unused: Never, **options: Any) -> None: ...

class InvalidFormatError(CommandException):
    message: str | Text
    token: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            token: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...

class UnrecognizedOptionError(CommandException):
    message: str | Text
    input: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            input: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...

class ParameterWrongUsageError(CommandException):
    message: str | Text
    switcher: Option | Flag
    input: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            switcher: Option | Flag,
            input: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...

class UnknownCommandError(CommandException):
    message: str | Text
    input: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            input: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...

class UnexpectedPositionalArgumentError(CommandException):
    message: str | Text
    offset: int
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            offset: int,
            *,
            hint: str | Text = ...
    ) -> None: ...

class GroupConflictError(CommandException):
    message: str | Text
    group: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            group: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...

class DuplicateArgumentError(CommandException):
    message: str | Text
    input: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            input: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...

class StandaloneUsageError(CommandException):
    message: str | Text
    input: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            input: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...

class UnparsedTokensError(CommandException):
    message: str | Text
    remaining: tuple[str, ...]
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            remaining: Iterable[str],
            *,
            hint: str | Text = ...
    ) -> None: ...

class MissingArgumentError(CommandException):
    message: str | Text
    input: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            input: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...

class MissingParameterError(CommandException):
    message: str | Text
    subject: str | Text
    expected_min: int
    got: int
    hint: str | Text | None
    offset: int | None
    def __init__(
            self,
            message: str | Text,
            subject: str | Text,
            *,
            expected_min: int = ...,
            got: int = ...,
            hint: str | Text = ...,
            offset: int | None = ...
    ) -> None: ...

class ArityMismatchError(CommandException):
    message: str | Text
    subject: str | Text
    expected: int
    got: int
    hint: str | Text | None
    offset: int | None
    def __init__(
            self,
            message: str | Text,
            subject: str | Text,
            *,
            expected: int,
            got: int,
            hint: str | Text = ...,
            offset: int | None = ...
    ) -> None: ...

class ParameterConversionError(CommandException):
    message: str | Text
    subject: str | Text
    value: str | Text
    hint: str | Text | None
    offset: int | None
    def __init__(
            self,
            message: str | Text,
            subject: str | Text,
            value: str | Text,
            *,
            hint: str | Text = ...,
            offset: int | None = ...
    ) -> None: ...

class CommandWarning(Warning):
    message: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...
    def __trigger__(self, *unused: Never, **options: Any) -> None: ...

class DeprecatedArgumentWarning(CommandWarning):
    message: str | Text
    argument: Cardinal | Option | Flag
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            argument: Cardinal | Option | Flag,
            *,
            hint: str | Text = ...
    ) -> None: ...

class ParameterCoercionWarning(CommandWarning):
    message: str | Text
    subject: str | Text
    value: str | Text
    hint: str | Text | None
    def __init__(
            self,
            message: str | Text,
            subject: str | Text,
            value: str | Text,
            *,
            hint: str | Text = ...
    ) -> None: ...

@final
class CommandExit(ExceptionGroup[CommandException]):
    def __trigger__(self, *unused: Never, **options: Any) -> None: ...

def trigger(x: Triggerable, /, **options: Any) -> None: ...
def customize(x: type[Triggerable], /) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...
