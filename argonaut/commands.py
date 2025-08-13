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
import copy
import difflib
import functools
import inspect
import os
import re
import shlex
import sys
import textwrap
from collections import deque, defaultdict
from collections.abc import Sequence, Mapping, Set
from inspect import Parameter
from re import IGNORECASE
from types import MemberDescriptorType, MappingProxyType
from typing import Iterable, ChainMap
from warnings import catch_warnings

from rich.console import Console, Group
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from .arguments import Cardinal, Option, Flag
from .triggers import *
from .void import *


class SentinelException(Exception): ...


# Update the name of a callable and return itself to avoid exposing names like "_get_call.<locals>._decorator"
def _update_name(callable, newname=None):
    if not isinstance(callable, str):
        callable.__qualname__ = newname
        callable.__name__ = newname
        return callable
    return functools.partial(_update_name, newname=callable)


# Build the property for the dynamic class ensuring that the collections become immutable
def _build_property(name, metadata):
    if isinstance(metadata[name], Sequence) and not isinstance(metadata[name], str):
        metadata[name] = tuple(metadata[name])
    elif isinstance(metadata[name], Mapping):
        metadata[name] = MappingProxyType(metadata[name])
    elif isinstance(metadata[name], Set):
        metadata[name] = frozenset(metadata[name])
    return property(_update_name(lambda self: metadata[name], name))


# Generate the signature and the arguments for the dynamic call method
def _build_call(callback):
    # Inspect the original callback signature (ordered mapping of name -> Parameter)
    signature = inspect.signature(callback)

    # Avoid shadowing: if the callback’s first parameter is literally named "self",
    # rename the wrapper's instance parameter to "__command__" so both can coexist.
    self = "__command__" if next(iter(signature.parameters), None) == "self" else "self"

    # parameters: tokens for the generated function's parameter list
    # arguments: tokens used to forward the call into self._callback(...)
    parameters = []
    arguments = []

    # slashed: whether '/' (end of positional-only section) has been inserted
    # starred: whether '*' (start of keyword-only section) has been inserted
    slashed = False
    starred = False

    # Walk the original parameters in order and rebuild a compatible signature.
    # We emit:
    # - '/' once, right when we encounter the first non-positional-only param (only if there were pos-only params).
    # - '*' when we encounter keyword-only params (only once, and only if not already separated by *args).
    for name, parameter in signature.parameters.items():
        # If we transition from the (implicit) positional-only section to a non-positional-only param,
        # emit '/' exactly once. If there were no positional-only params, we'll delete the stray '/'
        # right after the loop (see below).
        if parameter.kind is Parameter.POSITIONAL_OR_KEYWORD and not slashed:
            parameters.append("/")  # Added only if there were positional-only parameters
            slashed = True

        # If we reach a keyword-only parameter, make sure the keyword-only section is introduced.
        # This is done by adding a bare '*'. (If *args existed, that would also serve as the separator.)
        if parameter.kind is Parameter.KEYWORD_ONLY:  # Correct: keyword-only section requires '*'
            parameters.append("*")
            starred = True

        # Always append the parameter name itself to the wrapper signature.
        parameters.append(name)

        # When forwarding:
        # - keyword-only parameters must be passed with 'name=name'
        # - others are forwarded positionally by name
        arguments.append(f"{name}={name}" if parameter.kind is Parameter.KEYWORD_ONLY else name)

    # If the very first token became '/', that means the callback had no positional-only parameters.
    # Remove it so we don't advertise a pos-only section that doesn't exist.
    if parameters and parameters[0] == "/":
        parameters.pop(0)

    # Prepend the instance parameter ('self' or '__command__') to the wrapper signature.
    parameters.insert(0, self)

    # Dynamically define the wrapper with the constructed signature and forward into _callback(...)
    exec(textwrap.dedent(f"""
        def function({", ".join(parameters)}):
            return {self}._callback({", ".join(arguments)})
    """), globals(), namespace := {})
    function = namespace["function"]

    # Rebuild defaults for non-keyword-only parameters:
    # - void.nullify(...) transforms sentinel defaults into runtime-safe values per project conventions.
    function.__defaults__ = tuple(
        void.nullify(parameter.default.default) for parameter in signature.parameters.values()
        if parameter.kind is not Parameter.KEYWORD_ONLY and parameter.default is not Parameter.empty
    )

    # For keyword-only parameters, record presence flags (per project conventions).
    function.__kwdefaults__ = dict(
        (name, False) for name, parameter in signature.parameters.items()
        if parameter.kind is Parameter.KEYWORD_ONLY
    )

    function.__annotations__ = callback.__annotations__
    return function


# Dynamically initiates a new type according to the metadata info
def _cmdtype(cls, metadata):
    __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()

    def _fallback(self, fallback):
        if not callable(fallback):
            raise TypeError(f"{__typename__} fallback must be callable")
        if self._fallback is not void:
            raise TypeError(f"{__typename__} fallback already set")
        self._fallback = fallback
        return fallback

    def _init_subclass():
        raise TypeError(f"type {__typename__!r} is not an acceptable base type")

    return type(cls)(
        __typename__,
        (cls,) + cls.__bases__,
        {  # Clear members of the class to avoid conflicts with the slots
            name: object for name, object in cls.__dict__.items() if not isinstance(object, MemberDescriptorType)
        } | {  # Inject the readonly attributes
            name: _build_property(name, metadata) for name in metadata.keys()
        } | {  # Inject the fallback setter (one-life usable)
            "fallback": _update_name(
                lambda self, fallback: _fallback(self, fallback), "fallback"
            ),
        } | {  # Inject the two reprs (including rich support)
            "__repr__": _update_name(
                lambda self: f"{__typename__}({", ".join("%s=%r" % item for item in metadata.items())})", "__repr__"
            ),
            "__rich_repr__": _update_name(
                lambda self: metadata.items(), "__rich_repr__"
            )
        } | {  # Inject the dynamic class final
            "__init_subclass__": _update_name(
                lambda cls, **options: _init_subclass(), "__init_subclass__"
            )
        } | ({
            "__call__": _update_name(
                _build_call(metadata["callback"]), "__call__"
            )
        } if metadata["callback"] is not _dummy_callback else {})
    )


