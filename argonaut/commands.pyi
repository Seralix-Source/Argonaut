from collections.abc import Callable, Iterable, Iterator, Sequence, Mapping, Set
from typing import Protocol, Self, Any, overload
from typing import type_check_only  # NOQA: F-401

from rich.text import Text

from argonaut.arguments import Cardinal, Option, Flag
from argonaut.faults import Triggerable


@type_check_only
class Invocable(Protocol):
    @overload
    def __invoke__(self) -> None: ...
    @overload
    def __invoke__(self, prompt: str) -> None: ...
    @overload
    def __invoke__(self, prompt: Iterable[str]) -> None: ...

@type_check_only
class CommandType(type):
    __introspectable__: tuple[str, ...]
    __displayable__: tuple[str, ...]
    __typename__: str

class Command(metaclass=CommandType):
    parent: Command | None
    children: Mapping[str, Command]
    name: str
    descr: str | Text | None
    usage: str | Text | None
    epilog: str | Text | None
    notes: Sequence[str | Text]
    examples: Sequence[str | Text]
    warnings: Sequence[str | Text]
    version: str | Text | None
    license: str | Text | None
    support: str | Text | None
    homepage: str | Text | None
    copyright: str | Text | None
    bugtracker: str | Text | None
    developers: Sequence[str | Text]
    maintainers: Sequence[str | Text]
    conflicts: Mapping[str, Set[str]]
    cardinals: Mapping[str, Cardinal]
    switches: Mapping[str, Option | Flag]
    groups: Mapping[str, Sequence[Cardinal | Option | Flag]]
    shell: bool
    fancy: bool
    colorful: bool
    deferred: bool
    @property
    def root(self) -> Command | Self: ...
    @property
    def path(self) -> Sequence[Command]: ...
    @overload
    def __new__(
            cls,
            callback: Callable[..., Any],
            /,
            parent: Command = ...,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            epilog: str | Text = ...,
            notes: Iterable[str | Text] = ...,
            examples: Iterable[str | Text] = ...,
            warnings: Iterable[str | Text] = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            support: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            bugtracker: str | Text = ...,
            developers: Iterable[str | Text] = ...,
            maintainers: Iterable[str | Text] = ...,
            conflicts: Iterable[Iterable[str | Text]] = ...,
            *,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            deferred: bool = ...
    ) -> Command: ...
    @overload
    def __new__(
            cls,
            template: Command,
            /,
            parent: Command = ...,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            epilog: str | Text = ...,
            notes: Iterable[str | Text] = ...,
            examples: Iterable[str | Text] = ...,
            warnings: Iterable[str | Text] = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            support: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            bugtracker: str | Text = ...,
            developers: Iterable[str | Text] = ...,
            maintainers: Iterable[str | Text] = ...,
            conflicts: Iterable[Iterable[str | Text]] = ...,
            *,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            deferred: bool = ...
    ) -> Command: ...
    def fallback(self, fallback: Callable) -> Callable: ...
    @overload
    def command(
            self,
            callback: Callable[..., Any],
            /,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            epilog: str | Text = ...,
            notes: Iterable[str | Text] = ...,
            examples: Iterable[str | Text] = ...,
            warnings: Iterable[str | Text] = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            support: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            bugtracker: str | Text = ...,
            developers: Iterable[str | Text] = ...,
            maintainers: Iterable[str | Text] = ...,
            conflicts: Iterable[Iterable[str | Text]] = ...,
            *,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            deferred: bool = ...
    ) -> Command: ...
    @overload
    def command(
            self,
            template: Command,
            /,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            epilog: str | Text = ...,
            notes: Iterable[str | Text] = ...,
            examples: Iterable[str | Text] = ...,
            warnings: Iterable[str | Text] = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            support: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            bugtracker: str | Text = ...,
            developers: Iterable[str | Text] = ...,
            maintainers: Iterable[str | Text] = ...,
            conflicts: Iterable[Iterable[str | Text]] = ...,
            *,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            deferred: bool = ...
    ) -> Command: ...
    @overload
    def command(
            self,
            *,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            epilog: str | Text = ...,
            notes: Iterable[str | Text] = ...,
            examples: Iterable[str | Text] = ...,
            warnings: Iterable[str | Text] = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            support: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            bugtracker: str | Text = ...,
            developers: Iterable[str | Text] = ...,
            maintainers: Iterable[str | Text] = ...,
            conflicts: Iterable[Iterable[str | Text]] = ...,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            deferred: bool = ...
    ) -> Callable[[Callable | Command], Command]: ...
    def include(self, source: str, /, *, propagate: bool = ...) -> None: ...
    def trigger(self, fault: Triggerable) -> None: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    @overload
    def __invoke__(self) -> None: ...
    @overload
    def __invoke__(self, prompt: str) -> None: ...
    @overload
    def __invoke__(self, prompt: Iterable[str]) -> None: ...
    def __repr__(self) -> str: ...
    def __rich_repr__(self) -> Iterator[tuple[str, Any]]: ...

