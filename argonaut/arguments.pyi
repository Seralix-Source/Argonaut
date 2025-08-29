from collections.abc import Callable, Iterable, Iterator, Sequence, Set
from typing import Protocol, Literal, Self, Any, overload
from typing import type_check_only  # NOQA: F-401

from rich.text import Text


@type_check_only
class SupportsCardinal[_T](Protocol):
    def __cardinal__(self) -> Cardinal[_T]: ...
@type_check_only
class SupportsOption[_T](Protocol):
    def __option__(self) -> Option[_T]: ...
@type_check_only
class SupportsFlag(Protocol):
    def __flag__(self) -> Flag: ...

@type_check_only
class ArgumentType(type):
    __introspectable__: tuple[str, ...]
    __displayable__: tuple[str, ...]
    __typename__: str

class Cardinal[_T](metaclass=ArgumentType):
    metavar: str | None
    type: Callable[[str], _T]
    nargs: Literal["?", "*", "+"] | int | ellipsis | None
    default: _T | None
    choices: Sequence[_T] | Set[_T]
    group: str
    descr: str | None
    nowait: bool
    hidden: bool
    deprecated: bool
    @overload
    def __new__(
            cls,
            metavar: str = ...,
            /,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            default: _T | None = ...,
            group: str = ...,
            descr: str | Text = ...,
            *,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Cardinal[_T]: ...
    @overload
    def __new__(
            cls,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            default: _T | None = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str | Text = ...,
            *,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Cardinal[_T]: ...
    @overload
    def __new__(
            cls,
            *,
            type: Callable[[str], _T] = ...,
            nargs: ellipsis,
            default: _T | None = ...,
            group: str = ...,
            descr: str | Text = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...
    ) -> Cardinal[_T]: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    def __cardinal__(self) -> Self: ...
    def __repr__(self) -> str: ...
    def __rich_repr__(self) -> Iterator[tuple[str, Any]]: ...

class Option[_T](metaclass=ArgumentType):
    names: Set[str]
    metavar: str | None
    type: Callable[[str], _T]
    nargs: Literal["?", "*", "+"] | int | None
    default: _T | None
    choices: Sequence[_T] | Set[_T]
    group: str
    descr: str | None
    inline: bool
    helper: bool
    standalone: bool
    terminator: bool
    nowait: bool
    hidden: bool
    deprecated: bool
    @overload
    def __new__(
            cls,
            *names: str,
            metavar: str = ...,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            default: _T | None = ...,
            group: str = ...,
            descr: str | Text = ...,
            inline: bool = ...,
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
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            default: _T | None = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str | Text = ...,
            inline: bool = ...,
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
            default: _T | None = ...,
            group: str = ...,
            descr: str | Text = ...,
            inline: bool = ...,
            helper: Literal[True]
    ) -> Option[_T]: ...
    @overload
    def __new__(
            cls,
            *names: str,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int = ...,
            default: _T | None = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str | Text = ...,
            inline: bool = ...,
            helper: Literal[True]
    ) -> Option[_T]: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    def __option__(self) -> Self: ...
    def __repr__(self) -> str: ...
    def __rich_repr__(self) -> Iterator[tuple[str, Any]]: ...

class Flag(metaclass=ArgumentType):
    names: Set[str]
    group: str
    descr: str | None
    helper: bool
    standalone: bool
    terminator: bool
    nowait: bool
    hidden: bool
    deprecated: bool
    @overload
    def __new__(
            cls,
            *names: str,
            group: str = ...,
            descr: str | Text = ...,
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
            *names: str,
            group: str = ...,
            descr: str | Text = ...,
            helper: Literal[True]
    ) -> Flag: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    def __flag__(self) -> Self: ...
    def __repr__(self) -> str: ...
    def __rich_repr__(self) -> Iterator[tuple[str, Any]]: ...

@overload
def cardinal[_T](
        metavar: str = ...,
        /,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T | None = ...,
        group: str = ...,
        descr: str | Text = ...,
        *,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsCardinal[_T] | Callable[[Cardinal], Cardinal[_T]]: ...
@overload
def cardinal[_T](
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T | None = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str | Text = ...,
        *,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsCardinal[_T] | Callable[[Cardinal], Cardinal[_T]]: ...
@overload
def cardinal[_T](
        *,
        type: Callable[[str], _T] = ...,
        nargs: ellipsis,
        default: _T | None = ...,
        group: str = ...,
        descr: str | Text = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsCardinal[_T] | Callable[[Cardinal], Cardinal[_T]]: ...
@overload
def option[_T](
        *names: str,
        metavar: str = ...,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T | None = ...,
        group: str = ...,
        descr: str | Text = ...,
        inline: bool = ...,
        helper: Literal[False] = ...,
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsOption[_T] | Callable[[Callable], Option[_T]]: ...
@overload
def option[_T](
        *names: str,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T | None = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str | Text = ...,
        inline: bool = ...,
        helper: Literal[False] = ...,
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsOption[_T] | Callable[[Callable], Option[_T]]: ...
@overload
def option[_T](
        *names: str,
        metavar: str = ...,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T | None = ...,
        group: str = ...,
        descr: str | Text = ...,
        inline: bool = ...,
        helper: Literal[True]
) -> SupportsOption[_T] | Callable[[Callable], Option[_T]]: ...
@overload
def option[_T](
        *names: str,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T | None = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str | Text = ...,
        inline: bool = ...,
        helper: Literal[True]
) -> SupportsOption[_T] | Callable[[Callable], Option[_T]]: ...
@overload
def flag(
        *names: str,
        group: str = ...,
        descr: str | Text = ...,
        helper: Literal[False] = ...,
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...
) -> SupportsFlag | Callable[[Callable], Flag]: ...
@overload
def flag(
        *names: str,
        group: str = ...,
        descr: str | Text = ...,
        helper: Literal[True]
) -> SupportsFlag | Callable[[Callable], Flag]: ...
