"""
argonaut.arguments
~~~~~~~~~~~~~~~~~~

Public API for defining command-line arguments parsed by the command runner.

What this module provides
- Cardinal: positional arguments (that do not trigger subcommand resolution).
- Option: named options (e.g., -o/--opt) that accept values.
- Flag: named switches (e.g., -v/--verbose) that do not accept values.

Defining arguments
- Instantiate Cardinal/Option/Flag directly, or
- Use the decorators: @cardinal(...), @option(...), @flag(...).
  The decorators attach a callback (single-assignment). The callback is invoked
  when the spec is matched during parsing, and you can also call the spec object
  directly; its __call__ signature adapts to `nargs`.

Key semantics
- Values and callback payloads
  - Option accepts values; Flag does not.
  - Cardinal handles positional values.
  - For `nargs`, the callback receives:
    * None: a single value (scalar).
    * "?": an optional single value (scalar). If omitted, the callback is not invoked.
    * "*": a list of zero or more values.
    * "+": a list of one or more values.
    * int (including 1): a list with exactly that many values.
      Note: even when `nargs == 1`, the callback receives a list of length 1.

- Special behaviors (Option and Flag)
  - helper=True: marks a help-like switch (e.g., -h/--help). Implies
    standalone=True and terminator=True; cannot be hidden or deprecated.
  - standalone=True: must be the only user-provided argument for the resolved
    command (mutually exclusive with other args).
  - terminator=True: after parsing (and running any callbacks), short-circuits
    the command run (e.g., for --help or --version).
  - explicit=True (Option only): requires attached values only, such as
    --opt=value or -oVALUE (not --opt value or -o value).

- Names
  - Options/Flags accept one or more names like "-o" and/or "--opt".
    Names are validated, deduplicated, and ordered (short before long).

- Immutability and safety
  - Instances expose read-only attributes. Collections are frozen to prevent
    accidental mutation.

- Callbacks
  - Each spec supports `callback(func)` exactly once (single-assignment). The
    generated `__call__` matches `nargs` and invokes the callback only when
    appropriate (e.g., not for an omitted optional value with "?").

Notes
- An internal “void” sentinel distinguishes “unset” from None. This lets
  optional values be omitted without being mistaken for an explicit None.
- Cardinal with `greedy=True` consumes remaining positionals (and typically also
  consumes options and flags); it must not specify explicit `metavar` or `nargs`.
"""
from collections.abc import Callable, Iterable
from typing import Protocol, Literal, Self, Any, overload
from typing import type_check_only  # NOQA: Needed

from rich.text import Text


@type_check_only
class Void:
    """Internal sentinel type used to represent an unset value (distinct from None)."""

@type_check_only
class SupportsCardinal[_T](Protocol):
    """Decorator helper exposing a bound Cardinal via __cardinal__()."""
    def __cardinal__(self) -> Cardinal[_T]: ...

