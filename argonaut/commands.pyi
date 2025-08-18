from collections.abc import Callable, Iterable, Iterator, Sequence, Mapping
from typing import *

from .arguments import Operand, Option, Switch


class Command:
    name: str
    descr: str | None
    groups: Mapping[str, tuple[Operand | Option | Switch, ...]]
    operands: Mapping[str, Operand]
    qualifiers: Mapping[str, Option | Switch]
    parent: Command | None
    children: Mapping[str, Command]
    @overload
    def __new__(
            cls,
            callback: Callable[..., Any],
            /,
            parent: Command = ...,
            name: str = ...,
            descr: str = ...,
    ) -> Command: ...
    @overload
    def __new__(
            cls,
            arguments: Iterable[Operand | Option | Switch],
            /,
            parent: Command = ...,
            name: str = ...,
            descr: str = ...,
    ) -> Command: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    def __repr__(self) -> str: ...
    def __rich_repr__(self) -> Iterator[tuple[str, Any]]: ...
    @overload
    def command(
            self,
            callback: Callable[..., Any],
            /,
            name: str = ...,
            descr: str = ...,
    ) -> Command: ...
    @overload
    def command(
            self,
            arguments: Iterable[Operand | Option | Switch],
            /,
            name: str = ...,
            descr: str = ...,
    ) -> Command: ...

@overload
def command(
        callback: Callable[..., Any],
        /,
        parent: Command = ...,
        name: str = ...,
        descr: str = ...,
) -> Command: ...
@overload
def command(
        arguments: Iterable[Operand | Option | Switch],
        /,
        parent: Command = ...,
        name: str = ...,
        descr: str = ...,
) -> Command: ...
@overload
def command(
        *,
        parent: Command = ...,
        name: str = ...,
        descr: str = ...,
) -> Callable[[Callable[..., Any]], Command]: ...