def _dummy_callback(*args, **kwargs):
    raise NotImplementedError("dummy-callback is not intended to be called")


def _get_argument(x, ctx):
    if sum((
        hasattr(x, "__cardinal__") and callable(x.__cardinal__),
        hasattr(x, "__option__") and callable(x.__option__),
        hasattr(x, "__flag__") and callable(x.__flag__),
    )) != 1:
        if isinstance(ctx, str):
            raise TypeError(f"parameter {ctx!r} default must be argument-resoluble")
        raise TypeError(f"object at {ctx!r} position must be argument-resoluble")
    if hasattr(x, "__cardinal__"):
        argument = x.__cardinal__()
        if not isinstance(argument, Cardinal):
            raise TypeError("__cardinal__() non-cardinal return")
    elif hasattr(x, "__option__"):
        argument = x.__option__()
        if not isinstance(argument, Option):
            raise TypeError("__option__() non-option return")
    elif hasattr(x, "__flag__"):
        argument = x.__flag__()
        if not isinstance(argument, Flag):
            raise TypeError("__flag__() non-flag return")
    else:
        raise RuntimeError("unreachable")
    return argument


# Extract the arguments either from the callback signature or the iterable
def _resolve_callback(cls, metadata):
    __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()
    source = metadata.pop("source")

    if not callable(source) and not isinstance(source, Iterable):
        raise TypeError(f"{__typename__} first argument must be a callable or an iterable of argument-resoluble")

    active = "cardinals"
    greedy = False
    hidden = False
    deprecated = False

    def _inject_cardinal(x, ctx):
        nonlocal active, greedy, hidden, deprecated

        if callable(source):
            if ctx.kind is not Parameter.POSITIONAL_ONLY:
                raise TypeError(f"{__typename__} callback parameter {ctx.name!r} must be positional-only")
            if greedy:
                raise TypeError("cannot define more cardinals after a greedy one")
            hidden |= x.hidden
            if hidden and not x.hidden:
                raise TypeError(f"non-hidden cardinal at {ctx.name!r} cannot follow a hidden one")
            deprecated |= x.deprecated
            if deprecated and not x.deprecated:
                raise TypeError(f"non-deprecated cardinal at {ctx.name!r} cannot follow a deprecated one")
        else:
            if active != "cardinals":
                raise TypeError(f"cardinal at {ctx!r} position cannot be followed by options and flags")

        metadata["cardinals"][getattr(ctx, "name", ctx)] = x
        metadata["styles"].setdefault(f"group-{x.group}", getattr(x.group, "style", None))
        metadata["groups"][str(x.group)] += (x,)

    def _inject_switcher(x, ctx):
        nonlocal active

        if callable(source):
            if isinstance(x, Option) and ctx.kind is not Parameter.POSITIONAL_OR_KEYWORD:
                raise TypeError(f"{__typename__} callback parameter {ctx.name!r} must be standard")
            if isinstance(x, Flag) and ctx.kind is not Parameter.KEYWORD_ONLY:
                raise TypeError(f"{__typename__} callback parameter {ctx.name!r} must be keyword-only")
        else:
            if isinstance(x, Option) and active == "flags":
                raise TypeError(f"option at {ctx!r} position cannot be followed by flags")
        active = "options" if isinstance(x, Option) else "flags"

        for name in map(str, x.names):
            if name in metadata["switchers"]:
                raise TypeError(f"switcher {name!r} already defined")
            metadata[active][name] = x
        metadata["styles"].setdefault(f"group-{x.group}", getattr(x.group, "style", None))
        metadata["groups"][str(x.group)] += (x,)

    if callable(source):
        parameters = source.__parameters__ = list(inspect.signature(source).parameters.values())
        if metadata["methodize"]:
            try:
                parameter = parameters.pop(0)
            except IndexError:
                raise TypeError(f"methodize-ed {__typename__} callback must have at least one parameter") from None
            if parameter.default is not Parameter.empty:
                raise TypeError(f"methodize-ed {__typename__} callback first argument cannot have a default")
            if parameter.name not in ("self", "this"):
                raise TypeError(f"methodize-ed {__typename__} callback first argument must be named 'self' or 'this'")
            if parameter.kind not in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD):
                raise TypeError(f"methodize-ed {__typename__} callback first argument must be positional")
        for parameter in parameters:
            if parameter.default is Parameter.empty:
                raise TypeError(f"{__typename__} callback parameter {parameter.name!r} must have a default")
            if isinstance(argument := _get_argument(parameter.default, parameter.name), Cardinal):
                _inject_cardinal(argument, parameter)
            else:
                _inject_switcher(argument, parameter)
    else:
        if metadata["methodize"]:
            raise TypeError(f"{__typename__} built from iterables cannot be methodize-ed")
        for index, object in enumerate(source):
            if isinstance(argument := _get_argument(object, index), Cardinal):
                _inject_cardinal(argument, index)
            else:
                _inject_switcher(argument, index)
    metadata["callback"] = source if callable(source) else _dummy_callback


# Load all the metadata needed for help and version generations
def _resolve_help_metadata(cls, metadata, defaults):
    __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()
    callback = metadata["callback"]

    for name, object in map(lambda x: (x, metadata[x]), (
        "name",
        "descr",
        "usage",
        "build",
        "epilog",
        "version",
        "license",
        "homepage",
        "copyright",
    )):
        if object is not void and not isinstance(object, (str, Text)):
            raise TypeError(f"{__typename__} {name!r} must be a string")
        elif isinstance(object, (str, Text)) and not object:
            raise ValueError(f"{__typename__} {name!r} must be a non-empty string")
        metadata[name] = void.nullify(object, defaults.get(name))


# Load the style for help and version generations
def _resolve_styles(cls, metadata):
    __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    if not isinstance(metadata["styles"], Mapping):
        raise TypeError(f"{__typename__} styles must be a mapping of strings to strings or styles")
    styles = {}
    for name, style in metadata["styles"].items():
        if not isinstance(name, str) or style is not None and not isinstance(style, (str, Style)):
            raise TypeError(f"{__typename__} styles must be a mapping of strings to strings or styles")
        styles[name] = style or None  # Remove empty styles
    metadata["styles"] = styles