class Cardinal[_T]:
    """
    Positional argument specification.

    See module docstring for full semantics. Key points:
    - `nargs` controls arity and callback payload shape.
    - `greedy=True` consumes remaining positionals and forbids explicit `metavar`/`nargs`.
    - `callback(func)` is single-assignment and adapts __call__ to `nargs`.
    """
    metavar: str | Text
    type: Callable[[str], _T]
    nargs: Literal["?", "*", "+"] | int | None
    choices: Iterable[_T]
    default: _T | Void
    descr: str | Text
    group: str | Text | None
    greedy: bool
    nowait: bool
    hidden: bool
    deprecated: bool
    @overload
    def __new__(
            cls,
            metavar: str | Text = ...,
            /,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            choices: Iterable[_T] = ...,
            default: _T = ...,
            descr: str | Text = ...,
            group: str | Text = ...,
            *,
            greedy: Literal[False] = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Cardinal[_T]: ...
    @overload
    def __new__(
            cls,
            *,
            type: Callable[[str], _T] = ...,
            choices: Iterable[_T] = ...,
            default: _T = ...,
            descr: str | Text = ...,
            group: str | Text = ...,
            greedy: Literal[True],
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Cardinal[_T]: ...
    def __cardinal__(self) -> Self: ...
    def __call__(self, *args: Any, **kwargs: Any) -> None: ...
    def callback(self, callback: Callable[..., Any], /) -> Callable[..., Any]: ...

@overload
def cardinal[_T](
        metavar: str | Text = ...,
        /,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        choices: Iterable[_T] = ...,
        default: _T = ...,
        descr: str | Text = ...,
        group: str | Text = ...,
        *,
        greedy: Literal[False] = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsCardinal[_T] | Callable[[Callable[..., Any]], Cardinal[_T]]: ...
@overload
def cardinal[_T](
        *,
        type: Callable[[str], _T] = ...,
        choices: Iterable[_T] = ...,
        default: _T = ...,
        descr: str | Text = ...,
        group: str | Text = ...,
        greedy: Literal[True],
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsCardinal[_T] | Callable[[Callable[..., Any]], Cardinal[_T]]: ...

@type_check_only
class SupportsOption[_T](Protocol):
    """Decorator helper exposing a bound Option via __option__()."""
    def __option__(self) -> Option[_T]: ...

class Option[_T]:
    """
    Named option specification that accepts values.

    Highlights:
    - Names validated and ordered (short before long).
    - `explicit=True` requires attached values only (e.g., --opt=value, -oVALUE).
    - `callback(func)` is single-assignment and adapts __call__ to `nargs`.
    """
    names: tuple[str | Text, ...]
    metavar: str | Text
    type: Callable[[str], _T]
    nargs: Literal["?", "*", "+"] | int | None
    choices: Iterable[_T]
    default: _T | Void
    descr: str | Text
    group: str | Text
    explicit: bool
    helper: bool
    standalone: bool
    terminator: bool
    nowait: bool
    hidden: bool
    deprecated: bool
    @overload
    def __new__(
            cls,
            *names: str | Text,
            metavar: str | Text = ...,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            choices: Iterable[_T] = ...,
            default: _T = ...,
            descr: str | Text = ...,
            group: str | Text = ...,
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
            *names: str | Text,
            metavar: str | Text = ...,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            choices: Iterable[_T] = ...,
            default: _T = ...,
            descr: str | Text = ...,
            group: str | Text = ...,
            explicit: bool = ...,
            helper: Literal[True],
            standalone: bool = ...,  # Ignored
            terminator: bool = ...,  # Ignored
            nowait: bool = ...       # Ignored
    ) -> Option[_T]: ...
    def __option__(self) -> Self: ...
    def __call__(self, *args: Any, **kwargs: Any) -> None: ...
    def callback(self, callback: Callable[..., Any], /) -> Callable[..., Any]: ...

@overload
def option[_T](
        *names: str | Text,
        metavar: str | Text = ...,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        choices: Iterable[_T] = ...,
        default: _T = ...,
        descr: str | Text = ...,
        group: str | Text = ...,
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
        *names: str | Text,
        metavar: str | Text = ...,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        choices: Iterable[_T] = ...,
        default: _T = ...,
        descr: str | Text = ...,
        group: str | Text = ...,
        explicit: bool = ...,
        helper: Literal[True],
        standalone: bool = ...,  # Ignored
        terminator: bool = ...,  # Ignored
        nowait: bool = ...       # Ignored
) -> SupportsOption[_T] | Callable[[Callable[..., Any]], Option[_T]]: ...

@type_check_only
class SupportsFlag(Protocol):
    """Decorator helper exposing a bound Flag via __flag__()."""
    def __flag__(self) -> Flag: ...

class Flag:
    """
    Named switch specification that does not accept values.

    Highlights:
    - Names validated and ordered (short before long).
    - `callback(func)` is single-assignment; __call__ takes no value parameters.
    """
    names: tuple[str | Text, ...]
    descr: str | Text
    group: str | Text | None
    explicit: bool
    helper: bool
    standalone: bool
    terminator: bool
    nowait: bool
    hidden: bool
    deprecated: bool
    @overload
    def __new__(
            cls,
            *names: str | Text,
            descr: str | Text = ...,
            group: str | Text = ...,
            explicit: bool = ...,
            helper: Literal[False] = ...,
            standalone: bool = ...,
            terminator: bool = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Flag: ...
    @overload
    def __new__(
            cls,
            *names: str | Text,
            descr: str | Text = ...,
            group: str | Text = ...,
            explicit: bool = ...,
            helper: Literal[True],
            standalone: bool = ...,  # Ignored
            terminator: bool = ...,  # Ignored
            nowait: bool = ...       # Ignored
    ) -> Flag: ...
    def __flag__(self) -> Self: ...
    def __call__(self, *args: Any, **kwargs: Any) -> None: ...
    def callback(self, callback: Callable[..., Any], /) -> Callable[..., Any]: ...

@overload
def flag(
        *names: str | Text,
        descr: str | Text = ...,
        group: str | Text = ...,
        explicit: bool = ...,
        helper: Literal[False] = ...,
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsFlag | Callable[[Callable[..., Any]], Flag]: ...
@overload
def flag(
        *names: str | Text,
        descr: str | Text = ...,
        group: str | Text = ...,
        explicit: bool = ...,
        helper: Literal[True],
        standalone: bool = ...,  # Ignored
        terminator: bool = ...,  # Ignored
        nowait: bool = ...       # Ignored
) -> SupportsFlag | Callable[[Callable[..., Any]], Flag]: ...
