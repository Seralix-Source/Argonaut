import copy
from abc import ABC, abstractmethod
from types import MappingProxyType

try:
    from .null import _update_name  # NOQA: Internal
except ImportError:
    from null import _update_name  # NOQA: Internal


class CommandException(ABC, Exception):
    def __init__(self, message, /, **options):
        super().__init__(message)
        self.message = message
        self.options = MappingProxyType(options)

    def __replace__(self, **overrides):
        return type(self)(overrides.get("message", self.message), **{**self.options, **overrides})

    @abstractmethod
    def __trigger__(self):
        raise NotImplementedError("base exception has no trigger")


class CommandWarning(ABC, Warning):
    def __init__(self, message, /, **options):
        super().__init__(message)
        self.message = message
        self.options = MappingProxyType(options)

    def __replace__(self, **overrides):
        return type(self)(overrides.get("message", self.message), **{**self.options, **overrides})

    @abstractmethod
    def __trigger__(self):
        raise NotImplementedError("base warning has no trigger")


def trigger(fault, /, **options):
    if (
            not hasattr(fault, "__trigger__") or
            not callable(fault.__trigger__) or
            not hasattr(fault, "__replace__") or
            not callable(fault.__replace__)
    ):
        raise TypeError("trigger() argument must be a fault")
    copy.replace(fault, **options).__trigger__()


def mutate(fault, /):
    # validate target is a class
    if not isinstance(fault, type):
        raise TypeError("mutate() argument must be a fault class")

    # ensure the class exposes a trigger interface we can replace
    if (
            not hasattr(fault, "__trigger__") or
            not callable(fault.__trigger__) or
            not hasattr(fault, "__replace__") or
            not callable(fault.__replace__) or
            not hasattr(fault, "message") or
            not hasattr(fault, "options")
    ):  # type: ignore[attr-defined]
        raise TypeError("mutate() argument must be a fault class")

    @_update_name("mutate")
    def decorator(triggerer, /):
        if not callable(triggerer):
            raise TypeError("@mutate() must be wrapping a callable")
        try:
            triggerer.__trigger__ = _update_name(lambda self: triggerer(self.message, **self.options), "__trigger__")
        except AttributeError:
            raise RuntimeError(f"unable to mutate {fault.__name__} __trigger__") from None
        return triggerer

    return decorator


__all__ = (
    *(name for name, object in globals().items() if isinstance(object, type) and issubclass(object, CommandException)),
    *(name for name, object in globals().items() if isinstance(object, type) and issubclass(object, CommandWarning)),
    "trigger",
    "mutate"
)
