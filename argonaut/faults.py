import copy
import inspect
import sys
import warnings
from abc import ABC, abstractmethod
from types import MappingProxyType

from rich.console import Console
from rich.text import Text

from .internals import Unset

console = Console(stderr=True, style="red")


class CommandException(ABC, Exception):
    def __init__(self, message=Unset, /, **options):
        assert isinstance(message, str | Unset)
        self.message = message
        self.options = MappingProxyType(options)

    @abstractmethod
    def __trigger__(self) -> None:
        raise self from None

    def __replace__(self, *unused, **overrides):
        assert not unused, "unused arguments are not allowed"
        return type(self)(self.message, **{**self.options, **overrides})


class MalformedTokenError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class UnknownModifierError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class FlagTakesNoParamError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class UnknownCommandError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class UnknownSubcommandError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class TooManyPositionalsError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class DuplicateModifierError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class InlineParamRequiredError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()

class MissingParamError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()

class AtLeastOneParamRequiredError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()

class NotEnoughParamsError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()

class TooManyInlineParamsError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()

class InvalidParamError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()

class DisallowedParamError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class UncastableParamError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class InvalidChoiceError(CommandException):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class CommandWarning(ABC, Warning):
    def __init__(self, message=Unset, /, **options):
        assert isinstance(message, str | Unset)
        self.message = message
        self.options = MappingProxyType(options)

    def __trigger__(self) -> None:
        warnings.warn(self, stacklevel=len(inspect.stack()))

    def __replace__(self, *unused, **overrides):
        assert not unused, "unused arguments are not allowed"
        return type(self)(self.message, **{**self.options, **overrides})


class EmptyInlineParamWarning(CommandWarning):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class DeprecatedArgumentWarning(CommandWarning):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


class ExternalConverterWarning(CommandWarning):
    def __trigger__(self) -> None:
        if not self.options.get("shell", False):
            return super().__trigger__()


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
