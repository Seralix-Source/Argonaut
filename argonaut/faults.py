import copy
import inspect
import sys
import warnings
from abc import ABC, abstractmethod
from collections.abc import Iterable
from types import MappingProxyType

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .internals import Unset

console = Console(stderr=True)


class CommandException(Exception):
    def __init__(self, message=Unset, /, **options):
        assert isinstance(message, str | Unset)
        self.message = message
        self.options = MappingProxyType(options)

    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            raise self from None
        command = self.options.get("command")
        colorful = self.options.get("colorful", False)
        fancy = self.options.get("fancy", False)
        path = " ".join(cmd.name for cmd in command.rootpath) if command else "argonaut"  # type: ignore[attr-defined]
        message = self.message if isinstance(self.message, str) else type(self).__name__
        hint = self.options.get("hint")

        if fancy:
            body = Text()
            # primary line
            body.append(f"{path}: error: {message}\n", style="red" if colorful else "")
            # optional hint line
            if hint:
                body.append("  ", style="")
                body.append("↳ ", style="cyan" if colorful else "")
                body.append(f"hint: {hint}\n", style="white" if colorful else "")
            console.print(Panel(
                body,
                border_style="red" if colorful else "",
                expand=False
            ))
        else:
            console.print(Text(f"{path}: error: {message}", style="red" if colorful else ""))
            if hint:
                console.print(Text(f"  ↳ hint: {hint}", style="white" if colorful else ""))

        sys.exit(1)

    def __replace__(self, *unused, **overrides):
        assert not unused, "unused arguments are not allowed"
        return type(self)(self.message, **{**self.options, **overrides})


class MalformedTokenError(CommandException): ...
class UnknownModifierError(CommandException): ...
class FlagTakesNoParamError(CommandException): ...
class UnknownCommandError(CommandException): ...
class UnknownSubcommandError(CommandException): ...
class TooManyPositionalsError(CommandException): ...
class DuplicateModifierError(CommandException): ...
class InlineParamRequiredError(CommandException): ...
class MissingParamError(CommandException): ...
class AtLeastOneParamRequiredError(CommandException): ...
class NotEnoughParamsError(CommandException): ...
class TooManyInlineParamsError(CommandException): ...
class InvalidParamError(CommandException): ...
class DisallowedParamError(CommandException): ...
class UncastableParamError(CommandException): ...
class InvalidChoiceError(CommandException): ...
class ConflictingGroupError(CommandException): ...
class StandaloneOnlyError(CommandException): ...
class UnparsedInputError(CommandException): ...


class CommandWarning(ABC, Warning):
    def __init__(self, message=Unset, /, **options):
        assert isinstance(message, str | Unset)
        self.message = message
        self.options = MappingProxyType(options)

    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return warnings.warn(self, stacklevel=len(inspect.stack()))
        command = self.options.get("command")
        colorful = self.options.get("colorful", False)
        fancy = self.options.get("fancy", False)
        path = " ".join(cmd.name for cmd in command.rootpath) if command else "argonaut"  # type: ignore[attr-defined]
        message = self.message if isinstance(self.message, str) else type(self).__name__
        hint = self.options.get("hint")

        if fancy:
            body = Text()
            # primary line in yellow
            body.append(f"{path}: warning: {message}\n", style="yellow" if colorful else "")
            # optional hint line
            if hint:
                body.append("  ", style="")
                body.append("↳ ", style="cyan" if colorful else "")
                body.append(f"hint: {hint}\n", style="white" if colorful else "")
            console.print(Panel(
                body,
                border_style="yellow" if colorful else "",
                expand=False
            ))
        else:
            console.print(Text(f"{path}: warning: {message}", style="yellow" if colorful else ""))
            if hint:
                console.print(Text(f"  ↳ hint: {hint}", style="white" if colorful else ""))

    def __replace__(self, *unused, **overrides):
        assert not unused, "unused arguments are not allowed"
        return type(self)(self.message, **{**self.options, **overrides})


class EmptyInlineParamWarning(CommandWarning): ...
class DeprecatedArgumentWarning(CommandWarning): ...
class ExternalConverterWarning(CommandWarning): ...


class CommandExit(ExceptionGroup[CommandException]):
    def __new__(cls, exceptions, **options):
        return super().__new__(cls, "FAILURE", exceptions)

    def __init__(self, exceptions, **options):
        assert isinstance(exceptions, Iterable), "all exceptions must be exceptions"
        self.options = MappingProxyType(options)
        super().__init__("FAILURE", tuple(exceptions))

    def __trigger__(self) -> None:  # CommandExit
        if not self.options.get("shell", False):
            raise self from None  # Base doesn't have trigger
        command = self.options.get("command")
        colorful = self.options.get("colorful", False)
        fancy = self.options.get("fancy", False)
        try:
            # header
            path = " ".join(cmd.name for cmd in command.rootpath) if command else None  # type: ignore[attr-defined]
            count = len(self.exceptions)
            header = f"{(path or 'argonaut')}: error: {count} error{'s' if count != 1 else ''}"

            limit = 10  # show up to 10 errors, then summarize
            if fancy:
                body = Text()
                shown = 0
                for idx, exc in enumerate(self.exceptions):
                    if idx == limit:
                        remaining = count - limit
                        summary = f"... and {remaining} more error{'s' if remaining != 1 else ''}"
                        body.append(summary + "\n", style="red" if colorful else "")
                        break
                    message = exc.message if isinstance(exc.message, str) else type(exc).__name__
                    body.append(f"- {message}\n", style="red" if colorful else "")
                    hint = exc.options.get("hint")
                    if hint:
                        # arrow cyan for contrast with red; hint text in white (friendly)
                        body.append("  ", style="")
                        body.append(f" ↳ hint: {hint}\n", style="white" if colorful else "")
                    shown += 1

                body.rstrip()
                console.print(Panel(
                    body,
                    title=Text(header, style="red" if colorful else ""),
                    border_style="red" if colorful else "",
                    expand=False
                ))
            else:
                console.print(Text(header, style="red" if colorful else ""))
                for idx, exc in enumerate(self.exceptions):
                    if idx == limit:
                        remaining = count - limit
                        summary = f"... and {remaining} more error{'s' if remaining != 1 else ''}"
                        console.print(Text(summary, style="red" if colorful else ""))
                        break
                    message = exc.message if isinstance(exc.message, str) else type(exc).__name__
                    console.print(Text(f"- {message}", style="red" if colorful else ""))
                    hint = exc.options.get("hint")
                    if hint:
                        console.print(Text(f" ↳ hint: {hint}", style="white" if colorful else ""))
        finally:
            # conventional "command line usage error" exit code
            sys.exit(2)

    def __replace__(self, *unused, **overrides):
        return type(self)(self.exceptions, **{**self.options, **overrides})


def trigger(fault, /, **options):
    if (
        not hasattr(fault, "__trigger__") or
        not hasattr(fault, "__replace__") or
        not callable(fault.__trigger__) or
        not callable(fault.__replace__)
    ):
        raise TypeError("trigger() argument must have a __trigger__ and __replace__ methods")
    copy.replace(fault, **options).__trigger__()


__all__ = (
    *(name for name, object in globals().items() if hasattr(object, "__trigger__")),
    "trigger",
)
