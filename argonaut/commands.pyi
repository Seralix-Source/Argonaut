"""
argonaut.commands
~~~~~~~~~~~~~~~~~

Public API for declaring and running command trees.

What this module provides
- Command: a node in a command hierarchy (root or subcommand).
- command(...): decorator/factory to create a Command from a callable or an
  iterable of argument specifications.
- invoke(...): convenience runner that parses argv (or a provided prompt),
  runs callbacks, and returns either None (if a handler ran) or the parsed
  namespace mapping (if no handler was associated with the resolved command).

Two ways to define commands
- Decorator-based:
    >>> from argonaut import *
    >>>
    >>> @command(name="cli")
    >>> def cli(
    ...     file: str = Cardinal(),
    ...     /,
    ...     *,
    ...     verbose: bool = Flag("-v", "--verbose"),
    ... ) -> None:
    ...     pass
    >>>
    >>> @cli.command(name="sub")
    >>> def sub(
    ...     count: int = Option("-n", "--count", type=int, default=1),
    ... ) -> None:
    ...     pass

- Iterable-based:
    >>> from argonaut import *
    >>>
    >>> cli = command([
    ...     Cardinal(),
    ...     Flag("-v", "--verbose"),
    ... ])
    >>>
    >>> sub = cli.command([
    ...     Option("-n", "--count", type=int, default=1),
    ... ])

Decorator vs factory
- The top-level command(...) and the bound method Command.command(...) accept either:
  • no positional source (returning a decorator), or
  • a callable or an iterable of argument specs (returning a Command immediately).
- Do not stack the two styles for the same command: use one or the other for clarity.

Invoking
- invoke(cmd) parses sys.argv[1:] by default. Pass a string or an iterable of strings
  to override input: invoke(cmd, "--flag value").
- If the resolved command has a handler (callable source), that handler is executed
  and invoke(...) returns None. If it has no handler (iterable-defined), the parsed
  namespace is returned as a dict[str, Any].

Helpful properties on Command
- stderr/stdout: preconfigured rich.Console instances.
- patriarch: top-most ancestor Command in the tree.
- rootpath: tuple of Commands from the root (excluded in some descriptions) down to self.
- namespace: deep copy of last parsed mapping, or None if not available.
- tokens: deep copy of the remaining token deque from the last parse, or None.

Notes
- Use methodize=True to write handlers as instance-style functions (first param is self/this).
- Helper switches (e.g., -h/--help, -v/--version) are injected automatically unless provided.
- Only orphan commands can be attached to a parent; existing parents are not overridden.
"""
from collections import deque
from collections.abc import Callable, Iterable, Sequence, Mapping, Set
from typing import Protocol, Self, Any, overload
from typing import type_check_only  # NOQA: Needed

from rich.console import Console
from rich.style import Style
from rich.text import Text

from .arguments import SupportsCardinal, SupportsOption, SupportsFlag, Cardinal, Option, Flag
from .triggers import Triggerable

type SupportArgument = SupportsCardinal | SupportsOption | SupportsFlag


@type_check_only
class Invocable(Protocol):
    @overload
    def __invoke__(self) -> dict[str, Any] | None: ...
    @overload
    def __invoke__(self, prompt: str | Sequence[str]) -> dict[str, Any] | None: ...