# Load the conflicts groups
def _resolve_groups(cls, metadata):
    __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    message = f"{__typename__} 'conflicts' must be an iterable of iterables of conflicting groups"
    if not isinstance(metadata["conflicts"], Iterable):
        raise TypeError(message)
    conflicts = defaultdict(frozenset)
    for conflict in metadata["conflicts"]:
        if not isinstance(conflict, Iterable):
            raise TypeError(message)
        try:
            conflict = set(conflict)
        except TypeError:
            raise TypeError(message) from None
        if len(conflict) < 2:
            raise TypeError("conflicting groups must have at least two elements")
        for group in conflict:
            if not isinstance(group, str):
                raise TypeError(message)
            elif group not in metadata["groups"]:
                raise TypeError(f"{__typename__} unknown group {group!r}")
            conflicts[group] |= frozenset(conflict - {group})
    metadata["conflicts"] = conflicts


# Attach the new instance to the parent if apply
def _attach_to_parent(metadata, child):
    parent = metadata["parent"]
    if parent is void:
        metadata["parent"] = None
        return  # Update the parent to expose None instead of void
    if not isinstance(parent, Command):
        raise TypeError(f"{child.__class__.__name__} parent must be any command")
    if parent.cardinals:
        raise TypeError(f"{child.__class__.__name__} parent must have no cardinals")
    if parent._children.setdefault(str(child.name), child) is not child:  # NOQA: Owned Attribute
        raise TypeError(f"parent delegated command {child.name!r} is duplicated")


