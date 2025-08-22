from collections.abc import Callable, Iterable, Set
from types import EllipsisType
from typing import Protocol, Literal, Self, Any, overload
from typing import type_check_only  # NOQA: Needed


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
    __fields__: tuple[str, ...]


class Cardinal[_T](metaclass=ArgumentType):
    metavar: str | None
    type: Callable[[str], _T]
    nargs: Literal["?", "*", "+"] | int | EllipsisType | None
    default: _T | None
    choices: Iterable[_T]
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
            default: _T = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str = ...,
            *,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...,
    ) -> Cardinal[_T]: ...
    @overload
    def __new__(
            cls,
            *,
            type: Callable[[str], _T] = ...,
            nargs: Literal["..."] | EllipsisType,
            default: _T = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...,
    ) -> Cardinal[_T]: ...
    def __cardinal__(self) -> Self: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...

@overload
def cardinal[_T](
        metavar: str = ...,
        /,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int = ...,
        default: _T = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str = ...,
        *,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...,
) -> SupportsCardinal[_T] | Callable[[Callable[..., Any]], Cardinal[_T]]: ...
@overload
def cardinal[_T](
        *,
        type: Callable[[str], _T] = ...,
        nargs: Literal["..."] | EllipsisType,
        default: _T = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...,
) -> SupportsCardinal[_T] | Callable[[Callable[..., Any]], Cardinal[_T]]: ...


class Option[_T](metaclass=ArgumentType):
    names: Set[str]
    metavar: str | None
    type: Callable[[str], _T]
    nargs: Literal["?", "*", "+"] | int | None
    default: _T | None
    choices: Iterable[_T]
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
            default: _T = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str = ...,
            inline: bool = ...,
            helper: Literal[False] = ...,
            standalone: bool = ...,
            terminator: bool = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...,
    ) -> Option[_T]: ...
    @overload
    def __new__(
            cls,
            *names: str,
            metavar: str = ...,
            type: Callable[[str], _T] = ...,
            nargs: Literal["?", "*", "+"] | int,
            default: _T = ...,
            choices: Iterable[_T] = ...,
            group: str = ...,
            descr: str = ...,
            inline: bool = ...,
            helper: Literal[True],
            standalone: bool = ...,
            terminator: bool = ...,
            nowait: bool = ...,
            deprecated: bool = ...,
    ) -> Option[_T]: ...
    def __option__(self) -> Self: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...

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
        inline: bool = ...,
        helper: Literal[False] = ...,
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...,
) -> SupportsOption[_T] | Callable[[Callable[..., Any]], Option[_T]]: ...
@overload
def option[_T](
        *names: str,
        metavar: str = ...,
        type: Callable[[str], _T] = ...,
        nargs: Literal["?", "*", "+"] | int,
        default: _T = ...,
        choices: Iterable[_T] = ...,
        group: str = ...,
        descr: str = ...,
        inline: bool = ...,
        helper: Literal[True],
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        deprecated: bool = ...,
) -> SupportsOption[_T] | Callable[[Callable[..., Any]], Option[_T]]: ...

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
            descr: str = ...,
            helper: Literal[False] = ...,
            standalone: bool = ...,
            terminator: bool = ...,
            nowait: bool = ...,
            hidden: bool = ...,
            deprecated: bool = ...,
    ) -> Flag: ...
    @overload
    def __new__(
            cls,
            *names: str,
            group: str = ...,
            descr: str = ...,
            helper: Literal[True],
            standalone: bool = ...,
            terminator: bool = ...,
            nowait: bool = ...,
            deprecated: bool = ...,
    ) -> Flag: ...
    def __flag__(self) -> Self: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...

@overload
def flag(
        *names: str,
        group: str = ...,
        descr: str = ...,
        helper: Literal[False] = ...,
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        hidden: bool = ...,
        deprecated: bool = ...,
) -> SupportsFlag | Callable[[Callable[..., Any]], Flag]: ...
@overload
def flag(
        *names: str,
        group: str = ...,
        descr: str = ...,
        helper: Literal[True],
        standalone: bool = ...,
        terminator: bool = ...,
        nowait: bool = ...,
        deprecated: bool = ...,
) -> SupportsFlag | Callable[[Callable[..., Any]], Flag]: ...