# Exposed mappings are sanitized, read-only views for convenience.
# They may look redundant, but are provided to avoid rebuilding common indices in user code.
# Internals maintain a single source of truth and keep these views in sync.
# Tip: set methodize=True to write callbacks as instance-style functions (first param self/this).
# Sorry for overwhelm loggings (feel free to extend the class and edit the reprs)
class Command:
    parent: Command | None
    name: str | Text
    descr: str | Text | None
    usage: str | Text | None
    build: str | Text | None
    epilog: str | Text | None
    version: str | Text | None
    license: str | Text | None
    homepage: str | Text | None
    copyright: str | Text | None
    children: Mapping[str, Command]
    groups: Mapping[str, tuple[Cardinal | Option | Flag, ...]]
    conflicts: Mapping[str, Set[str]]
    cardinals: Mapping[str, Cardinal]
    switchers: Mapping[str, Option | Flag]
    options: Mapping[str, Option]
    flags: Mapping[str, Flag]
    styles: Mapping[str, str | Style]
    lazy: bool
    shell: bool
    fancy: bool
    colorful: bool
    methodize: bool
    @property
    def stderr(self) -> Console: ...
    @property
    def stdout(self) -> Console: ...
    @property
    def patriarch(self) -> Command | Self: ...
    @property
    def rootpath(self) -> tuple[Command, ...]: ...
    @property
    def namespace(self) -> Mapping[str, Any] | None: ...
    @property
    def tokens(self) -> deque[str] | None: ...
    @overload
    def __new__(
            cls,
            callback: Callable[..., Any],
            /,
            parent: Command = ...,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            build: str | Text = ...,
            epilog: str | Text = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            styles: Mapping[str, str | Style] = ...,
            conflicts: Iterable[Iterable[str]] = ...,
            *,
            lazy: bool = ...,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            methodize: bool = ...,
    ) -> Command: ...
    @overload
    def __new__(
            cls,
            arguments: Iterable[SupportArgument],
            /,
            parent: Command = ...,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            build: str | Text = ...,
            epilog: str | Text = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            styles: Mapping[str, str | Style] = ...,
            conflicts: Iterable[Iterable[str]] = ...,
            *,
            lazy: bool = ...,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            methodize: bool = ...,
    ) -> Command: ...
    @overload
    def command(
            self,
            callback: Callable[..., Any],
            /,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            build: str | Text = ...,
            epilog: str | Text = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            styles: Mapping[str, str | Style] = ...,
            conflicts: Iterable[Iterable[str]] = ...,
            *,
            lazy: bool = ...,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            methodize: bool = ...,
    ) -> Command: ...
    @overload
    def command(
            self,
            arguments: Iterable[SupportArgument],
            /,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            build: str | Text = ...,
            epilog: str | Text = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            styles: Mapping[str, str | Style] = ...,
            conflicts: Iterable[Iterable[str]] = ...,
            *,
            lazy: bool = ...,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            methodize: bool = ...,
    ) -> Command: ...
    @overload
    def command(
            self,
            *,
            name: str | Text = ...,
            descr: str | Text = ...,
            usage: str | Text = ...,
            build: str | Text = ...,
            epilog: str | Text = ...,
            version: str | Text = ...,
            license: str | Text = ...,
            homepage: str | Text = ...,
            copyright: str | Text = ...,
            styles: Mapping[str, str | Style] = ...,
            conflicts: Iterable[Iterable[str]] = ...,
            lazy: bool = ...,
            shell: bool = ...,
            fancy: bool = ...,
            colorful: bool = ...,
            methodize: bool = ...,
    ) -> Callable[[Callable[..., Any]], Command]: ...
    def discover(self, module: str, /) -> Self: ...
    def trigger(self, x: Triggerable, /, **options: Any) -> None: ...
    def fallback(self, fallback: Callable[..., Any], /) -> Callable[..., Any]: ...
    def __call__(self, *args: Any, **kwargs: Any) -> None: ...
    @overload
    def __invoke__(self) -> dict[str, Any] | None: ...
    @overload
    def __invoke__(self, prompt: str | Sequence[str]) -> dict[str, Any] | None: ...
    def __replace__(self, **overrides: Any) -> Command: ...

@overload
def command(
        callback: Callable[..., Any],
        /,
        parent: Command = ...,
        name: str | Text = ...,
        descr: str | Text = ...,
        usage: str | Text = ...,
        build: str | Text = ...,
        epilog: str | Text = ...,
        version: str | Text = ...,
        license: str | Text = ...,
        homepage: str | Text = ...,
        copyright: str | Text = ...,
        styles: Mapping[str, str | Style] = ...,
        conflicts: Iterable[Iterable[str]] = ...,
        *,
        lazy: bool = ...,
        shell: bool = ...,
        fancy: bool = ...,
        colorful: bool = ...,
        methodize: bool = ...,
) -> Command: ...
@overload
def command(
        arguments: Iterable[SupportArgument],
        /,
        parent: Command = ...,
        name: str | Text = ...,
        descr: str | Text = ...,
        usage: str | Text = ...,
        build: str | Text = ...,
        epilog: str | Text = ...,
        version: str | Text = ...,
        license: str | Text = ...,
        homepage: str | Text = ...,
        copyright: str | Text = ...,
        styles: Mapping[str, str | Style] = ...,
        conflicts: Iterable[Iterable[str]] = ...,
        *,
        lazy: bool = ...,
        shell: bool = ...,
        fancy: bool = ...,
        colorful: bool = ...,
        methodize: bool = ...,
) -> Command: ...
@overload
def command(
        *,
        parent: Command = ...,
        name: str | Text = ...,
        descr: str | Text = ...,
        usage: str | Text = ...,
        build: str | Text = ...,
        epilog: str | Text = ...,
        version: str | Text = ...,
        license: str | Text = ...,
        homepage: str | Text = ...,
        copyright: str | Text = ...,
        styles: Mapping[str, str | Style] = ...,
        conflicts: Iterable[Iterable[str]] = ...,
        lazy: bool = ...,
        shell: bool = ...,
        fancy: bool = ...,
        colorful: bool = ...,
        methodize: bool = ...,
) -> Callable[[Callable[..., Any]], Command]: ...

@overload
def invoke(invocable: Invocable, /) -> dict[str, Any] | None: ...
@overload
def invoke(invocable: Invocable, prompt: str | Iterable[str], /) -> dict[str, Any] | None: ...
# These are not expected to be used but are supported anyway
@overload
def invoke(callback: Callable[..., Any], /) -> None: ...
@overload
def invoke(callback: Callable[..., Any], prompt: str | Iterable[str], /) -> None: ...
@overload
def invoke(arguments: Iterable[SupportArgument], /) -> dict[str, Any]: ...
@overload
def invoke(arguments: Iterable[SupportArgument], prompt: str | Iterable[str], /) -> dict[str, Any]: ...