class Command:
    """
    A node in a command tree that parses tokens, runs argument callbacks, and optionally
    dispatches to a handler. Can be used as the root command or as a subcommand.

    Construction
    - Via decorator (recommended for handler-backed commands):
        @command(name="cli")        # top-level
        def cli(file: str = Cardinal(), *, verbose: bool = Flag("-v", "--verbose")) -> None:
            ...

        @cli.command(name="sub")    # attach a child command to `cli`
        def sub(count: int = Option("-n", "--count", type=int, default=1)) -> None:
            ...
    - Via iterable (no handler; returns parsed namespace when invoked):
        cli = command([Cardinal(), Flag("-v", "--verbose")])
        sub = cli.command([Option("-n", "--count", type=int, default=1)])

    Behavior and features
    - Argument model:
        • Cardinal: positional argument specs (optionally greedy).
        • Option: named options that accept values (supports nargs semantics).
        • Flag: named switches without values.
    - Helpers:
        • -h/--help and -v/--version are injected if not provided.
    - Rendering and UX:
        • stderr/stdout properties expose Rich Consoles.
        • trigger(...) integrates with warnings/errors for shell/non-shell flows (see triggers).
        • lazy mode queues warnings/errors until parse finalization.
        • shell, fancy, colorful, styles influence how issues and help are shown.
    - Handler dispatch:
        • For decorator-built commands, the handler signature defines arguments using default
          values set to Cardinal/Option/Flag instances. The parser collects values and calls
          the handler with properly shaped arguments.
        • For iterable-built commands, no handler is attached; invoking returns a dict namespace.

    Key properties
    - parent: Command | None — parent node in the tree.
    - children: Mapping[str, Command] — attached subcommands by name.
    - cardinals/options/flags/switchers/groups/conflicts: read-only indices.
    - styles: Mapping[str, str|Style] — named styles for rendering.
    - lazy/shell/fancy/colorful/methodize: behavior flags.
    - stderr/stdout: Rich consoles, cached per instance.
    - patriarch: top-most ancestor in the tree (root).
    - rootpath: tuple of Commands from root to self (root may be excluded elsewhere).
    - namespace: deep copy of the last parsed mapping, or None.
    - tokens: deep copy of remaining token deque captured during the last parse, or None.

    Important methods
    - command(...):
        • Bound convenience wrapper for creating/attaching subcommands.
        • Accepts either a callable (decorator/factory) or an iterable of specs.
    - discover(module: str):
        • Attach all top-level orphan Command instances from a module/globbed package path.
    - trigger(x, **options):
        • Emit a warning/error or aggregate exit; merges standard rendering options automatically.
        • In shell mode, may show help before triggering.
    - __invoke__(prompt=void):
        • Parse and execute. Accepts:
          — void (default): sys.argv[1:],
          — str: tokenized with shlex.split,
          — Iterable[str]: used as-is.
        • Returns handler result or a namespace dict for iterable-defined commands.
    - __replace__(**overrides):
        • Clone this command with selected metadata overridden (e.g., parent, name, descr).
        • Reuses the original handler or rebuilds from specs if no handler is attached.

    Notes
    - Only orphan commands can be attached to a parent; parentage is set during construction.
    - Parent commands with cardinals cannot accept subcommands.
    - Option explicit=True requires attached values (e.g., --opt=value).
    - methodize=True lets handlers be written as instance-style callables (first arg self/this).
    """

    __slots__ = (
        "_children",
        "_callback",
        "_fallback",
        "_triggers",
        "_namespace",
        "_tokens",
    )

    @property
    @functools.cache
    def stderr(self):
        return Console(stderr=True)

    @property
    @functools.cache
    def stdout(self):
        return Console()

    @property
    @functools.cache
    def patriarch(self):
        """
        Return the root command in this hierarchy.

        Behavior
        - Walks parent links to the top-most ancestor and returns it.
        - Memoized per-instance.

        Notes
        - Consider naming this 'root' or 'root_command' for clarity.
        """
        child, parent = self, self.parent
        while parent:
            child, parent = parent, parent.parent
        return child

    @property
    def rootpath(self):
        """
        Return the ancestor path (as a list) excluding the root, ordered from closest ancestor down to self.

        Behavior
        - The list is ordered from the nearest ancestor to this command down to this command itself.
          The root (top-most ancestor whose parent is None) is excluded.
        - If this command has no parent, returns an empty list.
        - Memoized per-instance.

        Notes
        - If you prefer a different ordering or inclusion:
          • Include the root: append the final root after collection (before reversing), or compute separately.
          • Order from self upward: return the list without reversing.
          • Exclude self: start from `parent` instead of `self` when collecting.
        """
        rootpath = []
        command = self
        while command:
            rootpath.append(command)
            command = command.parent
        return tuple(reversed(rootpath))

    @property
    def namespace(self):
        """
        Return a deep copy of the last parsed namespace, or None if not available.
        """
        if self._namespace is void:  # type: ignore[attr-defined]
            return
        return copy.deepcopy(self._namespace)  # type: ignore[attr-defined]

    @property
    def tokens(self):
        """
        Return a deep copy of the remaining token deque captured during the last parse, or None if not available.
        """
        if self._tokens is void:  # type: ignore[attr-defined]
            return
        return copy.deepcopy(self._tokens)  # type: ignore[attr-defined]

    def __new__(
            cls,
            source,
            /,
            parent=void,
            name=void,
            descr=void,
            usage=void,
            build=void,
            epilog=void,
            version=void,
            license=void,
            homepage=void,
            copyright=void,
            styles={},  # NOQA: Sentinel not used
            conflicts=(),
            *,
            lazy=void,
            shell=void,
            fancy=void,
            colorful=void,
            methodize=False,
    ):
        metadata = {
            # Identity & hierarchy
            "parent": parent,
            "name": name,
            "descr": descr,

            # Structure
            "children": (children := {}),
            "groups": defaultdict(tuple),
            "conflicts": conflicts,
            "cardinals": {},
            "switchers": ChainMap(options := {}, flags := {}),  # ease merging
            "options": options,
            "flags": flags,

            # Appearance (exposed; frozen by _build_property)
            "styles": styles,

            # Behavior
            "lazy": bool(void.nullify(lazy, getattr(parent, "lazy", False))),
            "shell": bool(void.nullify(shell, getattr(parent, "shell", False))),
            "fancy": bool(void.nullify(fancy, getattr(parent, "fancy", False))),
            "colorful": bool(void.nullify(colorful, getattr(parent, "colorful", False))),
            "methodize": bool(methodize),

            # Volatile (kept last; popped/consumed later)
            "source": getattr(source, "_callback", source),  # Due to the way to alias commands is stacking decorators
        }
        _resolve_styles(cls, metadata)
        _resolve_callback(cls, metadata)
        _resolve_help_metadata(cls, metadata, {
            "name": getattr(source, "__name__", os.path.basename(sys.argv[0])),
            "descr": inspect.getdoc(source) if callable(source) else void,
        })
        _resolve_groups(cls, metadata)
        _attach_to_parent(metadata, self := super().__new__(_cmdtype(cls, metadata)))

        # Load default helpers
        if not all(name in metadata["switchers"] for name in ("-h", "--help")):
            help = flags["-h"] = flags["--help"] = Flag("-h", "--help", helper=True)
            help.callback(self._show_help)
        if not all(name in metadata["switchers"] for name in ("-v", "--version")):
            version = flags["-v"] = flags["--version"] = Flag("-v", "--version", helper=True)
            version.callback(self._show_version)

        # Volatiles
        self._callback = metadata.pop("callback")
        # Need mutability to attach
        self._children = children
        # Fallbacks
        self._fallback = void
        self._triggers = []
        # Runtimes
        self._namespace = void
        self._tokens = void
        return self

    def _show_help(self):
        console = (
            self.stderr
            if any(isinstance(trigger, CommandException) for trigger in self._triggers) else
            self.stdout
        )
        console.print("NO HELP HERE")

    def _show_version(self):
        console = self.stdout
        console.print("NO VERSION HERE")

    def command(self, x=void, /, *args, **kwargs):
        """
        Create or decorate a subcommand with this command set as its parent.

        This is a convenience wrapper around the top-level `command(...)` that pre-binds
        `parent=self`, so you can define child commands close to where they belong.

        Usage
        - Decorator form (x is the default sentinel):
            @self.command(name="child")
            def run(...): ...
          Returns a decorator that, when applied, builds and attaches the child Command.

        - Factory form (x is provided):
            child = self.command(x, name="child")
          Immediately builds and attaches the child Command from a callable or an iterable of argument specs.

        Parameters
        - x: positional-only
          • void (default) to obtain a decorator, or
          • a callable (callback) or an iterable of argument specs to build a child Command directly.
        - *args, **kwargs:
          Forwarded unchanged to the top-level `command(x, parent=self, *args, **kwargs)`.

        Returns
        - In decorator form: a decorator that produces and attaches the child Command.
        - In factory form: the constructed child Command.

        Notes
        - Only orphan commands can be attached; parent assignment happens during construction/
          attachment and existing parents are not overridden.
        - This helper does not accept an already-constructed Command instance as `x`;
          pass a callable or an iterable of argument specs instead.
        """
        # Delegate to the global factory/decorator while pre-binding the parent to `self`.
        return command(x, self, *args, **kwargs)

    def discover(self, module, /):
        """
        Bind all top-level orphan Command instances from a module or modules.

        Parameters
        - module: str (positional-only)
          • Exact dotted module path (e.g., "package.subpackage.commands"), or
          • A glob pattern (e.g., "core.commands.*" or "core.commands.**") to match multiple modules.

        Behavior
        - Exact path: imports the target module and attaches orphan Command instances found at its top level.
        - Glob path: imports the anchor package (prefix before the first glob char) and scans all submodules;
          each module whose full dotted name matches the glob is imported and attached similarly.

        Raises
        - TypeError: if `module` is not a string.
        - LookupError: if the target module cannot be imported, or for glob patterns if no matches are found.

        Notes
        - Matching uses fnmatch on full dotted module names, so '*' matches across dots.
        - Only top-level attributes of each imported module are considered; no recursive attribute traversal.
        - Already-parented commands are skipped; only orphans are attached.
        """
        # Validate argument
        if not isinstance(module, str):
            raise TypeError("bind argument must be a string")

        # Detect glob usage
        has_glob = any(ch in module for ch in "*?[]")

        if not has_glob:
            # Exact module import
            try:
                mod = __import__("importlib").import_module(module)
            except ImportError:
                raise LookupError(f"{type(self).__name__} unable to bind commands from module {module!r}") from None

            for attr in dir(mod):
                if isinstance(object := getattr(mod, attr), Command) and object.parent is None:
                    copy.replace(object, parent=self)
            return self

        # Globbed import
        importlib = __import__("importlib")
        pkgutil = __import__("pkgutil")
        fnmatch = __import__("fnmatch").fnmatch

        # Find the non-glob anchor prefix (before first glob char)
        first_glob = min((i for i in (module.find("*"), module.find("?"), module.find("[")) if i != -1), default=-1)
        anchor = module[:first_glob].rstrip(".") or module  # fallback, though glob implies first_glob != -1

        # Import the anchor package
        try:
            anchor_mod = importlib.import_module(anchor)
        except ImportError:
            raise LookupError(f"{type(self).__name__} unable to bind commands from pattern {module!r}: "
                              f"anchor {anchor!r} cannot be imported") from None

        # Ensure anchor is a package
        if not hasattr(anchor_mod, "__path__"):
            raise LookupError(f"{type(self).__name__} unable to bind commands from pattern {module!r}: "
                              f"anchor {anchor!r} is not a package")

        # Walk packages under anchor and collect matches
        matched_any = False
        for finder, name, ispkg in pkgutil.walk_packages(anchor_mod.__path__, prefix=anchor_mod.__name__ + "."):
            if not fnmatch(name, module):
                continue
            matched_any = True
            try:
                mod = importlib.import_module(name)
            except ImportError:
                # Skip modules that fail to import; you may choose to accumulate and report if desired
                continue
            for attr in dir(mod):
                if isinstance(object := getattr(mod, attr), Command) and object.parent is None:
                    copy.replace(object, parent=self)

        if not matched_any:
            raise LookupError(f"{type(self).__name__} unable to bind commands from pattern {module!r}: no matches")

        return self

    def trigger(self, x, /, **options):
        # Lazy mode: queue the trigger and merged options for later processing.
        # Right operand of "|" wins, so built-ins (cmd, shell, styles, colorful) override user-provided ones.
        if self.lazy:
            self._triggers.append((x, options | {  # type: ignore[attr-defined]
                "cmd": self,
                "shell": self.shell,
                "fancy": self.fancy,
                "styles": self.styles,
                "colorful": self.colorful
            }))

        # Fallback mode: invoke the fallback handler instead of triggering now.
        # Note: current behavior does NOT pass options to the fallback; add if needed.
        elif self._fallback is not void:  # type: ignore[attr-defined]
            self._fallback(x)  # type: ignore[attr-defined]

        else:
            # Interactive shell hint: show help first (still proceeds to trigger).
            # If you intend to only show help and stop, add `return` after the call below.
            if self.shell:
                self.flags["--help"]()

            # Immediate trigger: forward to the global trigger with merged options.
            # Keys in this dict literal override entries from **options (desired precedence).
            trigger(x, **{
                **options,
                "cmd": self,
                "shell": self.shell,
                "fancy": self.fancy,
                "styles": self.styles,
                "colorful": self.colorful
            })

    def _parse_switch(self, token):
        # Strictly parse a switch token:
        # - Names: "-x", "--long", "--long-name", case-insensitive, alnum with optional dashes.
        # - Optional inline parameter with "=" (captures even empty string to detect misuse).
        match = re.fullmatch(r"(?P<input>--?[A-Z](?:-?[A-Z0-9]+)*)(?:=(?P<param>(?s:.)*))?", token, IGNORECASE)

        # If the token does not match the expected syntax, emit a format error and abort parsing this token.
        if not match:
            self.trigger(InvalidFormatError(
                f"invalid option or flag format: {token!r}",
                token,
                hint="ensure the option or flag follows the expected syntax, including any required prefix or separator"
            ))
            raise SentinelException

        # Unpack the switch name and the (possibly None or empty) inline parameter.
        input, param = match.groupdict().values()

        # Unknown switch name: try to suggest close matches using the full alias set; then abort.
        if input not in self.switchers:
            suggestions = difflib.get_close_matches(input, self.switchers.keys(), 5)
            try:
                hint = f"did you mean: {suggestions.pop(0)!r}?"
            except IndexError:
                hint = "check the spelling or run --help to see available options"
            self.trigger(UnrecognizedOptionError(
                f"unrecognized option or flag: {input!r}",
                input,
                hint=hint,
            ))
            raise SentinelException

        # Inline "=" present but with an empty value:
        # - For flags, parameters are not allowed → instruct to remove "=...".
        # - For options, an empty value is invalid → instruct how to provide it properly.
        if isinstance(param, str) and not param:
            if isinstance(self.switchers[input], Flag):  # subclasses ok
                message = f"flag does not take a parameter: {input!r}"
                hint = "remove '=parameter' from the flag usage"
            else:  # Option (or any subclass that takes parameters)
                message = f"empty parameter for option: {input!r}"
                hint = "provide a parameter after '=' or omit '=' to pass the parameter as the next token"
            self.trigger(ParameterWrongUsageError(
                message,
                self.switchers[input],
                input,
                hint=hint,
            ))

        # Valid switch; return the canonical name and its inline parameter (or None).
        return input, param

    def _getparams(self, tokens, input, argument, *, offset):
        kind = "option" if isinstance(argument, Option) else "flag"
        if getattr(argument, "greedy", False):
            result = []
            while tokens:
                result.append(tokens.popleft())
        else:
            peekable = lambda: tokens and not tokens[0].startswith("-")
            nargs = argument.nargs
            if not nargs or nargs == "?":
                if not nargs:
                    self.trigger(MissingParameterError(
                        f"missing required parameter for {input!r} {kind}",
                        input,
                        expected_min=1, got=0,
                        hint="provide the parameter after the option or as the next token",
                        offset=offset,
                    ))
                result = tokens.popleft() if peekable() else void
            elif nargs in ("*", "+"):
                result = []
                while peekable():
                    result.append(tokens.popleft())
                if nargs == "+" and not result:
                    self.trigger(MissingParameterError(
                        f"missing required parameter for {input!r} {kind}",
                        input,
                        expected_min=1, got=0,
                        hint="provide at least one parameter",
                        offset=offset,
                    ))
            else:
                result = []
                while peekable() and len(result) < nargs:
                    result.append(tokens.popleft())
                if len(result) != nargs:
                    self.trigger(ArityMismatchError(
                        f"expected {nargs} parameter{'s' if nargs != 1 else ''} for {input!r} {kind}, "
                        f"got {len(result)}",
                        input,
                        expected=nargs, got=len(result),  # type: ignore[misc]
                        hint="check the number of parameters provided",
                        offset=offset,
                    ))

        if isinstance(result, list):
            for index, string in enumerate(result):
                try:
                    with catch_warnings(record=True) as warnings:
                        result[index] = argument.type(string)
                    for warning in warnings:
                        if isinstance(warning, CommandWarning):
                            self.trigger(warning)
                        else:
                            self.trigger(ParameterCoercionWarning(
                                str(getattr(warning, "message", warning)),
                                input,
                                string,
                                hint="verify the interpreted parameter",
                            ))
                except CommandException as exception:
                    self.trigger(exception)
                except Exception as exception:  # NOQA
                    self.trigger(ParameterConversionError(
                        f"invalid parameter for {input!r} {kind}: {string!r}",
                        input,
                        string,
                        hint="check the expected type and format",
                        offset=offset,
                    ))
        else:
            try:
                with catch_warnings(record=True) as warnings:
                    result = argument.type(result)
                for warning in warnings:
                    if isinstance(warning, CommandWarning):
                        self.trigger(warning)
                    else:
                        self.trigger(ParameterCoercionWarning(
                            str(getattr(warning, "message", warning)),
                            input,
                            result if isinstance(result, str) else str(result),
                            hint="verify the interpreted value",
                        ))
            except CommandException as exception:
                self.trigger(exception)
            except Exception as exception:  # NOQA
                self.trigger(ParameterConversionError(
                    f"invalid value for {input!r} {kind}: {result!r} ",
                    input,
                    result,
                    hint="check the expected type and format",
                    offset=offset,
                ))

        return result

    def _finalize(self, *, helper=False):
        # If nothing was enqueued during parsing, there’s nothing to do.
        if not self._triggers:
            return

        # If a fallback handler was registered, delegate all collected triggers
        # (warnings/exceptions) to it and stop here.
        if self._fallback is not void:
            self._fallback(self._triggers)
            return

        # Partition all collected triggerables into:
        # - warnings: list of (CommandWarning, options)
        # - exceptions: list[CommandException]
        # - specifics: mapping CommandException -> options
        warnings, exceptions, specifics = [], [], {}
        for triggerable, options in self._triggers:
            options = options or {}
            if isinstance(triggerable, CommandWarning):
                warnings.append((triggerable, options))
            elif isinstance(triggerable, CommandException):
                exceptions.append(triggerable)
                specifics[triggerable] = options

        # Emit all warnings first (non-fatal). Each keeps its own options.
        for warning, options in warnings:
            trigger(warning, **options)

        try:
            # Aggregate any exceptions into a CommandExit.
            # If there are no exceptions, CommandExit(...) should raise ValueError
            # (caught below), which we treat as "nothing to exit for".
            exit = CommandExit("bad exit", exceptions)

            # In interactive shell mode, optionally show help once before exiting.
            # The 'helper' flag prevents printing help twice when the help flag
            # itself triggered this finalizer.
            if self.shell and not helper:
                self.flags.get("--help", self._show_help)()

            # Finally, trigger the aggregated exit with the standard rendering options
            # and the 'specifics' mapping so the printer can render per-exception details.
            trigger(
                exit,
                cmd=self, shell=self.shell, fancy=self.fancy, styles=self.styles, colorful=self.colorful,
                specifics=specifics,
            )
        except ValueError:
            # No exceptions → nothing to exit for.
            pass

    def _parse(self, tokens, *, triggers=()):
        self._triggers[::] = triggers  # Clean old triggers if any

        assert self._namespace is void and self._tokens is void, "illegal state"
        self._namespace = dict()
        self._tokens = tokens

        number = lambda number: {
            1: "first",
            2: "second",
            3: "third",
            4: "fourth",
            5: "fifth",
            6: "sixth",
            7: "seventh",
            8: "eighth",
            9: "ninth",
            10: "tenth",
        }.get(number, f"{number}th")

        groups = set()
        called = set()

        remaining = lambda: cardinals and self.cardinals[cardinals[0]].greedy
        cardinals = deque(self.cardinals.keys())
        offset = 0
        index = 0
        runt = False

        while self._tokens:
            token = self._tokens.popleft()

            if token.startswith("-") and not remaining():
                try:
                    input, param = self._parse_switch(token)
                except SentinelException:
                    continue
                argument = self.switchers[input]
            elif not self._namespace and self.children and not runt:
                try:
                    return self.children[token]._parse(tokens, triggers=self._triggers)  # NOQA: Owned Attribute
                except KeyError:
                    suggestions = difflib.get_close_matches(token, list(self.children.keys()), n=5, cutoff=0.6)
                    kind = "command" if self.parent is None else "subcommand"
                    hint = (
                        f"did you mean {suggestions[0]!r}?"
                        if suggestions
                        else ("run --help to see available commands" if self.parent is None
                              else "run --help to see available subcommands")
                    )
                    self.trigger(UnknownCommandError(
                        f"unrecognized {kind}: {token!r}",
                        token,
                        hint=hint,
                    ))
                runt = True
                continue
            else:
                try:
                    argument = self.cardinals[input := cardinals.popleft()]
                    param = void
                    index += 1
                except IndexError:
                    offset += 1
                    self.trigger(UnexpectedPositionalArgumentError(
                        f"unexpected positional argument: {token!r} at {number(offset)} position",
                        offset,
                        hint="remove the extra argument or run --help to see expected positionals",
                    ))
                    continue
                self._tokens.appendleft(token)

            # Deprecated
            if argument.deprecated and not argument.hidden:
                if isinstance(argument, Cardinal):
                    kind = "positional argument"
                elif isinstance(argument, Option):
                    kind = "option"
                else:
                    kind = "flag"
                self.trigger(DeprecatedArgumentWarning(
                    f"usage of {input!r} at {number(offset + 1)} position is deprecated",
                    argument,
                    hint=f"remove the {kind} or run --help to see available {kind}s",
                ))

            # Conflicting group
            if conflicts := self.conflicts[group := str(argument.group)] & groups:
                self.trigger(GroupConflictError(
                    f"conflicting argument group: {group!r}",
                    group,
                    hint=f"remove one of: {', '.join(sorted(map(str, conflicts | {group})))}",
                ))
            groups.add(group)

            # Already parsed
            if input in self._namespace:
                self.trigger(DuplicateArgumentError(
                    f"duplicate argument (or an alias): {input!r}",
                    input,
                    hint="specify the argument only once",
                ))

            # Argument must be the first parsed (no namespace)
            if getattr(argument, "standalone", False):
                if isinstance(argument, Option):
                    kind = "option"
                else:
                    kind = "flag"
                if self._namespace or any(self._tokens[index].startswith("-") for index in range(1, len(self._tokens))):
                    self.trigger(StandaloneUsageError(
                        f"{kind} {input!r} should be specified alone",
                        input,
                        hint="invoke it without other arguments",
                    ))

            if isinstance(argument, Flag):
                self._namespace |= dict.fromkeys(map(str, argument.names), True)
            elif isinstance(argument, Option):
                if argument.explicit and not param:
                    self.trigger(ParameterWrongUsageError(
                        f"missing required inline parameter for option: {input!r}",
                        argument,
                        input,
                        hint="use '--name=parameter' (inline) rather than spacing the parameter",
                    ))
                self._namespace |= dict.fromkeys(map(str, argument.names), self._getparams(
                    deque(param.split(",")) if param else self._tokens,
                    input,
                    argument,
                    offset=offset
                ))
            else:
                self._namespace[input] = self._getparams(
                    self._tokens,
                    index,
                    argument,
                    offset=offset
                )

            if (not hasattr(argument, "nargs")
                    or (not argument.nargs or argument.nargs == "?")
                    and not getattr(argument, "greedy", False)):
                offset += 1
            else:
                offset += len(self._namespace[input])

            if argument.nowait and argument not in called:
                if isinstance(argument, Flag):
                    argument()
                elif (not argument.nargs or argument.nargs == "?") and not getattr(argument, "greedy", False):
                    argument(self._namespace[input])
                elif not isinstance(argument.nargs, int) or len(self._namespace[input]) == argument.nargs:
                    argument(*self._namespace[input])  # To avoid the type error of missing arguments

                if getattr(argument, "terminator", False):
                    self._finalize(helper="--help" in map(str, getattr(argument, "names", ())))
                    if self.shell:
                        sys.exit(0)
                    self._namespace = void
                    self._tokens = void
                    return

                called.add(argument)

        # Unparsed arguments
        if self._tokens:
            leftover = tuple(self._tokens)
            self.trigger(UnparsedTokensError(
                f"unparsed {'argument' if len(leftover) == 1 else 'arguments'}: "
                f"{', '.join(map(repr, leftover))}",
                leftover,
                hint="remove extra arguments or check your syntax with --help",
            ))

        arguments = {}
        arguments.update(self.cardinals)
        arguments.update((str(switcher.names[0]), switcher) for switcher in set(self.switchers.values()))

        # Complete the no handled yet if any
        for input, argument in arguments.items():
            if argument in called or input not in self._namespace:
                continue
            if isinstance(argument, Flag):
                argument()
            elif (not argument.nargs or argument.nargs == "?") and not getattr(argument, "greedy", False):
                argument(self._namespace[input])
            elif not isinstance(argument.nargs, int) or len(self._namespace[input]) == argument.nargs:
                argument(*self._namespace[input])  # To avoid the type error of missing arguments

        while cardinals:
            # Get the next cardinal and its nargs in one shot
            nargs = (cardinal := self.cardinals[input := cardinals.popleft()]).nargs

            # Required if: not greedy, and (nargs is None, "+", or an int)
            if not cardinal.greedy and (not nargs or nargs == "+" or isinstance(nargs, int)):
                self.trigger(MissingArgumentError(
                    f"missing required positional argument: {input!r}",
                    input,
                    hint="run --help to see expected positionals",
                ))
            self._namespace[input] = cardinal.default

        # Before retrieve the result sanitized, all pending triggers must be processed
        # Non-lazy or no-errored commands this statement has no effect
        self._finalize()

        if self._callback is _dummy_callback:
            for option in self.options.values():
                for name in map(str, option.names):
                    self._namespace.setdefault(name, option.default)
            for flag in self.flags.values():
                for name in map(str, flag.names):
                    self._namespace.setdefault(name, False)
            namespace, self._namespace = self._namespace, void
            self._tokens = void
            return {key: void.nullify(value) for key, value in namespace.items()}

        args, kwargs = (), {}
        for parameter in self._callback.__parameters__:
            argument = parameter.default
            if parameter.kind is not Parameter.KEYWORD_ONLY:
                args += (void.nullify(self._namespace.get(
                    str(getattr(argument, "names", (parameter.name,))[0]), argument.default
                )),)
            else:
                kwargs[parameter.name] = self._namespace.get(str(argument.names[0]), False)

        self._namespace = void
        self._tokens = void
        if self.methodize:
            self(self, *args, **kwargs)  # NOQA: Call Dynamically Injected
        else:
            self(*args, **kwargs)  # NOQA: Call Dynamically Injected

    def __invoke__(self, prompt=void, /):
        """
        Parse and execute this command.

        Parameters
        - prompt: positional-only
          • void (default): use process arguments (sys.argv[1:]).
          • str: split with shlex.split(prompt).
          • iterable[str]: tokens are taken as-is.
          Any other type, or any iterable containing non-str items, raises TypeError.

        Returns
        - The result of self._parse(...), which typically is:
          • The produced namespace (dict) when no handler/callback is attached, or
          • The callback’s return value if a handler is attached.

        Notes
        - This method is the primary entry point for running a command.
        - Tokenization via shlex.split honors shell-like quoting rules.
        """
        if prompt is void:
            tokens = sys.argv[1:]
        elif isinstance(prompt, str):
            tokens = shlex.split(prompt)
        else:
            try:
                tokens = list(prompt)
            except TypeError:
                raise TypeError("invoke argument must be a string or an iterable of strings") from None
            if any(not isinstance(token, str) for token in tokens):
                raise TypeError("invoke argument must be a string or an iterable of strings") from None

        # Delegate to the parser. If no handler is attached, this yields the namespace;
        # otherwise it triggers the callback and returns its result.
        return self._parse(deque(tokens))  # type: ignore[attr-defined]

    def __replace__(self, **overrides):
        """
        Return a new Command cloned from this one, overriding selected metadata.

        Source selection
        - If this command was created from a callback, reuse that callback.
        - If it was created from argument specs (and thus uses the internal _dummy_callback),
          rebuild from the current specs (cardinals, options, flags).

        Supported overrides (keyword-only)
        - parent, name, descr, styles, conflicts, lazy, shell, fancy, colorful, methodize

        Notes
        - descr preserves “unset” semantics by passing the internal sentinel when None.
        - conflicts expects an iterable of iterables; the current mapping is converted accordingly.
        - Unknown override names raise TypeError.
        """
        # Decide which source to reuse: the real callback or the current specs
        try:
            is_dummy = (self._callback is _dummy_callback)  # type: ignore[attr-defined]
        except NameError:
            # Fallback if symbol not visible (shouldn't happen if defined in this module)
            is_dummy = False
        seen = set()
        src = (
                list(self.cardinals.values()) +
                list(option for option in self.options.values() if seen.add(option) or option not in seen) +
                list(flag for flag in self.flags.values() if seen.add(flag) or flag not in seen)
        ) if is_dummy else self._callback  # type: ignore[attr-defined]

        # Build constructor kwargs from current values unless overridden
        kwargs = {
            "parent": overrides.pop("parent", self.parent),
            "name": overrides.pop("name", self.name),
            "descr": overrides.pop("descr", self.descr if self.descr is not None else void),
            "styles": overrides.pop("styles", self.styles),
            "conflicts": overrides.pop("conflicts", tuple(self.conflicts.values())),
            "lazy": overrides.pop("lazy", self.lazy),
            "shell": overrides.pop("shell", self.shell),
            "fancy": overrides.pop("fancy", self.fancy),
            "colorful": overrides.pop("colorful", self.colorful),
            "methodize": overrides.pop("methodize", self.methodize),
        }

        if overrides:
            unknown = ", ".join(sorted(overrides.keys()))
            raise TypeError(f"__replace__() got unexpected keyword(s): {unknown}")

        if type(self).__bases__ == (Command, *Command.__bases__):
            return Command(src, **kwargs)
        return type(self)(src, **kwargs)