@overload
def command(
        callback: Callable[..., Any],
        /,
        parent: Command = ...,
        name: str | Text = ...,
        descr: str | Text = ...,
        usage: str | Text = ...,
        epilog: str | Text = ...,
        notes: Iterable[str | Text] = ...,
        examples: Iterable[str | Text] = ...,
        warnings: Iterable[str | Text] = ...,
        version: str | Text = ...,
        license: str | Text = ...,
        support: str | Text = ...,
        homepage: str | Text = ...,
        copyright: str | Text = ...,
        bugtracker: str | Text = ...,
        developers: Iterable[str | Text] = ...,
        maintainers: Iterable[str | Text] = ...,
        conflicts: Iterable[Iterable[str | Text]] = ...,
        *,
        shell: bool = ...,
        fancy: bool = ...,
        colorful: bool = ...,
        deferred: bool = ...
) -> Command: ...
@overload
def command(
        template: Command,
        /,
        parent: Command = ...,
        name: str | Text = ...,
        descr: str | Text = ...,
        usage: str | Text = ...,
        epilog: str | Text = ...,
        notes: Iterable[str | Text] = ...,
        examples: Iterable[str | Text] = ...,
        warnings: Iterable[str | Text] = ...,
        version: str | Text = ...,
        license: str | Text = ...,
        support: str | Text = ...,
        homepage: str | Text = ...,
        copyright: str | Text = ...,
        bugtracker: str | Text = ...,
        developers: Iterable[str | Text] = ...,
        maintainers: Iterable[str | Text] = ...,
        conflicts: Iterable[Iterable[str | Text]] = ...,
        *,
        shell: bool = ...,
        fancy: bool = ...,
        colorful: bool = ...,
        deferred: bool = ...
) -> Command: ...
@overload
def command(
        template: Command,
        /,
        parent: Command = ...,
        name: str | Text = ...,
        descr: str | Text = ...,
        usage: str | Text = ...,
        epilog: str | Text = ...,
        notes: Iterable[str | Text] = ...,
        examples: Iterable[str | Text] = ...,
        warnings: Iterable[str | Text] = ...,
        version: str | Text = ...,
        license: str | Text = ...,
        support: str | Text = ...,
        homepage: str | Text = ...,
        copyright: str | Text = ...,
        bugtracker: str | Text = ...,
        developers: Iterable[str | Text] = ...,
        maintainers: Iterable[str | Text] = ...,
        conflicts: Iterable[Iterable[str | Text]] = ...,
        *,
        shell: bool = ...,
        fancy: bool = ...,
        colorful: bool = ...,
        deferred: bool = ...
) -> Command: ...
@overload
def command(
        *,
        parent: Command = ...,
        name: str | Text = ...,
        descr: str | Text = ...,
        usage: str | Text = ...,
        epilog: str | Text = ...,
        notes: Iterable[str | Text] = ...,
        examples: Iterable[str | Text] = ...,
        warnings: Iterable[str | Text] = ...,
        version: str | Text = ...,
        license: str | Text = ...,
        support: str | Text = ...,
        homepage: str | Text = ...,
        copyright: str | Text = ...,
        bugtracker: str | Text = ...,
        developers: Iterable[str | Text] = ...,
        maintainers: Iterable[str | Text] = ...,
        conflicts: Iterable[Iterable[str | Text]] = ...,
        shell: bool = ...,
        fancy: bool = ...,
        colorful: bool = ...,
        deferred: bool = ...
) -> Callable[[Callable | Command], Command]: ...
@overload
def invoke(invocable: Invocable, /) -> None: ...
@overload
def invoke(invocable: Invocable, prompt: str, /) -> None: ...
@overload
def invoke(invocable: Invocable, prompt: Iterable[str], /) -> None: ...
@overload
def invoke(callback: Callable, /) -> None: ...
@overload
def invoke(callback: Callable, prompt: str, /) -> None: ...
@overload
def invoke(callback: Callable, prompt: Iterable[str], /) -> None: ...
