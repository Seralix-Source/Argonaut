# Public type stubs for argument specifications.
# Keep this file focused on the public surface:
# - Do not leak internal helpers (e.g., _process_* / __type__ / null).
# - Defaults are shown as "= ..." to indicate optional parameters in stubs.
# - Comments here document semantics that the runtime guarantees.

from collections.abc import Callable, Iterable, Iterator
from typing import Protocol, Literal, Self, Any, overload
from typing import type_check_only  # NOQA: Needed


# Decorator retrieval protocols (type-only)
# Hint: these let type-checkers understand that a decorator instance can yield
# the underlying spec object. Align method names with your runtime hooks.
@type_check_only
class SupportsOperand[_T](Protocol):
    def __operand__(self) -> Operand[_T]: ...

@type_check_only
class SupportsOption[_T](Protocol):
    # NOTE: If runtime exposes __option__, consider renaming for accuracy.
    def __operand__(self) -> Option[_T]: ...

@type_check_only
class SupportsSwitch(Protocol):
    # NOTE: If runtime exposes __switch__, consider renaming for accuracy.
    def __operand__(self) -> Switch: ...


# Operand: positional arguments
# - Accepts Ellipsis only here to mean “consume the remainder”.
# - __call__ arity is specialized at runtime based on `nargs`.
class Operand[_T]:
    # Frozen, read-only attributes at runtime
    metavar: str | None
    type: Callable[[str], _T]
    nargs: Literal["?", "*", "+"] | int | ellipsis | None  # Ellipsis → remainder (operands only)
    default: _T  # Do not access directly (internal sentinel may be used at runtime)
    choices: tuple[_T, ...] | frozenset[_T]
    group: str
    descr: str | None
    nowait: bool
    hidden: bool
    deprecated: bool

    # Constructor overloads
    @overload
    def __new__(
            cls,
            metavar: str = ...,
            /,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            default: _T = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Operand[_T]: ...
    @overload
    def __new__(
            cls,
            *,
            type: Callable[[str], _T] = ...,
            nargs: Literal["..."] | ellipsis,  # “consume remainder”
            default: _T = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Operand[_T]: ...

    # Runtime provides an arity-specialized __call__ (per `nargs`)
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...

    # Decorator plumbing: returns the spec itself
    def __operand__(self) -> Self: ...

    # Diagnostics and Rich integration (help/pretty)
    def __repr__(self) -> str: ...
    def __rich_repr__(self) -> Iterator[tuple[str, Any]]: ...

    # Single-assignment; returns func for decorator style
    def callback(self, callback: Callable[..., Any], /) -> Callable[..., Any]: ...


# Option: named, value-bearing arguments
# - Typical `nargs`: None | "?" | "*" | "+" | int
# - If `explicit` is True, only attached forms are allowed (e.g., --opt=value).
class Option[_T]:
    # Frozen, read-only attributes at runtime
    names: frozenset[str]  # Set of aliases (short/long), order not guaranteed
    metavar: str | None
    type: Callable[[str], _T]
    nargs: Literal["?", "*", "+"] | int | None
    default: _T  # Do not access directly (internal sentinel may be used at runtime)
    choices: tuple[_T, ...] | frozenset[_T]
    group: str
    descr: str | None
    explicit: bool
    helper: bool
    standalone: bool
    terminator: bool
    nowait: bool
    hidden: bool
    deprecated: bool

    # Constructor overloads (helper wiring: helper→standalone/terminator, terminator→nowait)
    @overload
    def __new__(
            cls,
            *names: str,
            metavar: str = ...,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            default: _T = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str = ...,
            explicit: bool = ...,
            helper: Literal[False] = ...,
            standalone: bool = ...,
            terminator: bool = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Option[_T]: ...
    @overload
    def __new__(
            cls,
            *names: str,
            metavar: str = ...,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            default: _T = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str = ...,
            explicit: bool = ...,
            helper: Literal[True],
            standalone: bool = ...,
            terminator: bool = ...,
            nowait: bool = ...,
            deprecated: bool = ...  # Not recommended with helper
    ) -> Option[_T]: ...

    # Runtime provides an arity-specialized __call__ (per `nargs`)
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...

    # Decorator plumbing: returns the spec itself
    def __option__(self) -> Self: ...

    # Diagnostics and Rich integration (help/pretty)
    def __repr__(self) -> str: ...
    def __rich_repr__(self) -> Iterator[tuple[str, Any]]: ...

    # Single-assignment; returns func for decorator style
    def callback(self, callback: Callable[..., Any], /) -> Callable[..., Any]: ...


# Switch: named boolean (presence only; no values)
# - Helper wiring: helper→standalone/terminator, terminator→nowait.
class Switch:
    # Frozen, read-only attributes at runtime
    names: frozenset[str]
    group: str
    descr: str | None
    helper: bool
    standalone: bool
    terminator: bool
    nowait: bool
    hidden: bool
    deprecated: bool

    # Constructor overloads
    @overload
    def __new__(
            cls,
            *names: str,
            group: str = ...,
            descr: str = ...,
            helper: Literal[False] = ...,
            standalone: bool = ...,
            terminator: bool = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Switch: ...
    @overload
    def __new__(
            cls,
            *names: str,
            group: str = ...,
            descr: str = ...,
            explicit: bool = ...,  # Ignored for switches; kept for parity (if present in runtime)
            helper: Literal[True],
            standalone: bool = ...,
            terminator: bool = ...,
            nowait: bool = ...,
            deprecated: bool = ...  # Not recommended with helper
    ) -> Switch: ...

    # Presence-only invocation at runtime
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...

    # Decorator plumbing: returns the spec itself
    def __switch__(self) -> Self: ...

    # Diagnostics and Rich integration (help/pretty)
    def __repr__(self) -> str: ...
    def __rich_repr__(self) -> Iterator[tuple[str, Any]]: ...

    # Single-assignment; returns func for decorator style
    def callback(self, callback: Callable[..., Any], /) -> Callable[..., Any]: ...


# Decorator/factory overloads (both styles supported)
@overload
def operand[_T](
        metavar: str = ...,
        /,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsOperand[_T] | Callable[[Callable[..., Any]], Operand[_T]]: ...
@overload
def operand[_T](
        *,
        type: Callable[[str], _T] = ...,
        nargs: Literal["..."] | ellipsis,  # remainder (operands only)
        default: _T = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsOperand[_T] | Callable[[Callable[..., Any]], Operand[_T]]: ...

@overload
def option[_T](
        *names: str,
        metavar: str = ...,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str = ...,
        explicit: bool = ...,
        helper: Literal[False] = ...,
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsOption[_T] | Callable[[Callable[..., Any]], Option[_T]]: ...
@overload
def option[_T](
        *names: str,
        metavar: str = ...,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str = ...,
        explicit: bool = ...,
        helper: Literal[True],
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        deprecated: bool = ...  # Not recommended with helper
) -> SupportsOption[_T] | Callable[[Callable[..., Any]], Option[_T]]: ...

@overload
def switch(
        *names: str,
        group: str = ...,
        descr: str = ...,
        helper: Literal[False] = ...,
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsSwitch | Callable[[Callable[..., Any]], Switch]: ...
@overload
def switch(
        *names: str,
        group: str = ...,
        descr: str = ...,
        explicit: bool = ...,  # Ignored for switches; kept for parity (if present in runtime)
        helper: Literal[True],
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        deprecated: bool = ...  # Not recommended with helper
) -> SupportsSwitch | Callable[[Callable[..., Any]], Switch]: ...