def command(x=void, /, *args, **kwargs):
    """
    Decorator/factory for building a Command.

    Usage patterns
    - As a decorator:
        @command(...)
        def main(...) -> None: ...
      In this form, `x` is the function being decorated.

    - As a factory with a source (iterable of argument specs or a callable):
        cmd = command(source, ...)
      Here `x` is the source used to construct the Command.

    Parameters
    - x: positional-only. Either:
        • void (default) when used as a decorator, or
        • a source (callable or iterable of specs) to be wrapped into a Command.
    - *args, **kwargs: forwarded to Command(...) as-is.

    Returns
    - When used as a decorator (x is void): a decorator function that, when applied,
      returns a Command instance built from the decorated object.
    - When used as a factory (x is not void): a Command instance.

    Errors
    - Raises TypeError if used as a decorator but applied to a non-callable.
    """
    # When x is void we are returning a decorator; otherwise we directly construct Command.
    # This prevents the ambiguous pattern command(...)(<iterable>) by requiring command(<iterable>, ...).
    decorating = x is void  # Force usage of `command(<iterable>, ...)` instead of `command(...)(<iterable>)`

    @_update_name("command")
    def decorator(x):
        # If used as a decorator, ensure the decorated target is callable.
        if decorating and not callable(x):
            raise TypeError("@command() must be applied to a callable")
        # Construct and return the Command. All extra args/kwargs are passed through.
        return Command(x, *args, **kwargs)

    # If x is provided, build immediately; otherwise return the decorator to be applied later.
    return decorator(x) if x is not void else decorator


