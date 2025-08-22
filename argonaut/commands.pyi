from collections.abc import Callable, Iterable, Mapping, Set
from typing import Protocol, Any, overload
from typing import type_check_only  # NOQA: Needed

from argonaut.arguments import SupportsCardinal, SupportsOption, SupportsFlag, Cardinal, Option, Flag
from argonaut.faults import Triggerable


class Invocable(Protocol):
    @overload
    def __invoke__(self) -> Any: ...
    @overload
    def __invoke__(self, prompt: Any) -> Any: ...


@type_check_only
class CommandType(type):
    __fields__: tuple[str, ...]


class Command(metaclass=CommandType):
    name: str
    descr: str | None
    usage: str | None
    groups: Mapping[str, tuple[Cardinal | Option | Flag, ...]]
    cardinals: Mapping[str, Cardinal]
    modifiers: Mapping[str, Option | Flag]
    conflicts: Mapping[str, Set[str]]
    parent: Command | None
    children: Mapping[str, Command]
    shell: bool
    fancy: bool
    deferred: bool
    @property
    def root(self) -> Command: ...
    @property
    def rootpath(self) -> tuple[Command, ...]: ...
    @property
    def source(self) -> Callable[..., Any] | tuple[Cardinal | Option | Flag, ...]: ...
    @overload
    def __new__(
            cls,
            callback: Callable[..., Any],
            /,
            parent: Command = ...,
            name: str = ...,
            descr: str = ...,
            usage: str = ...,
            *,
            fancy: bool = ...,
            shell: bool = ...,
            deferred: bool = ...,
    ) -> Command: ...
    @overload
    def __new__(
            cls,
            arguments: Iterable[SupportsCardinal | SupportsOption | SupportsFlag],
            /,
            parent: Command = ...,
            name: str = ...,
            descr: str = ...,
            usage: str = ...,
            *,
            fancy: bool = ...,
            shell: bool = ...,
            deferred: bool = ...,
    ) -> Command: ...
    @overload
    def __new__(
            cls,
            template: Command,
            /,
            parent: Command = ...,
            name: str = ...,
            descr: str = ...,
            usage: str = ...,
            *,
            fancy: bool = ...,
            shell: bool = ...,
            deferred: bool = ...,
    ) -> Command: ...
    def fallback(self, fallback: Callable[..., Any]) -> Any: ...
    @overload
    def command(
            self,
            callback: Callable[..., Any],
            /,
            name: str = ...,
            descr: str = ...,
            usage: str = ...,
            *,
            fancy: bool = ...,
            shell: bool = ...,
            deferred: bool = ...,
    ) -> Command: ...
    @overload
    def command(
            self,
            arguments: Iterable[SupportsCardinal | SupportsOption | SupportsFlag],
            /,
            name: str = ...,
            descr: str = ...,
            usage: str = ...,
            *,
            fancy: bool = ...,
            shell: bool = ...,
            deferred: bool = ...,
    ) -> Command: ...
    @overload
    def command(
            self,
            template: Command,
            /,
            name: str = ...,
            descr: str = ...,
            usage: str = ...,
            *,
            fancy: bool = ...,
            shell: bool = ...,
            deferred: bool = ...,
    ) -> Command: ...
    def include(self, source: str, /) -> None: ...
    def trigger(self, fault: Triggerable, /, **options: Any) -> None: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    @overload
    def __invoke__(self) -> dict[str, Any] | None: ...
    @overload
    def __invoke__(self, prompt: str | Iterable[str]) -> dict[str, Any] | None: ...

@overload
def command(
        callback: Callable[..., Any],
        /,
        parent: Command = ...,
        name: str = ...,
        descr: str = ...,
        usage: str = ...,
        *,
        fancy: bool = ...,
        shell: bool = ...,
        deferred: bool = ...,
) -> Command: ...
@overload
def command(
        arguments: Iterable[SupportsCardinal | SupportsOption | SupportsFlag],
        /,
        parent: Command = ...,
        name: str = ...,
        descr: str = ...,
        usage: str = ...,
        *,
        fancy: bool = ...,
        shell: bool = ...,
        deferred: bool = ...,
) -> Command: ...
@overload
def command(
        template: Command,
        /,
        parent: Command = ...,
        name: str = ...,
        descr: str = ...,
        usage: str = ...,
        *,
        fancy: bool = ...,
        shell: bool = ...,
        deferred: bool = ...,
) -> Command: ...
@overload
def command(
        *,
        parent: Command = ...,
        name: str = ...,
        descr: str = ...,
        usage: str = ...,
        fancy: bool = ...,
        shell: bool = ...,
        deferred: bool = ...,
) -> Callable[[Callable[..., Any]], Command]: ...

@overload
def invoke(invocable: Invocable, /) -> Any: ...
@overload
def invoke(invocable: Invocable, prompt: Any, /) -> Any: ...
# Allowed, but no intended and maybe removed in the future.
@overload
def invoke(callback: Callable[..., Any], /) -> Any: ...
@overload
def invoke(callback: Callable[..., Any], prompt: Any, /) -> Any: ...
@overload
def invoke(arguments: Iterable[SupportsCardinal | SupportsOption | SupportsFlag], /) -> Any: ...

@overload
def invoke(arguments: Iterable[SupportsCardinal | SupportsOption | SupportsFlag], prompt: Any, /) -> Any: ...