def invoke(x, prompt=void, /):
    """
    Execute an invocable command or wrap-and-invoke a source.

    Primary path
    - If `x` implements __invoke__, call that with the provided `prompt`.
      If `x` is a child command, its 'patriarch' is invoked instead (children cannot be given).

    Permissive fallback
    - If `x` does not implement __invoke__, attempt to wrap it with command(x) and
      then invoke the resulting Command. This is a convenience path; explicit usage
      (invoke(command(x), prompt)) is preferred for clarity.

    Parameters
    - x: positional-only. Either an object that implements __invoke__, or a callable/iterable
         that can be wrapped into a Command.
    - prompt: positional-only. Prompt/context passed through to __invoke__.

    Returns
    - The result of the underlying __invoke__ call.

    Errors
    - If `x` neither implements __invoke__ nor qualifies for the permissive wrapping,
      a TypeError is raised. A second, more specific TypeError is raised when the
      permissive fallback fails binding.
    """
    # Direct invocation path: allow objects with __invoke__ (e.g., Command instances).
    if hasattr(x, "__invoke__"):
        # If `x` has a 'patriarch' (root command), invoke that instead of a child.
        return getattr(x, "patriarch", x).__invoke__(prompt)  # children cannot be given

    # Permissive fallback: attempt to wrap a callable or an iterable of specs into a Command.
    # Note: this branch requires BOTH "callable(x)" AND "isinstance(x, Iterable)" to be true
    # to proceed; otherwise we raise. If you intended "callable OR iterable", adjust the check.
    # Prefer explicit usage: invoke(command(x), prompt)
    if not callable(x) or not isinstance(x, Iterable):
        raise TypeError("invoke() first argument must implement __invoke__ method") from None

    try:
        # Wrap then recurse into the primary path.
        return invoke(command(x), prompt)
    except TypeError:
        # If wrapping fails (e.g., due to incompatible source), surface a clearer error.
        raise TypeError("invoke() permissive fallback bad argument") from None


__all__ = (
    "Command",
    "command",
    "invoke"
)
