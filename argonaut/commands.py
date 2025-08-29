"""
Argonaut command layer: build, compose, and run CLI commands.

What this module provides
- Command: wraps a Python callable into an executable CLI with:
  • Argument discovery from the callable’s defaults (Cardinal, Option, Flag).
  • Hierarchies (parent/child) to model subcommands.
  • Polished help/version renderers (Rich-based, color-aware).
  • Robust parsing with empiric, position-first messages and actionable hints.
  • Deferred fault handling and graceful finalization.

- Factories and helpers:
  • command(...): create a Command or a decorator that produces one.
  • invoke(obj, prompt): convenience runner for Commands or plain callables.
  • include(pattern): discover and mount external command templates.

Core ideas
- Signature-driven UX: the wrapped function’s parameters define the CLI surface.
- Stable introspection: generated __call__ mirrors the underlying signature.
- Friendly diagnostics: errors and warnings start with an ordinal (“from third
  position”) to help users learn-by-trying.
- Styling that adapts: color and panel chrome are configurable per run.

Quick start (alternative to the example you might have seen)
    from argonaut import command, Cardinal, Option, Flag, invoke

    # Define a command with one positional, one option, and one flag
    @command(shell=True, fancy=True, colorful=True)
    def tool(
        path=Cardinal("PATH", type=str),        # positional
        /,
        count=Option("--count", type=int, nargs="?", default=1),
        *,                                       # keyword-only section
        verbose=Flag("-v", "--verbose"),
    ) -> None:
        # do your work here (this body runs after parsing + validation)
        print(dict(path=path, count=count, verbose=verbose))

    # Run it from a shell-like string (parsing mirrors standard CLIs)
    if __name__ == "__main__":
        invoke(tool, "--count=2 -v ./README.md")

Design notes
- Commands can be composed (parent → children) to build subcommand trees.
- Options support inline (--name=value) and spaced forms (--name value).
- Flags are presence-only (boolean); cardinals are position-based values.
- Faults are grouped and shown once in finalize; help can be auto-printed.

See also
- argonaut.arguments for spec builders and argument semantics.
- argonaut.faults for fault codes and rendering behavior.
"""
import builtins
import copy
import difflib
import functools
import importlib
import inspect
import itertools
import operator
import os.path
import re
import shlex
import sys
import textwrap
from collections import defaultdict, deque
from collections.abc import Iterable
from inspect import Parameter
from types import EllipsisType
from warnings import catch_warnings

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.containers import Lines
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .arguments import Cardinal, Option, Flag, flag
from .faults import *
from .utils import *


def _invoker(callback):
    """
    Build a trampoline __call__ that mirrors the callback's signature and forwards into self._callback.

    Why
    - Command instances behave like the underlying function they wrap. This keeps call-sites natural and
      enables delegation patterns (e.g., run() internally invoking build()) without re-implementing logic.

    Behavior
    - Inspects the callback's parameters and synthesizes a matching __call__(self, ...) implementation.
    - Preserves call semantics:
      • Inserts a positional-only slash (/) when the first POSITIONAL_OR_KEYWORD is encountered,
        so positional parameters remain positional-only from the outside.
      • Inserts a star (*) before the first KEYWORD_ONLY parameter, preserving keyword-only semantics.
      • Uses the parameter names as-is to keep introspection and error messages consistent.
    - Binds defaults and keyword-only defaults on the generated function for correct help/inspection:
      • __defaults__ carries positional defaults in order (from signature).
      • __kwdefaults__ sets keyword-only names to False by default (toggle flags, opt-ins).

    Notes
    - If a parameter named 'self' already exists in the callback, the trampoline uses '__self__' to avoid
      rebinding conflicts while still providing an instance-like first parameter for exec'ed code.

    Returns
    - The generated function object suitable to be assigned as Command.__call__.
    """
    parameters = inspect.signature(callback).parameters.values()

    # Choose an instance parameter name that won't collide with the callback's actual parameters.
    signature = [self := "self" if "self" not in map(lambda x: x.name, parameters) else "__self__"]
    arguments = []
    slashed = False
    starred = False

    for parameter in parameters:
        # First POSITIONAL_OR_KEYWORD => mark prior names positional-only for external callers.
        if parameter.kind is Parameter.POSITIONAL_OR_KEYWORD and not slashed:
            signature.append("/")
            slashed = True
        # First KEYWORD_ONLY => require '*' before subsequent keyword-only arguments.
        if parameter.kind is Parameter.KEYWORD_ONLY and not starred:
            signature.append("*")
            starred = True
        # Append the parameter name to the synthetic signature and build the forward list.
        signature.append(name := parameter.name)
        arguments.append(f"{name}={name}" if parameter.kind is Parameter.KEYWORD_ONLY else name)

    # If the only thing after the instance name was '/', drop it (edge case: no positional-or-keyword args).
    if len(signature) > 1 and signature[1] == "/":
        del signature[1]

    # Emit the forwarding trampoline with a clean, inspectable signature.
    exec(textwrap.dedent(f"""
        @rename("__call__")
        def __call__({", ".join(signature)}):
            return {self}._callback({", ".join(arguments)})
    """), globals(), namespace := locals())

    # Attach a helpful docstring to the generated method for introspection and help output.
    namespace["__call__"].__doc__ = textwrap.dedent(f"""
        Trampoline generated from callback={callback.__qualname__!s}.

        Signature
        - Mirrors the callback's parameters; positional-only (/) and keyword-only (*) markers are preserved.

        Forwarding
        - Calls self._callback({", ".join(arguments)}) with values as received.

        Defaults
        - __defaults__: positional defaults in the same order as the callback.
        - __kwdefaults__: keyword-only names default to False (toggle-friendly).
    """)

    # Populate positional defaults on the trampoline (skip keyword-only parameters).
    namespace["__call__"].__defaults__ = tuple(
        parameter.default.default for parameter in parameters if parameter.kind is not Parameter.KEYWORD_ONLY
    )

    # Provide keyword-only defaults as False to support boolean toggles in generated UIs.
    namespace["__call__"].__kwdefaults__ = dict.fromkeys(
        (parameter.name for parameter in parameters if parameter.kind is Parameter.KEYWORD_ONLY), False
    )

    return namespace["__call__"]


class CommandType(type):
    """
    Metaclass that turns callbacks into callable, introspectable Command classes.

    Responsibilities
    - Inject a trampoline __call__ on factory-backed Command classes that mirrors
      the wrapped callback's signature (built via _invoker(callback)). This keeps
      tracebacks, help, and IDE introspection clean and predictable.
    - Provide stable, readable __repr__/__rich_repr__ for diagnostics and rich UI.
    - Expose selected fields as read-only properties using mirror() for all names
      listed in __introspectable__.
    - Seal factory-backed Command subclasses against further subclassing so that
      generated instances have a predictable, immutable shape.

    Conventions
    - __typename__ is derived from the class name (camel-case split with hyphens)
      for consistent, human-friendly labels in logs and help.
    - __displayable__ (if set) narrows which properties are shown by __rich_repr__;
      otherwise __introspectable__ is used.

    Options (metaclass construction-time)
    - factory: when True, the resulting class represents a concrete, ready-to-use
      Command; it receives a generated __call__ and becomes non-subclassable.
    - callback: Callable used by _invoker to generate the forwarding __call__.

    Notes
    - The __module__ is tagged with a dynamic marker to make the synthesized
      origin explicit in tooling.
    """
    __introspectable__ = ()
    __displayable__ = Unset

    def __new__(cls, name, bases, namespace, **options):
        # If this is a factory-backed Command, synthesize a __call__ that mirrors the
        # callback's signature and forwards into self._callback.
        if options.get("factory", False):
            namespace["__call__"] = _invoker(options["callback"])

        # Build the class with:
        # - a human-friendly __typename__ derived from the class name,
        # - a dynamic __module__ marker for clarity in tooling,
        # - mirrored properties for every name listed in __introspectable__.
        self = super().__new__(
            cls,
            name,
            bases,
            namespace | {
                "__typename__": re.sub(r"(?<!^)(?=[A-Z])", r"-", name).lower(),
                "__module__": "dynamic-factory::commands",
            } | {
                name: mirror(name) for name in namespace.get("__introspectable__", ())
            },
            )

        # Provide a compact, stable string representation with high-signal fields.
        @rename("__repr__")
        def __repr__(self):
            """
            Return a concise, stable representation with key metadata.

            Example
            - command(name='build', ...)
            """
            return f"{type(self).__typename__}({
                ", ".join(map(functools.partial(operator.mod, "%s=%r"), self.__rich_repr__()))
            })"
        self.__repr__ = __repr__

        # Structured representation for pretty printers (e.g., rich).
        @rename("__rich_repr__")
        def __rich_repr__(self):
            """
            Yield a sequence of (name, object) pairs for pretty printers.

            The set of names comes from type(self).__displayable__ if provided,
            otherwise from type(self).__introspectable__.
            """
            for name in coalesce(type(self).__displayable__, type(self).__introspectable__):
                yield name, getattr(self, name)
        self.__rich_repr__ = __rich_repr__

        if options.get("factory", False):
            # Factory-backed Command classes are sealed to avoid subclassing surprises.
            @rename("__init_subclass__")
            def __init_subclass__(cls, **options):  # NOQA: F-841
                """
                Disallow subclassing of factory-backed Command classes.
                """
                raise TypeError(f"type {self.__name__!r} is not an acceptable base type")
            self.__init_subclass__ = classmethod(__init_subclass__)

        return self


def _process_source(cls, metadata):
    """
    Introspect the command callback and materialize argument specs.

    Responsibilities
    - Read the callable stored in metadata["callback"] and inspect its signature.
    - Resolve each parameter's default into a concrete spec (Cardinal, Option, or Flag).
    - Enforce placement/kind rules (e.g., Cardinal must be positional-only; Flag must be keyword-only).
    - Build three structures in metadata, mutating them in place:
      • cardinals: mapping[param_name -> Cardinal]
      • switches: mapping[option_or_flag_name -> Option|Flag] (aliases fan out to the same object)
      • groups: mapping[group_name -> list[spec]] ordered by appearance

    Errors
    - Raises TypeError/ValueError on non-callable or non-inspectable callbacks,
      invalid parameter kinds, duplicate switch names, or mixed visibility/deprecation
      ordering for cardinals.
    """
    cardinals = metadata["cardinals"] = {}
    switches = metadata["switches"] = {}
    groups = metadata["groups"] = defaultdict(list)

    try:
        signature = inspect.signature(metadata["callback"])
    except TypeError:
        raise TypeError(f"{cls.__typename__} 'callback' must be callable") from None
    except ValueError:
        raise ValueError(f"{cls.__typename__} 'callback' an inspectable callable") from None

    def _resolve_argument(x):
        """
        Return the concrete spec (Cardinal|Option|Flag) from a Supports* default.
        """
        nonlocal name

        if sum((
            hasattr(x, "__cardinal__") and callable(x.__cardinal__),
            hasattr(x, "__option__") and callable(x.__option__),
            hasattr(x, "__flag__") and callable(x.__flag__),
        )) != 1:
            raise TypeError(f"{cls.__typename__} 'callback' parameter {name!r} default must be argument-resoluble")

        if hasattr(x, "__cardinal__"):
            cardinal = x.__cardinal__()
            if not isinstance(cardinal, Cardinal):
                raise TypeError("__cardinal__() non-cardinal returned")
            return cardinal
        elif hasattr(x, "__option__"):
            option = x.__option__()
            if not isinstance(option, Option):
                raise TypeError("__option__() non-option returned")
            return option
        elif hasattr(x, "__flag__"):
            flag = x.__flag__()
            if not isinstance(flag, Flag):
                raise TypeError("__flag__() non-flag returned")
            return flag

        raise RuntimeError("unreachable")

    greedy = None
    hidden = False
    deprecated = False

    def _resolve_cardinal(x):
        """
        Validate and register a Cardinal under its parameter name; enforce ordering rules.
        """
        nonlocal name, parameter, greedy, hidden, deprecated

        if parameter.kind is not Parameter.POSITIONAL_ONLY:
            raise TypeError(f"{cls.__typename__} 'callback' cardinal at parameter {name!r}, parameter must be positional-only")

        if greedy:
            raise TypeError(f"{cls.__typename__} 'callback' greedy cardinal at parameter {greedy!r}, must be the last cardinal")
        greedy = name if x.nargs is Ellipsis else None

        hidden |= x.hidden
        if hidden and not x.hidden:
            raise TypeError(f"{cls.__typename__} 'callback' non-hidden cardinal at parameter {name!r}, cannot follow a hidden one")

        deprecated |= x.deprecated
        if deprecated and not x.deprecated:
            raise TypeError(f"{cls.__typename__} 'callback' non-deprecated cardinal at parameter {name!r}, cannot follow a deprecated one")

        cardinals[name] = x

    def _resolve_switch(x):
        """
        Validate kind, fan out aliases into 'switches', and ensure no duplicate names.
        """
        nonlocal name, parameter

        if isinstance(x, Option) and parameter.kind is not Parameter.POSITIONAL_OR_KEYWORD:
            raise TypeError(f"{cls.__typename__} 'callback' option at parameter {name!r}, parameter must be standard")
        if isinstance(x, Flag) and parameter.kind is not Parameter.KEYWORD_ONLY:
            raise TypeError(f"{cls.__typename__} 'callback' flag at parameter {name!r}, parameter must be keyword-only")

        for name in x.names:
            if name in switches:
                raise TypeError(f"{cls.__typename__} 'callback' name {name!r} is already in use")
            switches[name] = x

    for name, parameter in signature.parameters.items():
        if parameter.default is Parameter.empty:
            raise TypeError(f"{cls.__typename__} 'callback' parameter {name!r} must have a default")

        if isinstance(argument := _resolve_argument(parameter.default), Cardinal):
            _resolve_cardinal(argument)
        else:
            _resolve_switch(argument)
        groups[argument.group].append(argument)


def _process_strings(cls, metadata):
    """
    Normalize scalar string/Text metadata fields.

    Reads a fixed set of scalar keys from metadata and:
    - Validates type: each value must be str | Text | Unset.
    - Trims strings; empty strings are rejected.
    - Resolves Unset via coalesce(...) to None (keeps Text unchanged).

    Mutates
    - metadata[name] for each of:
      name, descr, usage, epilog, version, license, support,
      homepage, copyright, bugtracker.

    Errors
    - TypeError: when a value is not str | Text | Unset.
    - ValueError: when a string becomes empty after trimming.
    """
    for name in (
            "name",
            "descr",
            "usage",
            "epilog",
            "version",
            "license",
            "support",
            "homepage",
            "copyright",
            "bugtracker",
    ):
        if not isinstance(object := metadata[name], str | Text | Unset):
            raise TypeError(f"{cls.__typename__} {name!r} must be a string")
        elif isinstance(object, str) and not (object := object.strip()):
            raise ValueError(f"{cls.__typename__} {name!r} cannot be empty")
        metadata[name] = coalesce(object)


def _process_iterables(cls, metadata):
    """
    Normalize iterable-of-string/Text metadata fields.

    Reads a fixed set of collection keys from metadata and:
    - Validates type: the value must be Iterable.
    - Validates each item: str | Text, non-empty (strings are trimmed).
    - Rejects duplicates by stringified content (str(item)).
    - Stabilizes to a tuple while preserving original order.

    Mutates
    - metadata[name] for each of:
      notes, examples, warnings, developers, maintainers.

    Errors
    - TypeError: when the value is not Iterable or an element is not str | Text.
    - ValueError: when a string element becomes empty after trimming or duplicates are found.
    """
    for name in (
            "notes",
            "examples",
            "warnings",
            "developers",
            "maintainers",
    ):
        if not isinstance(object := metadata[name], Iterable):
            raise TypeError(f"{cls.__typename__} {name!r} must be iterable an iterable of strings")
        seen = set()
        for item in object:
            if not isinstance(item, str | Text):
                raise TypeError(f"{cls.__typename__} {name!r} must be an iterable of strings")
            elif isinstance(item, str) and not (item := item.strip()):
                raise ValueError(f"{cls.__typename__} must be an iterable of non-empty strings")
            elif str(item) in seen:
                raise ValueError(f"{cls.__typename__} {name!r} cannot contain duplicates")
            seen.add(str(item))
        metadata[name] = tuple(object)


def _process_conflicts(cls, metadata):
    """
    Compile and validate mutually-exclusive group constraints.

    Input
    - metadata["conflicts"]: Iterable[Iterable[str]]
      Each inner iterable represents a set of group names that are mutually
      exclusive (i.e., no two of them may be specified together).

    Validation rules
    - The outer value must be an Iterable (but not a plain string).
    - Each inner value must be an Iterable (but not a plain string).
    - Each group name must be a non-empty str after trimming.
    - Each referenced group must exist in metadata["groups"] (i.e., it has
      at least one argument assigned).
    - Each conflicting set must contain at least two distinct group names.
    - Duplicates inside a set are invalid (detected before converting to set).

    Result
    - Builds a symmetric mapping of conflicts:
        { group: {peer1, peer2, ...}, ... }
      where group conflicts with all peers in any set in which it appears.

    Raises
    - TypeError: when the outer/inner structures are not iterable or a group
      name is not a string (strings as containers are rejected).
    - ValueError: when a group name trims to empty, refers to an unknown
      group, a conflicting set has fewer than two elements, or contains duplicates.
    """
    conflicts = defaultdict(set)

    # Outer shape must be an iterable of collections (reject plain string)
    if not isinstance(metadata["conflicts"], Iterable) or isinstance(metadata["conflicts"], (str, Text)):
        raise TypeError(f"{cls.__typename__} 'conflicts' must be an iterable of iterables of strings")

    for conflict in metadata["conflicts"]:
        # Inner shape must also be an iterable of group names (reject plain string)
        if isinstance(conflict, (str, Text)):
            raise TypeError(f"{cls.__typename__} 'conflicts' must be an iterable of iterables of strings")
        try:
            items = list(conflict)
        except TypeError:
            raise TypeError(f"{cls.__typename__} 'conflicts' must be an iterable of iterables of strings") from None

        # Normalize and validate items; track duplicates before set()-ing
        normalized: list[str] = []
        for group in items:
            if not isinstance(group, str):
                raise TypeError(f"{cls.__typename__} 'conflicts' must be an iterable of iterables of strings")
            group = group.strip()
            if not group:
                raise ValueError(f"{cls.__typename__} 'conflicts' must be an iterable of iterables of non-empty strings")
            if group not in metadata["groups"]:
                raise ValueError(f"{cls.__typename__} group {group!r} is not a valid group")
            normalized.append(group)

        # Detect duplicates inside the set explicitly (len after normalization vs set)
        groups = set(normalized)
        if len(groups) != len(normalized):
            raise ValueError(f"{cls.__typename__} conflicting group sets cannot contain duplicates")
        if len(groups) < 2:
            raise ValueError(f"{cls.__typename__} conflicting groups sets must have at least two elements")

        # Symmetric mapping: for each group, union all peers
        for group in groups:
            conflicts[group] |= groups - {group}

    metadata["conflicts"] = conflicts


def _reverse_conflicts(conflicts):
    """
    Reconstruct an iterable of conflicting group-sets (each size >= 2)
    from a symmetric conflicts mapping: {group: {peers...}, ...}.

    Strategy
    - Try fast-path for disjoint cliques (closed-neighborhood grouping).
    - Otherwise, compute maximal cliques (Bron–Kerbosch) and return those.

    Returns
    - list[tuple[str, ...]]: normalized, sorted cliques; size >= 2.
    """
    # Validate symmetry (optional; comment out if you trust input)
    for group, peers in conflicts.items():
        for p in peers:
            if group not in conflicts.get(p, ()):
                raise ValueError(f"conflicts mapping is not symmetric: {group!r} ↔ {p!r}")

    # 1) Fast path: disjoint cliques produce identical closed neighborhoods
    buckets = {}
    for group, peers in conflicts.items():
        buckets.setdefault(frozenset({group, *peers}), set()).add(group)

    # If every bucket equals its key (and size >= 2), we can use them directly
    disjoint = True
    cliques = []
    for key, members in buckets.items():
        if len(key) < 2 or members != key:
            disjoint = False
            break
        cliques.append(tuple(sorted(key)))

    if disjoint:
        # Deduplicate and sort by (-size, lexicographic)
        return sorted({tuple(sorted(c)) for c in cliques}, key=lambda c: (-len(c), c))

    # 2) Robust path: maximal cliques (Bron–Kerbosch)
    # Build adjacency using only nodes present in mapping
    nodes = set(conflicts.keys())
    adj = {g: set(conflicts[g]) & nodes for g in nodes}

    res = set()

    def bronk(r, p, x):
        if not p and not x:
            if len(r) >= 2:
                res.add(frozenset(r))
            return
        # Simple pivot optimization
        u = max(p | x, key=lambda v: len(adj[v])) if (p or x) else None
        u_neighbors = adj[u] if u is not None else set()
        for v in list(p - u_neighbors):
            bronk(r | {v}, p & adj[v], x & adj[v])
            p.remove(v)
            x.add(v)

    bronk(set(), set(nodes), set())

    # Normalize to tuples and sort
    return sorted((tuple(sorted(c)) for c in res), key=lambda c: (-len(c), c))


def _attach_to_parent(self, parent):
    """
    Register this command under its parent, enforcing unique names.

    Behavior
    - Uses the parent's internal _children registry to attach self under
      the normalized command name.
    - If the name is already taken by a different object, raises ValueError
      with a precise message indicating whether the conflict is at the
      command or subcommand level.

    Parameters
    - parent: Command | Unset
      The parent command under which this command should be registered.
    """
    # Normalize to a plain string key (guard against Text-like inputs)
    # and use setdefault to atomically claim the slot if free.
    if getattr(parent, "_children", {}).setdefault(name := str(self.name), self) is self:
        return

    # Name is already registered to another command; construct a clear message.
    typeof = "subcommand" if parent.parent else "command"
    raise ValueError(f"{type(self).__typename__} {typeof} name {name!r} is already in use")


@functools.cache  # Memoize to avoid recomputing common ordinals in prompts/errors
def _ordinal(number):
    """
    Return a human-friendly ordinal label for a 1-based position.

    - 1..10 are rendered as words ("first"…"tenth") for nicer phrasing in prompts.
    - Other numbers use numeric ordinals with correct English suffixes.
    """
    # Prefer word forms for the first ten positions (reads better in UX copy)
    try:
        return {
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
        }[number]
    except KeyError:
        pass

    # Handle the “teens” exception: 11th, 12th, 13th (and 111th, 112th, 113th, …)
    if 10 < number % 100 < 20:
        return f"{number}th"

    # Standard suffix mapping based on the last digit (1→st, 2→nd, 3→rd, else → th)
    return f'{number}%s' % {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")


# Global registries used for template/scaffold cloning and late mounting via include()
# - _templates: map[template -> list[scaffold]]; when a top-level template is cloned without a parent,
#   we record its newly created scaffold(s) here to be mounted later under a real parent in include().
# - _scaffolds: map[scaffold -> template]; reverse lookup to resolve a scaffold back to its originating
#   template, so related clones can be grouped and attached together.
_templates = defaultdict(list)
_scaffolds = {}


class Command(metaclass=CommandType):
    """
    High-level command object that wraps a Python callable and provides CLI behavior.

    Responsibilities
    - Introspection: exposes metadata (name, descr, usage, version info, etc.) as read-only properties.
    - Composition: supports parent/child hierarchies to model subcommands.
    - Rendering: pretty help/usage/version output via Rich (see _helper/_versioner).
    - Invocation: acts as a callable (via a generated __call__) and can be executed via __invoke__.
    - Discovery: can include() external modules and mount their top-level commands as children.

    Lifecycle
    - Constructed either from a callback (most common) or by cloning an existing Command template.
    - Signature is inspected and defaults are resolved to argument specs (Cardinal/Option/Flag).
    - Metadata is sanitized/normalized; constraints (conflicts) are compiled.
    - Automatically adds helper/version flags unless provided (and attaches to parent if given).

    Extension points
    - _helper/_versioner for rich-based rendering.
    - include() for late discovery/mounting of subcommands from modules.
    - command() for convenient child creation with parent=self injection.

    Notes
    - The concrete __call__ is generated to mirror the callback’s signature so that help/tracebacks are clear.
    - Many fields are lists/sets/maps but are exposed as read-only views to discourage accidental mutation.
    """

    # Properties mirrored to read-only attributes for introspection and pretty printing.
    # These are the full set of fields that are safe to “reflect” publicly.
    __introspectable__ = (
        "name",
        "descr",
        "usage",
        "epilog",
        "notes",
        "examples",
        "warnings",
        "version",
        "license",
        "support",
        "homepage",
        "copyright",
        "bugtracker",
        "developers",
        "maintainers",
        "conflicts",
        "cardinals",
        "switches",
        "groups",
        "parent",
        "children",
        "shell",
        "fancy",
        "colorful",
        "deferred",
    )

    # Subset of fields prioritized for compact displays (e.g., __rich_repr__ when not expanded).
    # Keep this concise and high-signal; exhaustive data remains available via __introspectable__.
    __displayable__ = (
        # Compact, high-signal fields for typical display
        "name",
        "descr",
        "usage",
        "version",
        "support",
        "homepage",
        "parent",
        "children",
        "shell",
        "fancy",
        "colorful",
        "deferred",
    )

    @property
    def root(self):
        """
        Return the topmost command in the current command hierarchy.

        Walks up via .parent until there is no parent and returns that node.
        Useful to compute absolute routes, aggregate metadata, or render
        help/version from the root context regardless of the current node.
        """
        child, parent = self, self.parent
        while parent:
            child, parent = parent, parent.parent
        return child

    @property
    def path(self):
        """
        Return the full ancestry from root to this command as a tuple.

        The first element is the root command, the last is the current node.
        This is convenient for building user-facing routes (e.g., 'root sub a b')
        and for traversing upwards without repeated parent-chasing.
        """
        path = [command := self]
        while command.parent:
            path.append(command := command.parent)
        return tuple(reversed(path))

    def __new__(
            cls,
            source,
            /,
            parent=Unset,
            # ── Identity (introspection + help + version) ───────────────────────────
            name=Unset,
            # ── Help (scalars) ──────────────────────────────────────────────────────
            descr=Unset,
            usage=Unset,
            epilog=Unset,
            # ── Help (collections) ─────────────────────────────────────────────────
            notes=(),
            examples=(),
            warnings=(),
            # ── Version (scalars) ──────────────────────────────────────────────────
            version=Unset,
            license=Unset,
            support=Unset,
            homepage=Unset,
            copyright=Unset,
            bugtracker=Unset,
            # ── Version (collections) ──────────────────────────────────────────────
            developers=(),
            maintainers=(),
            # ── Semantics (parser constraints; not part of help autogen) ───────────
            conflicts=(),
            *,
            shell=Unset,
            fancy=Unset,
            colorful=Unset,
            deferred=Unset
    ):
        """
        Construct a Command either from a callback or by cloning a template.

        Invocation modes
        - Callback mode (common)
          source is a callable. The function signature is inspected, arguments are
          resolved into specs, and the resulting Command is bound to that callback.

        - Template mode (cloning)
          source is a Command. A new scaffold is created that reuses source._callback
          but allows metadata overrides (name/descr/etc.). If no parent is provided
          and the template is top-level, the scaffold/template pair is recorded in
          internal registries so later include() can mount them.

        Parameters
        - parent: Command | Unset
          Parent under which to attach this command. If Unset, remains top-level.
        - name, descr, usage, epilog: str | Text | Unset
          Identity and help scalars. Unset defers to derived defaults (e.g., __name__ or docstring).
        - notes, examples, warnings: Iterable[str | Text]
          Help collections for “Notes/Examples/Warnings” sections (validated and normalized).
        - version, license, support, homepage, copyright, bugtracker:
          Version block scalars shown by the version renderer.
        - developers, maintainers: Iterable[str | Text]
          Lists rendered in the version block.
        - conflicts: Iterable[Iterable[str]]
          Group-level mutual exclusion sets (compiled into a symmetric mapping).
        - shell, fancy, colorful, deferred: bool | Unset
          Runtime flags. If Unset, values inherit from parent (or default False).

        Behavior
        - Validates parent type and cardinal placement constraints on parent.
        - In callback mode:
            • Builds a metadata dict; processes callback signature into specs and groups.
            • Normalizes scalars/collections/conflicts.
            • Generates a factory-backed Command type with a trampoline __call__.
            • Mirrors sanitized metadata into read-only properties.
            • Attaches to parent (enforcing unique child names).
            • Ensures built-in helper/version flags exist (unless already provided).
        - In template mode:
            • Creates a shallow scaffold reusing the original callback.
            • Applies overrides or inherits from the template where Unset/empty.
            • Registers scaffolds/templates for later include() mounting when top-level.

        Raises
        - TypeError/ValueError on invalid parent, metadata types, duplicate switch names,
          invalid callback/defaults, or name conflicts upon attachment.

        Returns
        - Command: the constructed or cloned command instance.
        """
        # Validate parent shape: must be a Command (or Unset), and parents with cardinals
        # cannot host children (keeps routing unambiguous for positional arguments).
        if not isinstance(parent, Command | Unset):
            raise TypeError(f"{cls.__typename__} 'parent' must be a command")
        elif getattr(parent, "cardinals", {}):
            raise ValueError(f"{cls.__typename__} 'parent' command cannot have any cardinals")

        # Template mode: clone from an existing Command, applying overrides and inheriting where Unset.
        if isinstance(source, Command):

            scaffold = type(source).__base__(
                source._callback,  # NOQA: E-501
                parent := coalesce(parent, source.parent or Unset),
                coalesce(name, source.name),
                coalesce(descr, source.descr or Unset),
                coalesce(usage, source.usage or Unset),
                coalesce(epilog, source.epilog or Unset),
                notes or source.notes,
                examples or source.examples,
                warnings or source.warnings,
                coalesce(version, source.version or Unset),
                coalesce(license, source.license or Unset),
                coalesce(support, source.support or Unset),
                coalesce(homepage, source.homepage or Unset),
                coalesce(copyright, source.copyright or Unset),
                coalesce(bugtracker, source.bugtracker or Unset),
                developers or source.developers,
                maintainers or source.maintainers,
                conflicts or _reverse_conflicts(source.conflicts),
                shell=coalesce(shell, source.shell),
                fancy=coalesce(fancy, source.fancy),
                colorful=coalesce(colorful, source.colorful),
                deferred=coalesce(deferred, source.deferred)
            )

            # Record template/scaffold pairs for later include() mounting when top-level.
            if not parent:
                _templates[template := _scaffolds.get(source, source)].append(scaffold)
                _scaffolds[scaffold] = template

            return scaffold

        # Callback mode: assemble metadata and process the callback signature.
        metadata = {
            "callback": source,
            # Identity/help/version scalars and collections
            "name": coalesce(name, getattr(source, "__name__", os.path.basename(sys.argv[0]))),
            "descr": coalesce(descr, inspect.getdoc(source) or Unset),
            "usage": usage,
            "epilog": epilog,
            "notes": notes,
            "examples": examples,
            "warnings": warnings,
            "version": version,
            "license": license,
            "support": support,
            "homepage": homepage,
            "copyright": copyright,
            "bugtracker": bugtracker,
            "developers": developers,
            "maintainers": maintainers,
            # Parser semantics
            "conflicts": conflicts,
            # Runtime flags (inherit from parent when Unset)
            "shell": bool(coalesce(shell, getattr(parent, "shell", False))),
            "fancy": bool(coalesce(fancy, getattr(parent, "fancy", False))),
            "colorful": bool(coalesce(colorful, getattr(parent, "colorful", False))),
            "deferred": bool(coalesce(deferred, getattr(parent, "deferred", False))),
            # Parent/children wiring
            "parent": parent,
            "children": {}
        }
        # Build specs and groups from the callback; validate/massage metadata fields.
        _process_source(cls, metadata)
        _process_strings(cls, metadata)
        _process_iterables(cls, metadata)
        _process_conflicts(cls, metadata)

        # Create a factory-backed command type and instance; bind trampoline __call__.
        self = super().__new__(type(cls)(cls.__name__, (cls,), dict(cls.__dict__), factory=True, callback=source))
        # Cache signature/translation map for usage/help layout.
        self._parameters = list(inspect.signature(metadata["callback"]).parameters.values())
        self._transmap = {parameter.default: parameter for parameter in self._parameters}
        # Lift the callback out of metadata and mirror the rest as private fields.
        self._callback = metadata.pop("callback")
        self._fallback = Unset
        self._namespace = dict()
        self._faults = list()
        self._calls = set()
        self._waits = {}
        self._index = 0
        self._stderr = False
        for name, object in metadata.items():
            setattr(self, "_" + name, coalesce(object))
        # Attach to parent (enforces unique child names).
        _attach_to_parent(self, self.parent)

        # Ensure built-in helper/version flags exist unless user provided them.
        if all(name not in self.switches for name in ("-h", "--help")):
            self._switches.update(dict.fromkeys({"-h", "--help"},
                flag(
                    "-h", "--help", descr="show this help message and exit", helper=True
                )(self._helper),
            ))
            self._groups["flags"].append(self.switches["--help"])

        if all(name not in self.switches for name in ("-v", "--version")):
            self._switches.update(dict.fromkeys({"-v", "--version"},
                flag(
                    "-v", "--version", descr="show this version message and exit", helper=True
                )(self._versioner),
            ))
            self._groups["flags"].append(self.switches["--version"])
        return self

    def _helper(self):
        """
        Render CLI help to the console.

        Palette keys
        - usage-label, program-name, usage-section, description-section, epilog-section
        - group-label, argument-description
        - option-name, flag-name, deprecated-name
        - metavar, greedy-metavar, deprecated-metavar, choice, deprecated-choice
        - children-title, children-table, children, children-description, name-column
        - notes-label, notes-dot, note
        - examples-label, examples-dot, example
        - warnings-label, warnings-dot, warning
        - panel-title, panel-subtitle

        Customization
        - Define a mapping named __styles__ in __main__ to override any palette entry.
        - When colorful is False, styling is suppressed; deprecated* still apply strike.
        """
        console = Console(stderr=len(self._faults) > 0 or self._stderr)
        styles = defaultdict(str, {
            # === Head sections ===
            "usage-label": "bold #00E6FF",  # CYAN → signature info color
            "program-name": "bold #FF4D94",  # MAGENTA-PINK → brand pop
            "usage-section": "bold #36C5F0",  # SKY-BLUE → softer than cyan
            "description-section": "italic #A3A3A3",  # Neutral gray
            "epilog-section": "#737373",  # Dim footer gray

            # === Groups / arguments ===
            "group-label": "bold #FFFFFF",  # Pure white headers
            "argument-description": "#9CA3AF",  # Muted gray

            # === Names / metavars ===
            "option-name": "bold #00E6FF",  # CYAN for options
            "flag-name": "bold #22C55E",  # GREEN for flags (success/positive)
            "deprecated-name": "bold #F97316 strike",  # ORANGE strike for deprecated

            "metavar": "bold #FFD600",  # AMBER for parameters
            "greedy-metavar": "bold italic #FFD600",
            "deprecated-metavar": "bold #F97316 strike",

            "choice": "bold #FF4D94",  # MAGENTA → choices stand out
            "deprecated-choice": "bold #F97316 strike",

            # === Children table ===
            "children-title": "bold #FFFFFF",
            "children-table": "#4B5563",  # Slate border
            "children": "bold #36C5F0",  # Sky-blue subcommands
            "children-description": "#9CA3AF",
            "name-column": "",

            # === Notes / Examples / Warnings ===
            "notes-label": "bold #00E6FF",  # Cyan notes
            "notes-dot": "#00E6FF dim",
            "note": "#D1D5DB",

            "examples-label": "bold #22C55E",  # Green examples
            "examples-dot": "#22C55E dim",
            "example": "#E5E7EB",

            "warnings-label": "bold #EF4444",  # RED headline
            "warnings-dot": "#EF4444 dim",
            "warning": "bold #FFD600",  # Amber body

            # === Fancy panel ===
            "panel-title": "bold #FF4D94",  # Magenta branding
            "panel-subtitle": "#9CA3AF",
        } | getattr(__import__('__main__'), "__styles__", {}))

        def styler(style):
            # Keep strike for deprecated even in non-color mode; otherwise honor palette only when colorful=True
            if "deprecated" in style and not self.colorful:
                return "strike"
            return styles[style] if self.colorful else ""

        def text(fragment, style=""):
            # Normalize to Rich Text. In non-colorful mode, strip styles; preserve existing Text spans.
            if not fragment:
                return fragment
            if not self.colorful:
                return Text(str(fragment))
            if isinstance(fragment, Text):
                return fragment
            return Text(str(fragment), style)

        renders = []  # Accumulate sections then print as a Group (and optionally in a Panel)

        width = console.width - 4 * self.fancy  # Account for panel gutters when fancy=True

        # Render option/flag names list with styled separators; provides iterator and fused Text forms.
        def names(x, *, iter=False):
            shorts = sorted((name for name in x.names if not name.startswith("--")), key=len)
            longs = sorted((name for name in x.names if name.startswith("--")), key=len)

            style = "deprecated-name" if x.deprecated else "option-name" if isinstance(x, Option) else "flag-name"

            if iter:
                # Yield styled name fragments (for table-building flows)
                return map(lambda x: text(x, styler(style)), itertools.chain(shorts, longs))

            # Fused single Text segment with " | " separators
            short = Text(" | ").join(map(lambda x: text(x, styler(style)), shorts))
            long = Text(" | ").join(map(lambda x: text(x, styler(style)), longs))
            return Text(" | ").join(part for part in (short, long) if part)

        # Build a Text for metavars. Handles choices vs. metavar label, and shapes arity decorations.
        def metavar(x, altname, *, simple=False, iter=False):
            if x.choices:
                style = "deprecated-choice" if x.deprecated else "choice"
                metavar = Text.assemble(
                    "{",
                    Text(",").join(map(lambda x: text(x, styler(style)), map(repr, x.choices))),
                    "}"
                )
            else:
                style = "deprecated-metavar" if x.deprecated else ("greedy-metavar" if x.nargs is Ellipsis else "metavar")
                # Use explicit metavar when provided; otherwise synthesize from parameter name
                metavar = text(x.metavar, styler(style)) if x.metavar is not None else Text.assemble("<", text(altname, styler(style)), ">")

            if simple:
                return metavar

            # Arity decorations ([], ..., repetition). iter=True yields an iterable for table joins.
            match x.nargs:
                case "?":
                    metavar = Text.assemble("[", metavar, "]")
                    if iter:
                        return builtins.iter([metavar])
                    return metavar
                case "*":
                    metavar = Text.assemble("[", metavar, " ", "...", "]")
                    if builtins:
                        return builtins.iter([metavar])
                    return metavar
                case "+":
                    metavar = Text.assemble(metavar, " ", "[", metavar, " ", "...", "]")
                    if builtins:
                        return builtins.iter([metavar])
                    return metavar
                case int():
                    if iter:
                        return builtins.iter((metavar for _ in range(x.nargs)))
                    return Text(" ").join(metavar for _ in range(x.nargs))
                case _:
                    if iter:
                        return builtins.iter([metavar])
                    return metavar

        # Usage line: explicit (string) or synthesized from argument specs.
        if self.usage:
            # Explicit usage string wins; apply a headline style
            usage = Text()
            usage.append("usage", styler("usage-label")).append(":")
            usage.append(" ")
            usage.append(text(self.usage, styler("usage-section")))
        else:
            # Synthesize: program name + [flags/options] + positional cardinals
            usage = Text()
            usage.append("usage", styler("usage-label")).append(":")
            usage.append(" ")
            usage.append(text(self.name, styler("program-name")))
            usage.append(" ")

            offset = len(usage)  # Hanging-indent column for wrapped usage items
            inputs = deque()
            seen = set()

            # Optional flags first (dedupe across aliases)
            for parameter in filter(lambda x: isinstance(x.default, Flag) and not x.default.hidden, self._parameters):
                if (flag := parameter.default) in seen:
                    continue
                seen.add(flag)
                inputs.append(Text.assemble("[", names(flag), "]"))

            # Ensure built-ins appear first if present
            if self.switches["--help"] not in seen:
                inputs.insert(0, Text.assemble("[", names(self.switches["--help"]), "]"))
                seen.add(self.switches["--help"])

            if self.switches["--version"] not in seen:
                inputs.insert(0, Text.assemble("[", names(self.switches["--version"]), "]"))
                seen.add(self.switches["--version"])

            # Options with metavars
            for parameter in filter(lambda x: isinstance(x.default, Option) and not x.default.hidden, self._parameters):
                if (option := parameter.default) in seen:
                    continue
                seen.add(option)
                inputs.append(Text.assemble(
                    "[", names(option), " ", metavar(option, re.sub(r"_+", "-", parameter.name.lower().strip("_"))), "]"
                ))

            # Positional cardinals (in order)
            for parameter in filter(lambda x: isinstance(x.default, Cardinal) and not x.default.hidden, self._parameters):
                if (cardinal := parameter.default) in seen:
                    continue
                seen.add(cardinal)
                inputs.append(Text.assemble(
                    metavar(cardinal, re.sub(r"_+", "-", parameter.name.lower().strip("_")))
                ))

            # Wrap synthesized usage items across terminal width
            try:
                lines = Lines([inputs.popleft()])
            except IndexError:
                lines = Lines()

            while inputs:
                if len(lines[-1]) + 1 + len(input := inputs.popleft()) > width - offset:
                    lines.append(input)
                else:
                    lines[-1].append(Text(" ") + input)

            try:
                usage.append(lines.pop(0))
            except IndexError:
                pass
            for line in lines:
                usage.append("\n").append(" " * offset).append(line)

        renders.append(usage.append("\n"))

        # Description paragraph
        if self.descr:
            renders.append(text(self.descr, styler("description-section")).append("\n"))

        # Children (subcommands/commands) table
        if self.children:
            typeof = "subcommands" if self.parent else "commands"
            table = Table(
                "name", "help",
                title=text(typeof, styler("children-title")),
                width=int(width * (2 / 3)),
                box=ROUNDED,
                style=styler("children-table"),
                header_style=styler("children-title"),
            )

            for name, child in self.children.items():
                # Prefer explicit descr; then callback docstring; otherwise route hint
                descr = child.descr or inspect.getdoc(getattr(child, "_callback", None))
                if descr:
                    help = text(descr, styler("children-description"))
                else:
                    route = " ".join(step.name for step in child.path)  # full route from root
                    help = Text.assemble(
                        text("no description", styler("children-description")),
                        " — ",
                        text(f"run '{route} --help' for details", styler("examples-label")),
                    )

                table.add_row(
                    text(name, styler("children")),
                    help,
                    style=styler("name-column"),
                )

            renders.append(table)

        # Argument groups (options/flags/cardinals) pretty layout with hanging indents
        groups = Text("\n" if self.children else "")
        for index, (group, arguments) in enumerate(self.groups.items()):
            groups.append(text(group, styler("group-label"))).append(":")
            groups.append("\n")

            padding = 2   # Leading spaces before the first column
            indent = 15   # Column for description wrap/hanging indent

            for argument in filter(lambda x: not x.hidden, arguments):
                segments = deque()
                if isinstance(argument, Cardinal):
                    # Cardinal shows only its metavar segment in the names column
                    segments.append(metavar(argument, self._transmap[argument].name, simple=True))
                else:
                    # Options/flags list all names; options append metavar forms
                    for name in names(argument, iter=True):
                        segments.append(name)
                    if isinstance(argument, Option):
                        for meta in metavar(argument, self._transmap[argument].name, iter=True):
                            segments.append(meta)

                # Wrap names/metavars across terminal width
                lines = Lines([segments.popleft()])
                while segments:
                    if len(lines[-1]) + 1 + len(segment := segments.popleft()) > width - padding * (4 * (len(lines) > 1)):
                        lines.append(segment)
                    else:
                        lines[-1].append(Text(" ") + segment)

                # Stitch into a section with padding and (if needed) hanging-indent description
                section = Text()
                try:
                    section.append(" " * padding).append(lines.pop(0))
                except IndexError:
                    pass
                for line in lines:
                    section.append("\n").append(" " * padding * 4).append(line)

                # Description flow: if name column wraps or is wide, break line before description
                if descr := text(argument.descr, styler("argument-description")):
                    if lines or len(section) >= indent:
                        section.append("\n").append(" " * indent)
                    else:
                        section.append(" " * (indent - len(section)))
                    wrapped = descr.wrap(console, width - indent)
                    try:
                        section.append(wrapped.pop(0))
                    except IndexError:
                        pass
                    for line in wrapped:
                        section.append("\n").append(" " * indent).append(line)

                groups.append(section).append("\n")
            groups.append("\n" * (index < len(self.groups) - 1))

        if groups:
            renders.append(groups)

        # Notes / Examples / Warnings (bulleted lists with wrapping)
        if self.notes:
            padding = len(dot := text(" • ", styler("notes-dot")))
            notes = Text()
            notes.append(text("notes", styler("notes-label")).append(":"))
            notes.append("\n")
            for note in map(lambda x: text(x, styler("note")), self.notes):
                for index, segment in enumerate(note.wrap(console, width - padding)):
                    notes.append(dot if index == 0 else " " * padding).append(segment).append("\n")
            renders.append(notes)

        if self.examples:
            padding = len(dot := text(" • ", styler("examples-dot")))
            examples = Text()
            examples.append(text("examples", styler("examples-label")).append(":"))
            examples.append("\n")
            for example in map(lambda x: text(x, styler("example")), self.examples):
                for index, segment in enumerate(example.wrap(console, width - padding)):
                    examples.append(dot if index == 0 else " " * padding).append(segment).append("\n")
            renders.append(examples)

        if self.warnings:
            padding = len(dot := text(" • ", styler("warnings-dot")))
            warnings = Text()
            warnings.append(text("warnings", styler("warnings-label")).append(":"))
            warnings.append("\n")
            for warning in map(lambda x: text(x, styler("warning")), self.warnings):
                for index, segment in enumerate(warning.wrap(console, width - padding)):
                    warnings.append(dot if index == 0 else " " * padding).append(segment).append("\n")
            renders.append(warnings)

        # Epilog footer (single paragraph)
        if self.epilog:
            renders.append(text(self.epilog, styler("epilog-section")).append("\n"))

        renders[-1].rstrip()  # Trim trailing newline on the last chunk

        # Print all sections at once; optionally inside a decorative panel
        renderable = Group(*renders)

        if self.fancy:
            renderable = Panel(
                renderable,
                title=Text.assemble("[", " ", f"{self.name} HELP".upper(), " ", "]", style=styler("panel-title")),
                title_align="left",
                subtitle=text(self.copyright, styler("panel-subtitle")),
            )

        console.print(renderable)

    def _versioner(self):
        """
        Render version information to the console.

        Uses command metadata to build a compact “version” view:
        - Header: name and version.
        - Scalars: license, homepage, support, bugtracker, copyright.
        - Collections: developers, maintainers.

        Styling
        - Palette keys:
          program-name, program-version,
          license-label, license-section,
          homepage-label, homepage-section,
          support-label, support-section,
          bugtracker-label, bugtracker-section,
          copyright-label, copyright-section,
          developers-label, developers-dot, developer,
          maintainers-label, maintainers-dot, maintainer,
          panel-title, panel-subtitle.
        - User overrides are read from __main__.__styles__.
        - When colorful is False, styles are suppressed.

        Layout
        - Scalars render as label: value on individual lines.
        - Collections render as bulleted lists with wrapping.
        - If fancy is True, output is wrapped in a panel.
        """
        console = Console()
        # Palette (with user overrides). Keep values expressive; non-colorful mode strips styles in styler().
        styles = defaultdict(str, {
            # ==== Header ====
            "program-name": "bold #FF4D94",  # Magenta-pink brand pop
            "program-version": "bold #00E6FF",  # Cyan version (clear contrast)

            # ==== Scalar labels / values ====
            "license-label": "bold #FFFFFF",
            "license-section": "#9CA3AF",  # Neutral gray

            "homepage-label": "bold #FFFFFF",
            "homepage-section": "underline #00E6FF",  # Cyan link

            "support-label": "bold #FFFFFF",
            "support-section": "#22C55E",  # Green support channel

            "bugtracker-label": "bold #FFFFFF",
            "bugtracker-section": "underline #FF4D94",  # Magenta link (diff from homepage)

            "copyright-label": "bold #FFFFFF",
            "copyright-section": "#9CA3AF",

            # ==== Collections (people) ====
            "developers-label": "bold #FFD600",  # Amber header
            "developers-dot": "#FFD600 dim",
            "developer": "#E5E7EB",  # Light text body

            "maintainers-label": "bold #36C5F0",  # Sky-blue header (distinct from cyan)
            "maintainers-dot": "#36C5F0 dim",
            "maintainer": "#E5E7EB",

            # ==== Panel ====
            "panel-title": "bold #FF4D94",  # Magenta title
            "panel-subtitle": "#9CA3AF",
        } | getattr(__import__('__main__'), "__styles__", {}))

        def styler(style):
            # Keep strike for deprecated even in non-color mode; otherwise honor palette only when colorful=True
            if "deprecated" in style and not self.colorful:
                return "strike"
            return styles[style] if self.colorful else ""

        def text(fragment, style=""):
            # Normalize any input to Text and apply style conditionally (preserves existing Text)
            if not fragment:
                return fragment
            if not self.colorful:
                return Text(str(fragment))
            if isinstance(fragment, Text):
                return fragment
            return Text(str(fragment), style)

        renders = []  # Collect segments to print in one shot (optionally inside a Panel)
        width = console.width - 4 * self.fancy  # Reserve space for panel padding when fancy=True

        # Header: "<name> — <version>"
        renders.append(Text(" — ").join((
            text(self.name, styler("program-name")),
            text(self.version or "1.0.0", styler("program-version")))
        ))

        # Scalars (render if present)
        if self.license:
            license = Text()
            license.append(text("license", styler("license-label"))).append(":")
            license.append(" ")
            license.append(text(self.license, styler("license-section")))
            renders.append(license)

        if self.homepage:
            homepage = Text()
            homepage.append(text("homepage", styler("homepage-label"))).append(":")
            homepage.append(" ")
            homepage.append(text(self.homepage, styler("homepage-section")))
            renders.append(homepage)

        if self.support:
            support = Text()
            support.append(text("support", styler("support-label"))).append(":")
            support.append(" ")
            support.append(text(self.support, styler("support-section")))
            renders.append(support)

        if self.bugtracker:
            bugtracker = Text()
            bugtracker.append(text("bugtracker", styler("bugtracker-label"))).append(":")
            bugtracker.append(" ")
            bugtracker.append(text(self.bugtracker, styler("bugtracker-section")))
            renders.append(bugtracker)

        if self.copyright:
            copyright = Text()
            copyright.append(text("copyright", styler("copyright-label"))).append(":")
            copyright.append(" ")
            copyright.append(text(self.copyright, styler("copyright-section")))
            renders.append(copyright)

        # Visual spacing between scalar block and collections (only when collections exist)
        if self.developers or self.maintainers:
            renders[-1].append("\n")

        # Collections: developers (bulleted list with wrapping)
        if self.developers:
            padding = len(dot := text(" • ", styler("developers-dot")))
            developers = Text()
            developers.append(text("developers", styler("developers-label")).append(":"))
            developers.append("\n")
            # Wrap each entry to available width; prefix the first line with a bullet, indent continuation
            for item in map(lambda x: text(x, styler("developer")), self.developers):
                for index, segment in enumerate(item.wrap(console, width - padding)):
                    developers.append(dot if index == 0 else " " * padding).append(segment).append("\n")
            renders.append(developers)

        # Collections: maintainers (bulleted list with wrapping)
        if self.maintainers:
            padding = len(dot := text(" • ", styler("maintainers-dot")))
            maintainers = Text()
            maintainers.append(text("maintainers", styler("maintainers-label")).append(":"))
            maintainers.append("\n")
            for item in map(lambda x: text(x, styler("maintainer")), self.maintainers):
                for index, segment in enumerate(item.wrap(console, width - padding)):
                    maintainers.append(dot if index == 0 else " " * padding).append(segment).append("\n")
            renders.append(maintainers)

        renders[-1].rstrip()  # Trim trailing newline from the last section
        renderable = Group(*renders)

        # Optional panel chrome when fancy=True
        if self.fancy:
            renderable = Panel(
                renderable,
                title=Text.assemble("[", " ", f"{self.name} VERSION".upper(), " ", "]", style=styler("panel-title")),
                title_align="left",
                subtitle=text(self.copyright, styler("panel-subtitle")),
            )

        console.print(renderable)

    def fallback(self, fallback, /):
        """
        Register a one-time fallback handler for faults.

        Contract
        - fallback: callable invoked when faults occur during execution.
          • When deferred is True, it will be called with a sequence of faults.
          • When deferred is False, it will be called with a single fault.

        Rules
        - Must be callable.
        - Can be set only once per command (cannot be overridden).

        Returns
        - The same callable, enabling decorator-style usage: @cmd.fallback
        """
        if not callable(fallback):
            raise TypeError(f"{type(self).__typename__} fallback must be callable")
        if self._fallback is not Unset:  # type: ignore[attr-defined]
            raise TypeError(f"{type(self).__typename__} fallback cannot be overridden")
        self._fallback = fallback  # NOQA: New attributes are not typechecked
        # Note: In deferred mode, the callable must accept Iterable[fault];
        # otherwise it must accept a single fault instance.
        return fallback

    def command(self, source=Unset, /, *args, **kwargs):
        """
        Create or attach a subcommand under this command.

        This is a thin convenience wrapper around the top-level command(...) factory
        that automatically injects the current command as the parent. It supports the
        same invocation modes as command(...):
        - Callback mode: command(self, callback, ...) -> Command
        - Template mode: command(self, template, ...) -> Command
        - Decorator mode: @self.command(...)

        Parameters
        - source: Callable | Command | Unset
          The callback to wrap, an existing Command template to clone, or Unset
          when used in decorator mode.
        - *args, **kwargs:
          Forwarded to command(...). Common metadata includes name, descr, usage,
          epilog, notes, examples, warnings, version fields, conflicts, and runtime
          flags (shell/fancy/colorful/deferred).

        Returns
        - Command instance in direct mode, or a decorator in decorator mode.

        Notes
        - This method centralizes child creation/attachment so callers don't need
          to pass parent=self explicitly.
        """
        # Delegate to the global factory, injecting parent=self to mount the child here.
        return command(source, self, *args, **kwargs)  # type: ignore[arg-type]

    def include(self, source, /, *, propagate=False):
        """
        Discover and attach top-level Command templates from external modules.

        Purpose
        - Dynamically extend a command with child subcommands defined elsewhere.
          This is convenient for “plugin-like” layouts where commands live in
          separate modules/packages and need to be mounted under a parent.

        Parameters
        - source: str
          Module glob pattern to import. The pattern is expanded via mglob(...)
          to a sequence of importable module names (e.g., "pkg.tools.*").
        - propagate: bool (keyword-only)
          When True, propagate the parent’s runtime flags (shell/fancy/colorful/
          deferred) to each attached child; when False, children retain their
          own defaults.

        Behavior
        - Validates that source is a string and resolves it to modules with mglob.
        - Imports every matched module (ImportError is reported as TypeError).
        - Scans module globals; picks only Command instances that are not already
          attached to some parent (top-level entries).
        - For each discovered command “template”, resolves scaffolds/templates
          from internal registries and attaches all resulting children under
          the current command via self.command(...).
        - Name collisions, invalid shapes, or group constraints are handled by
          self.command(...) and may raise accordingly.

        Raises
        - TypeError: when source is not str, a module cannot be imported, or an
          invalid object shape is encountered during attachment.
        - ValueError: may bubble up from self.command(...) (e.g., duplicate names).

        Side effects
        - Mutates the current command’s children and internal groups/switches
          by mounting discovered subcommands.
        """
        if not isinstance(source, str):
            raise TypeError(f"include() argument must be a string")
        modules = mglob(source)  # Expand module glob pattern (e.g., "pkg.plugins.*") to concrete module names

        def imp(module):
            # Import each resolved module; convert ImportError into a TypeError to match include() contract
            try:
                return importlib.import_module(module)
            except ImportError:
                raise TypeError(f"unable to import module {module!r}")

        # Iterate discovered modules and scan their globals for top-level Command templates
        for module in map(imp, modules):
            for name, object in inspect.getmembers(module):
                # Only consider Command instances that are not already attached to a parent (top-level)
                if not isinstance(object, Command) or object.parent:
                    continue
                # Resolve the template into a concrete list of children to mount:
                # - Prefer a scaffold recorded for this template (if any), otherwise the template itself
                # - Append any additional scaffolds recorded under the same template key
                children = [template := _scaffolds.pop(object, object)] + _templates.pop(template, [])

                # Attach each discovered child under the current command, optionally propagating runtime flags
                for child in children:
                    self.command(child, **({
                        "shell": self.shell,
                        "fancy": self.fancy,
                        "colorful": self.colorful,
                        "deferred": self.deferred
                    } if propagate else {}))

    def trigger(self, fault, /, **options):
        if (
                not hasattr(fault, "__trigger__") or
                not callable(fault.__trigger__) or
                not hasattr(fault, "__replace__") or
                not callable(fault.__replace__)
        ):
            raise TypeError("trigger() argument must have a __trigger__ and __replace__ methods")
        fault = copy.replace(fault, **options, tool=self, shell=self.shell, fancy=self.fancy, colorful=self.colorful, deferred=self.deferred)
        if self.deferred:
            return self._faults.append(fault)  # NOQA: New attributes are not typechecked
        if self.shell:
            self._stderr = True
            self.switches["--help"]()
            self._stderr = False  # NOQA: New attributes are not typechecked
        if self._fallback:  # NOQA: New attributes are not typechecked
            self._fallback(fault)  # NOQA: New attributes are not typechecked
        else:
            trigger(fault)

    def _resolve_token(self, token):
        r"""
        normalize a raw switch token into (name, value) and validate shape.

        purpose
        - parse one token that looks like an option/flag (starts with '-' or '--').
        - accept both inline and spaced styles for options:
          • inline: '--name=value'  → returns ('--name', 'value')
          • spaced: '--name'        → returns ('--name', None)  (value is taken later)
        - flags are presence-only; any '=...' is a user error.

        parameters
        - token: str
          the raw token from the prompt stream (e.g., '--output=path', '-v', '--unknown').

        returns
        - tuple[str, str|None]: (normalized_name, value_or_none)
          • normalized_name is exactly as it appears in self.switches keys.
          • value_or_none carries the inline value when provided; otherwise None.
        - TypeError is raised indirectly when a fault is triggered inside (this method
          uses self.trigger(...); callers treat that as “handled” and continue).

        behavior
        - validates token shape with a single regex:
          (?P<input>--?[^\W\d_](-?[^\W_]+)*)(=(?P<value>[^\r\n]*))?
            • input: '-x', '--name', '-long-name' (unicode letters allowed)
            • optional '=value' tail (value may be empty string)
        - on malformed token: triggers MalformedTokenError with a friendly hint.
        - on unknown switch: suggests near matches and triggers UnknownSwitchError.
        - on empty inline value for an Option: emits EmptyOptionValueWarning with
          contextual hint (inline-only vs spaced-allowed).
        - on any inline value for a Flag: triggers FlagAssignmentError (flags cannot take values).
        """
        # shape: <name>[=<value>] where <name> matches our option/flag grammar
        match = re.fullmatch(r"(?P<input>--?[^\W\d_](-?[^\W_]+)*)(=(?P<value>[^\r\n]*))?", token)

        if not match:
            # malformed switch spelling; guide user towards --help and show examples
            return self.trigger(MalformedTokenError(
                "bad form of option or flag %r at %s position" % (token, _ordinal(self._index)),
                title="malformed option or flag",
                code=FaultCode.MALFORMED_TOKEN,
                hint="try '%s --help' to see valid spellings and forms (e.g., --name=value)" % " ".join(step.name for step in self.path),
                token=token,
                index=self._index,
                docs=getdoc(FaultCode.MALFORMED_TOKEN)
            ))

        input = match["input"]
        value = match["value"]  # None if no '=...' was present; '' if '=' present but nothing after it

        # ensure the switch name is known; otherwise suggest the closest matches
        try:
            argument = self.switches[input]
        except KeyError:
            suggestions = difflib.get_close_matches(input, self.switches.keys(), 5)
            try:
                hint = "did you mean %r? you can also run '%s --help' to see all options" % (
                    suggestions[0],
                    " ".join(step.name for step in self.path)
                )
            except IndexError:
                hint = "try '%s --help' to see all available options" % " ".join(step.name for step in self.path)
            return self.trigger(UnknownSwitchError(
                "unknown option or flag %r at %s position" % (input, _ordinal(self._index)),
                title="unknown option or flag",
                code=FaultCode.UNKNOWN_SWITCH,
                input=input,
                suggestions=suggestions,
                hint=hint,
                docs=getdoc(FaultCode.UNKNOWN_SWITCH)
            ))

        # when '=...' exists in the token, value is a str (possibly empty)
        if isinstance(value, str):
            # option with empty inline value: guide user depending on inline policy
            if isinstance(argument, Option) and not value:
                if argument.inline:
                    hint = "add a value after '=' (for example: %s=<value>)" % input
                else:
                    hint = ("add a value after '=' (for example: %s=<value>)"
                            " or "
                            "remove '=' and pass it after a space (for example: %s <value>)") % (input, input)

                self.trigger(EmptyOptionValueWarning(
                    "empty inline value for option %r at %s position" % (input, _ordinal(self._index)),
                    title="empty inline value",
                    code=FaultCode.EMPTY_INLINE_VALUE,
                    input=input,
                    argument=argument,
                    hint=hint,
                    docs=getdoc(FaultCode.EMPTY_INLINE_VALUE)
                ))

            # flags cannot accept any '=...' tail
            if isinstance(argument, Flag):
                self.trigger(FlagAssignmentError(
                    "flag %r at %s position cannot have an inline value" % (input, _ordinal(self._index)),
                    title="flag cannot take a value",
                    code=FaultCode.FLAG_ASSIGNMENT,
                    input=input,
                    argument=argument,
                    hint="remove everything from '=' (for example: %s)" % input,
                    docs=getdoc(FaultCode.FLAG_ASSIGNMENT)
                ))

        # hand back the normalized name and its inline value (or None) to the caller
        return input, value

    # noinspection PyUnboundLocalVariable
    def _getvalues(self, argument, input, tokens):
        """
        consume and convert value(s) for a spec, with position-first messages.

        purpose
        - read the exact number/shape of tokens required by 'argument.nargs' from the
          given tokens deque, validating arity and converting each raw string via
          argument.type. all user-facing messages lead with the ordinal position
          so beginners can learn by empiric feedback.

        parameters
        - argument: Cardinal | Option
          the spec that defines arity (nargs), converter (type), choices, etc.
        - input: str
          the canonical key for the namespace (param name for cardinals; alias for options).
        - tokens: deque[str]
          token stream to pull from. when different from self._tokens, we’re parsing
          an inline context (e.g., --opt=…).

        returns
        - Any | list[Any] | Unset
          converted value(s) (or Unset for optional-single that was omitted).

        faults (examples, not exhaustive)
        - OptionValueRequiredError: single value required but none present.
        - InlineExtraValuesError: extra inline tail after a single value.
        - AtLeastOneValueRequiredError: '+' arity without any value.
        - NotEnoughValuesError: fixed arity with missing values.
        - EmptyValueError: empty token where a value is required.
        - DelegatedCommandWarning/Error: converter warnings/errors with context.
        - InvalidChoiceError: value not in declared choices.

        notes
        - messages prefer “from <ordinal> position” when the value position is derived
          from the switch (inline form) or when cardinal positions are ambiguous.
        - index updates: self._index is advanced only when consuming spaced tokens
          (inline keeps the index anchored on the option name).
        """
        start = self._index
        inline = tokens is not self._tokens
        # Peek predicate: there’s a next token and either it’s not a switch-like
        # token or current arity is greedy (Ellipsis) which swallows '-' tokens too.
        peekable = lambda: tokens and (not tokens[0].startswith("-") or nargs is Ellipsis)

        match nargs := argument.nargs:
            case "?" | None:
                # optional/single arity — try a single token; for None on options, a value is required
                if nargs is None and not peekable():
                    self.trigger(OptionValueRequiredError(
                        "option %r at %s position requires a value" % (input, _ordinal(start)),
                        title="missing option value",
                        code=FaultCode.OPTION_VALUE_REQUIRED,
                        input=input,
                        index=start,
                        argument=argument,
                        hint="provide a value (e.g., %s=value)" % input,
                        docs=getdoc(FaultCode.OPTION_VALUE_REQUIRED),
                        ))
                # consume one token if available; otherwise Unset for '?'
                result = tokens.popleft().strip() if peekable() else Unset
                # inline single-value must not have extra inline tail
                if inline and tokens:
                    self.trigger(InlineExtraValuesError(
                        "option %r at %s position has extra inline values" % (input, _ordinal(start)),
                        title="extra inline values",
                        code=FaultCode.INLINE_EXTRA_VALUES,
                        input=input,
                        index=start,
                        argument=argument,
                        hint="use a single value in the inline form (e.g., %s=value)" % input,
                        docs=getdoc(FaultCode.INLINE_EXTRA_VALUES),
                        ))
                # advance index only when we actually consumed a spaced token
                self._index += 1 * (not inline and result is not Unset)

            case "*" | "+" | EllipsisType():
                # variadic arities (“*”, “+”, greedy “…”) — collect until sentinel
                result = []
                if nargs == "+" and not peekable():
                    self.trigger(AtLeastOneValueRequiredError(
                        "option %r at %s position requires at least one value" % (input, _ordinal(start)),
                        title="missing value",
                        code=FaultCode.AT_LEAST_ONE_VALUE_REQUIRED,
                        input=input,
                        index=start,
                        argument=argument,
                        hint="provide one or more values after %s" % input,
                        docs=getdoc(FaultCode.AT_LEAST_ONE_VALUE_REQUIRED),
                        ))
                # consume as many as fit (greedy allows '-' tokens as values)
                while peekable():
                    result.append(tokens.popleft().strip())
                    self._index += 1 * (not inline)

            case _:
                # fixed N arity — consume up to N values
                result = []
                while peekable() and len(result) < nargs:
                    result.append(tokens.popleft().strip())
                    self._index += 1 * (not inline)
                if len(result) < nargs:
                    # position-aware, cardinal-friendly copy
                    if isinstance(argument, Cardinal):
                        message = "cardinal from %s position must be follow by exactly %d values" % (_ordinal(start), nargs - 1)
                    else:
                        message = "option %r at %s position requires exactly %d values" % (input, _ordinal(start), nargs)
                    self.trigger(NotEnoughValuesError(
                        message,
                        title="not enough values",
                        code=FaultCode.NOT_ENOUGH_VALUES,
                        input=input,
                        index=start,
                        argument=argument,
                        hint="add the missing value%s" % ("" if nargs == 1 else "s"),
                        docs=getdoc(FaultCode.NOT_ENOUGH_VALUES),
                    ))
                # inline fixed-N must not trail extra inline values
                if inline and tokens:
                    self.trigger(InlineExtraValuesError(
                        "option %r at %s position has extra inline values" % (input, _ordinal(self._index)),
                        title="extra inline values",
                        code=FaultCode.INLINE_EXTRA_VALUES,
                        input=input,
                        index=self._index,
                        argument=argument,
                        hint="keep a single inline value; move others after the option",
                        docs=getdoc(FaultCode.INLINE_EXTRA_VALUES),
                        ))

        # true when converter work happens element-wise (greedy/variadic or fixed-N)
        variadic = nargs is Ellipsis or nargs in ("*", "+") or isinstance(nargs, int)

        if variadic:
            # compute where sub-positions start for message accuracy
            if isinstance(argument, Cardinal):
                begin = start
            else:
                # for options: if spaced, first value is after the name → start at start + 1
                # if inline, sub-position is ambiguous → we will mark as -1 below
                begin = start * (not inline) + 1  # skip option name when spaced

            index = 0
            for position, object in enumerate(result, start=begin):
                if not object:
                    # empty token — helpful, position-first guidance
                    route = " ".join(step.name for step in self.path)
                    typename = getattr(getattr(argument, "type", None), "__name__", "value")
                    if isinstance(argument, Cardinal):
                        message = "empty positional value from %s position" % _ordinal(self._index)
                        hint = "provide a non-empty positional value; run '%s --help' to see expected inputs" % route
                    else:
                        message = "empty value at %s %sposition (for option %r from %s position)" % (
                            _ordinal(position), "sub" * inline, input, _ordinal(start)
                        )
                        hint = (
                                "add a non-empty value after '=' (for example: %s=<%s>)"
                                % (input, typename)
                        ) if inline else (
                                "add a non-empty value after the name (for example: %s <%s>)"
                                % (input, typename)
                        )
                    self.trigger(EmptyValueError(
                        message,
                        title="empty value",
                        code=FaultCode.EMPTY_VALUE,
                        index=start,
                        input=input,
                        subindex=position if not inline else -1,  # mark variadic sub-position when meaningful
                        argument=argument,
                        hint=hint,
                        docs=getdoc(FaultCode.EMPTY_VALUE),
                    ))
                    continue
                try:
                    # convert and collect converter warnings (if any)
                    with catch_warnings(record=True) as warnings:
                        result[index] = argument.type(object)
                        index += 1
                    for warning in map(lambda warning: warning.message, warnings):
                        route = " ".join(step.name for step in self.path)
                        typename = getattr(getattr(argument, "type", None), "__name__", "value")
                        if isinstance(argument, Cardinal):
                            message = "cardinal value at %s from %s position raised a conversion warning" % (
                                _ordinal(position), _ordinal(start)
                            )
                            hint = "check the value format; expected %s. run '%s --help' for examples" % (typename, route)
                        else:
                            message = "value at %s %sposition (for option %r from %s position) raised a conversion warning" % (
                                _ordinal(position), "sub" * inline, input, _ordinal(start)
                            )
                            hint = "check the value format for %r; expected %s. run '%s --help' for examples" % (
                                input, typename, route
                            )
                        self.trigger(DelegatedCommandWarning(
                            message,
                            title="conversion warning",
                            code=FaultCode.DELEGATED_WARNING,
                            index=start,
                            input=input,
                            subindex=position if not inline else -1,
                            argument=argument,
                            hint=hint,
                            docs=getdoc(FaultCode.DELEGATED_WARNING),
                            warning=warning
                        ))
                except Exception as exception:
                    # conversion error — keep it calm, technical, and actionable
                    route = " ".join(step.name for step in self.path)
                    typename = getattr(getattr(argument, "type", None), "__name__", "value")
                    if isinstance(argument, Cardinal):
                        message = "cardinal value at %s from %s position cannot be converted" % (
                            _ordinal(position), _ordinal(start)
                        )
                        hint = "use a valid %s; run '%s --help' to see examples" % (typename, route)
                    else:
                        message = "value at %s %sposition (for option %r from %s position) cannot be converted" % (
                            _ordinal(position), "sub" * inline, input, _ordinal(start)
                        )
                        hint = "use a valid %s for %r; run '%s --help' to see examples" % (typename, input, route)
                    self.trigger(DelegatedCommandError(
                        message,
                        title="conversion error",
                        code=FaultCode.DELEGATED_ERROR,
                        index=start,
                        input=input,
                        subindex=position if not inline else -1,
                        argument=argument,
                        hint=hint,
                        docs=getdoc(FaultCode.DELEGATED_ERROR),
                        exception=exception,
                    ))

        elif result is not Unset:
            # single value path — convert once and translate warnings/errors
            try:
                with catch_warnings(record=True) as warnings:
                    result = argument.type(result)
                for warning in map(lambda warning: warning.message, warnings):
                    route = " ".join(step.name for step in self.path)
                    typename = getattr(getattr(argument, "type", None), "__name__", "value")

                    if isinstance(argument, Cardinal):
                        # cardinals: index points to the value itself → use “at”
                        message = "cardinal value at %s position raised a conversion warning" % _ordinal(self._index)
                        hint = "check the value format; expected %s. run '%s --help' for examples" % (typename, route)
                    else:
                        inline = bool(getattr(argument, "inline", False))
                        # inline name anchors the index to the option name → “from”; spaced → “at”
                        where = "from" if inline else "at"
                        message = "value for option %r %s %s position raised a conversion warning" % (
                            input, where, _ordinal(self._index)
                        )
                        hint = (
                                "check the value format for %r; expected %s (for example: %s=<%s>)"
                                % (input, typename, input, typename)
                        ) if inline else (
                                "check the value format for %r; expected %s (for example: %s <%s>)"
                                % (input, typename, input, typename)
                        )

                    self.trigger(DelegatedCommandWarning(
                        message,
                        title="conversion warning",
                        code=FaultCode.DELEGATED_WARNING,
                        index=self._index,  # inline: name position; spaced/cardinal: value position
                        input=input,
                        subindex=-1,
                        argument=argument,
                        hint=hint,
                        docs=getdoc(FaultCode.DELEGATED_WARNING),
                        warning=warning
                    ))
            except Exception as exception:
                route = " ".join(step.name for step in self.path)
                typename = getattr(getattr(argument, "type", None), "__name__", "value")

                if isinstance(argument, Cardinal):
                    message = "cardinal value at %s position cannot be converted" % _ordinal(self._index)
                    hint = "use a valid %s; run '%s --help' to see examples" % (typename, route)
                else:
                    inline = bool(getattr(argument, "inline", False))
                    where = "from" if inline else "at"
                    message = "value for option %r %s %s position cannot be converted" % (
                        input, where, _ordinal(self._index)
                    )
                    hint = (
                            "use a valid %s for %r (for example: %s=<%s>); run '%s --help' for examples"
                            % (typename, input, input, typename, route)
                    ) if inline else (
                            "use a valid %s for %r (for example: %s <%s>); run '%s --help' for examples"
                            % (typename, input, input, typename, route)
                    )

                self.trigger(DelegatedCommandError(
                    message,
                    title="conversion error",
                    code=FaultCode.DELEGATED_ERROR,
                    index=self._index,
                    input=input,
                    subindex=-1,
                    argument=argument,
                    hint=hint,
                    docs=getdoc(FaultCode.DELEGATED_ERROR),
                    exception=exception
                ))

        # choices validation (variadic or single)
        if argument.choices and result is not Unset:
            if variadic:
                if isinstance(argument, Cardinal):
                    begin = start
                else:
                    begin = start * (not inline) + 1  # skip option name when spaced

                for position, object in enumerate(result, start=begin):
                    if not object:
                        continue  # already warned above for empty
                    if object not in argument.choices:
                        allowed = " · ".join(map(str, argument.choices))

                        if isinstance(argument, Cardinal):
                            message = "cardinal from %s position is not a valid choice" % _ordinal(position)
                            hint = "use one of: %s" % allowed
                            self.trigger(InvalidChoiceError(
                                message,
                                title="invalid choice",
                                code=FaultCode.INVALID_CHOICE,
                                index=start,
                                input=input,
                                subindex=position if not inline else -1,
                                argument=argument,
                                hint=hint,
                                docs=getdoc(FaultCode.INVALID_CHOICE),
                            ))
                        else:
                            message = "value at %s %sposition (for option %r from %s position) is not a valid choice" % (
                                _ordinal(position), "sub" * inline, input, _ordinal(start)
                            )
                            hint = "use one of: %s" % allowed
                            self.trigger(InvalidChoiceError(
                                message,
                                title="invalid choice",
                                code=FaultCode.INVALID_CHOICE,
                                index=start,
                                input=input,
                                subindex=position if not inline else -1,
                                argument=argument,
                                hint=hint,
                                docs=getdoc(FaultCode.INVALID_CHOICE),
                            ))
            else:
                if result not in argument.choices:
                    allowed = " · ".join(map(str, argument.choices))

                    if isinstance(argument, Cardinal):
                        message = "cardinal from %s position is not a valid choice" % _ordinal(self._index)
                        hint = "use one of: %s" % allowed
                        self.trigger(InvalidChoiceError(
                            message,
                            title="invalid choice",
                            code=FaultCode.INVALID_CHOICE,
                            index=self._index,
                            input=input,
                            subindex=-1,
                            argument=argument,
                            hint=hint,
                            docs=getdoc(FaultCode.INVALID_CHOICE),
                        ))
                    else:
                        if inline:
                            message = "value for option %r from %s position is not a valid choice" % (
                                input, _ordinal(start)
                            )
                        else:
                            message = "value at %s position (for option %r from %s position) is not a valid choice" % (
                                _ordinal(self._index), input, _ordinal(start)
                            )
                        hint = "use one of: %s" % allowed
                        self.trigger(InvalidChoiceError(
                            message,
                            title="invalid choice",
                            code=FaultCode.INVALID_CHOICE,
                            index=self._index,
                            input=input,
                            subindex=-1,
                            argument=argument,
                            hint=hint,
                            docs=getdoc(FaultCode.INVALID_CHOICE),
                        ))

        # force the index forward by one for options (accounts for the switch token itself)
        self._index += 1 * isinstance(argument, Option)
        # materialize default when optional-single is Unset; otherwise return converted result as-is
        return coalesce(result, argument.default)

    def _parse_cardinal(self, argument, input, tokens):
        """
        parse a positional (cardinal) argument and store its value(s).

        behavior
        - delegates to _getvalues(argument, input, tokens) to consume exactly the
          number/shape of tokens required by the argument’s declared arity (nargs).
        - saves the result under the canonical cardinal key (the parameter name)
          in the namespace, so handlers can retrieve it by 'input'.

        parameters
        - argument: Cardinal
          the concrete positional spec (includes arity, type, etc.).
        - input: str
          the canonical name used as the namespace key for this cardinal.
        - tokens: deque[str]
          the remaining token stream to consume from.

        notes
        - _getvalues is responsible for arity validation, type conversion, and
          fault reporting (e.g., not-enough-values, at-least-one-required, etc.).
        """
        # store cardinal value(s) under its canonical name (cardinals are unnamed in CLI, but
        # we keep a stable key from the parameter name for handlers to read back).
        self._namespace[input] = self._getvalues(argument, input, tokens)

    def _parse_option(self, argument, input, tokens):
        """
        parse a named option and store its value(s) under all aliases.

        behavior
        - delegates to _getvalues(argument, input, tokens) to consume the correct
          number/shape of tokens according to the option’s arity (nargs).
        - mirrors the resulting value(s) for every alias in argument.names so that
          lookups by any alias succeed consistently.

        parameters
        - argument: Option
          the concrete option spec (names, arity, type, inline policy, etc.).
        - input: str
          the canonical long/short form that matched in parsing (primary alias).
        - tokens: deque[str]
          the remaining token stream to consume from.

        notes
        - using dict.fromkeys ensures all aliases map to the same value object,
          keeping the namespace coherent even when users mix aliases.
        - _getvalues handles edge cases (missing value, extra inline values, etc.) and
          emits faults where appropriate; this method only records the result.
        """
        # mirror the resolved value(s) across all declared aliases so any alias can be used later.
        self._namespace.update(dict.fromkeys(argument.names, self._getvalues(argument, input, tokens)))

    def _parse_flag(self, argument):
        """
        record a flag’s presence in the namespace.

        purpose
        - flags are presence-only; when seen, we mark all of their aliases as True
          so lookups by any name succeed consistently.

        parameters
        - argument: Flag
          the already-resolved flag specification for the current token.

        side effects
        - updates self._namespace in place, setting each alias to True.
        """
        # mark every alias as present; flags don’t carry a value
        self._namespace.update(dict.fromkeys(argument.names, True))
        self._index += 1

    def _handle(self, argument, input, *, index=None):
        """
        run the bound handler once and surface delegated warnings/errors.

        design
        - idempotent: if this 'argument' was already handled in this pass, return.
        - position-first copy: every message includes the ordinal, helping users
          learn by trying (“from third position”, etc.).
        - flow-preserving: no refactor, only explicit, beginner-friendly defaults.

        invariants
        - input and index are always resolvable for a scheduled handler:
          • when 'index' is not passed, it is obtained from self._waits[argument].
          • the namespace already contains the value(s) for 'input' at this point,
            so lookups and calls do not raise KeyError in this method.

        fault shaping
        - warnings (captured via catch_warnings):
          • emitted as DelegatedCommandWarning with position-aware messages.
        - errors (any Exception from the handler):
          • wrapped as DelegatedCommandError with the same position-aware message.

        notes
        - cardinals are unnamed; messages avoid quoting a name for them and use
          the ordinal position instead (“from … position”).
        """
        if argument in self._calls:
            return

        if not index:
            # safe by invariant: this handler was previously queued with (input, index)
            # and thus the key exists; no KeyError can occur here.
            input, index = self._waits.pop(argument)

        try:
            with catch_warnings(record=True) as warnings:
                # route the call according to declared arity (nargs)
                match getattr(argument, "nargs", Unset):
                    case UnsetType():
                        argument()
                    case "?" | None:
                        # invariant: self._namespace[input] exists (no KeyError)
                        argument(self._namespace[input])
                    case "*" | "+" | int() | EllipsisType():
                        # invariant: self._namespace[input] exists (no KeyError)
                        argument(*self._namespace[input])

            # translate captured stdlib warnings into delegated, position-aware warnings
            for warning in map(lambda x: x.message, warnings):
                if isinstance(argument, Cardinal):
                    kind = "positional"
                    message = "something occurred in cardinal from %s position" % _ordinal(index)
                else:
                    kind = "option" if isinstance(argument, Option) else "flag"
                    message = "something occurred in %s %r at %s position" % (
                        kind, input, _ordinal(index)
                    )

                self.trigger(DelegatedCommandWarning(
                    message,
                    title="delegated %s warning" % kind,
                    code=FaultCode.DELEGATED_WARNING,
                    input=input,
                    index=index,
                    argument=argument,
                    hint="check additional logs for more details",
                    docs=getdoc(FaultCode.DELEGATED_WARNING),
                    warning=warning
                ))
        except Exception as exception:
            # unexpected failure in the user handler — provide calm, technical guidance
            if isinstance(argument, Cardinal):
                kind = "positional"
                message = "something occurred in cardinal from %s position" % _ordinal(index)
            else:
                kind = "option" if isinstance(argument, Option) else "flag"
                message = "something occurred in %s %r at %s position" % (
                    kind, input, _ordinal(index)
                )
            self.trigger(DelegatedCommandError(
                message,
                title="delegated %s error" % kind,
                code=FaultCode.DELEGATED_ERROR,
                input=input,
                index=index,
                argument=argument,
                hint="check additional logs for more details",
                docs=getdoc(FaultCode.DELEGATED_ERROR),
                exception=exception
            ))

        # mark as handled to avoid re-invocation through aliases or duplicates
        self._calls.add(argument)

    def _finalize(self, *, help=True):
        """
        finalize parsing/execution by surfacing warnings and raising exits.

        purpose
        - sweep the collected faults and:
          • emit all warnings (non-fatal), then
          • if any exceptions remain, optionally show help once (see 'help') and
            raise a CommandExit to terminate the run or shell step.

        parameters
        - help: bool (keyword-only)
          show contextual help once before exiting when exceptions are present and
          we are running in shell mode. this prevents double-printing help when
          an early-terminating helper (like '--help') already ran: pass help=False
          from those paths to suppress the extra render.

        behavior
        - partition self._faults into exceptions vs warnings.
        - trigger (print) all warnings immediately.
        - if no exceptions → return (normal continuation).
        - if exceptions exist:
            • when shell is True and help is True, render self's help once.
            • raise a CommandExit containing all exceptions, carrying ui flags
              (shell/fancy/colorful/deferred) so the outer runner can handle it.

        notes
        - this method centralizes “graceful exit” behavior so callers do not have
          to reason about duplicate help output or how to batch warnings.
        """
        exceptions = []
        warnings = []

        # split faults into exception-like and warning-like groups
        for fault in self._faults:
            if isinstance(fault, CommandException):
                exceptions.append(fault)
            elif isinstance(fault, CommandWarning):
                warnings.append(fault)
            else:
                raise RuntimeError("unexpected fault")

        # print all warnings (soft feedback, does not stop execution)
        for warning in warnings:
            trigger(warning)

        # nothing fatal → continue
        if not exceptions:
            return

        # in shell mode, optionally show help once before exiting
        if self.shell and help:
            self.switches["--help"]()

        # raise a grouped exit carrying ui flags (consumed by the runner)
        trigger(
            CommandExit(exceptions),
            tool=self,
            shell=self.shell,
            fancy=self.fancy,
            colorful=self.colorful,
            deferred=self.deferred
        )

    def _parseargs(self, tokens, *, index=1):
        """
        parse argv-like tokens into a namespace, schedule/execute handlers, then dispatch.

        phases
        - setup
          • reset per-run state (namespace, faults, calls).
          • install the working deque and the starting ordinal (1-based).
        - loop (parsing)
          • classify each token as switch/route/positional.
          • resolve switches via _resolve_token(); pick the spec (switches[...] or next cardinal).
          • record UX-first faults (unknown/malformed/duplicate/deprecated/standalone) with ordinal-aware copy.
          • parse the argument:
              – cardinals: _parse_cardinal(...)
              – options:   _parse_option(...) (handles inline vs spaced)
              – flags:     _parse_flag()
          • scheduling:
              – if nowait: invoke immediately via _handle(argument, input, index=start)
              – else: remember (input, start) for post-parse execution (self._waits)
          • terminators:
              – finalize immediately (printing help once, if needed) and return.
        - post-parse
          • run pending handlers (_handle) using captured ordinals for consistent messages.
          • surface structural leftovers: missing cardinals, unparsed tokens.
          • finalize: print warnings; if exceptions, show help (once) and exit.
          • finally, map the namespace to the callback signature and call it.

        indexing
        - self._index advances for spaced tokens; inline keeps index on the option name.
        - messages lead with ordinals (“first/second/…”) to build empiric understanding.

        invariants
        - when a handler runs (nowait or later), (input, index) is known and the namespace has its value(s).
        """
        self._namespace.clear()
        self._faults.clear()
        self._calls.clear()

        self._tokens = tokens
        self._index = index

        cardinals = deque(self.cardinals.keys())
        while self._tokens:
            token = self._tokens.popleft()

            if token.startswith('-') and not (cardinals and self.cardinals[cardinals[0]].nargs is Ellipsis):  # Greedy doesn't make distinction between cardinals and switches
                try:
                    input, value = self._resolve_token(token)
                except TypeError:  # None is not iterable and the trigger was handled inside
                    self._index += 1
                    continue
                argument = self.switches[input]
            elif self.children and not self._namespace:
                try:
                    # first non-switch token can be a subcommand route; delegate if it matches
                    return self.children[input := token]._parseargs(self._tokens, index=self._index + 1)  # NOQA: E-501
                except KeyError:
                    # unknown command/subcommand: offer a suggestion and a help hint
                    suggestions = difflib.get_close_matches(input, self.children.keys(), 5)  # NOQA: F-821
                    route = " ".join(step.name for step in self.path)
                    try:

                        hint = "did you mean %r? you can also run '%s --help' to see available %scommands" % (
                            suggestions[0], route, "sub" * bool(self.parent)
                        )
                    except IndexError:
                        hint = "run '%s --help' to see available %scommands" % (route, "sub" * bool(self.parent))

                    # choose exception/code/title based on whether we are at the root (command) or nested (subcommand)
                    exception = UnknownSubcommandError if self.parent else UnknownCommandError
                    code = FaultCode.UNKNOWN_SUBCOMMAND if self.parent else FaultCode.UNKNOWN_COMMAND
                    type = "subcommand" if self.parent else "command"

                    self.trigger(exception(
                        "unknown %s %r at %s position" % (type, input, _ordinal(self._index)),
                        title="unknown %s" % type,
                        code=code,
                        input=input,
                        index = self._index,
                        suggestions=suggestions,
                        hint=hint,
                        docs=getdoc(code),
                    ))
                    break
            else:
                try:
                    argument = self.cardinals[input := cardinals.popleft()]
                    value = Unset
                except IndexError:
                    # there are no more declared positionals (cardinals) to accept
                    self.trigger(UnexpectedCardinalError(
                        "unexpected positional argument from %s position" % _ordinal(self._index),
                        title="unexpected positional",
                        code=FaultCode.UNEXPECTED_CARDINAL,
                        index=self._index,
                        hint="remove this extra value or run '%s --help' to see the expected usage" % " ".join(step.name for step in self.path),
                        docs=getdoc(FaultCode.UNEXPECTED_CARDINAL)
                    ))
                    self._index += 1
                    continue
                # put back the token so the resolved cardinal can consume it (and any peers) as needed
                self._tokens.appendleft(token)

            if argument.deprecated:
                # deprecation notices should never suggest alternatives by “did you mean”
                # because the name is valid (just discouraged). keep it soft, lowercased,
                # and offer an explicit successor only if we have one on the spec.
                route = " ".join(step.name for step in self.path)

                replacement = getattr(argument, "successor", None) or getattr(argument, "replacement", None)

                if isinstance(argument, Cardinal):
                    kind = "positional argument"
                    message = "positional argument from %s position is deprecated" % _ordinal(self._index)
                    hint = "run '%s --help' to see current usage and alternatives" % route
                    opts = {
                        "index": self._index,
                        "argument": argument,
                    }
                else:
                    kind = "option" if isinstance(argument, Option) else "flag"
                    message = "%s %r at %s position is deprecated" % (kind, input, _ordinal(self._index))
                    if replacement:
                        hint = "use %r instead; run '%s --help' to see details" % (replacement, route)
                    else:
                        hint = "run '%s --help' to see current usage and alternatives" % route
                    opts = {
                        "input": input,
                        "index": self._index,
                        "argument": argument,
                    }

                # emit a deprecation warning with a soft tone that still teaches the next step
                self.trigger(DeprecatedArgumentWarning(
                    message,
                    title="deprecated %s" % kind,
                    code=FaultCode.DEPRECATED_ARGUMENT,
                    hint=hint,
                    docs=getdoc(FaultCode.DEPRECATED_ARGUMENT),
                    **opts
                ))

            if input in self._namespace:
                type = "option" if isinstance(argument, Option) else "flag"

                self.trigger(DuplicatedSwitchError(
                    "%s %r at %s position was already provided" % (type, input, _ordinal(self._index)),
                    title="duplicated %s" % type,
                    code=FaultCode.DUPLICATED_SWITCH,
                    input=input,
                    index=self._index,
                    argument=argument,
                    hint="keep a single %s; each %s can be specified only once" % (type, type),
                    docs=getdoc(FaultCode.DUPLICATED_SWITCH)
                ))

            start = self._index
            if isinstance(argument, Cardinal):
                self._parse_cardinal(argument, input, self._tokens)
            elif isinstance(argument, Option):
                if argument.inline and not value:  # inline, forces the option to be inline-ed
                    self.trigger(MissingInlineValueError(
                        "option %r at %s position must include an inline value" % (input, _ordinal(self._index)),
                        title="missing inline value",
                        code=FaultCode.MISSING_INLINE_VALUE,
                        input=input,
                        index=self._index,
                        argument=argument,
                        hint="use the inline form: %s=<value>" % input,
                        docs=getdoc(FaultCode.MISSING_INLINE_VALUE),
                    ))
                if value:  # Priority splitter char from most strong to less strong
                    tokens = deque(value.split(os.pathsep if os.pathsep in value else ":" if ":" in value else ","))
                else:
                    tokens = self._tokens
                self._parse_option(argument, input, tokens)
            elif isinstance(argument, Flag):
                self._parse_flag(argument)
            else:
                raise RuntimeError("unexpected argument")

            if argument.nowait and argument not in self._calls:
                self._handle(argument, input, index=start)
            if not argument.nowait:
                self._waits.setdefault(argument, (input, start))

            # I there are something remaining or already parsed (Only can enter non-cardinals in this block)

            if getattr(argument, "standalone", False) and len(self._namespace.keys() - argument.names) + len(self._tokens):
                type = "option" if isinstance(argument, Option) else "flag"
                route = " ".join(step.name for step in self.path)

                self.trigger(StandaloneSwitchError(
                    "%s %r from %s position must be used alone" % (type, input, _ordinal(start)),
                    title="standalone %s" % type,
                    code=FaultCode.STANDALONE_SWITCH,
                    input=input,
                    index=start,
                    argument=argument,
                    # actionable, beginner-friendly hint that shows exactly how to run it
                    hint="remove other arguments or run '%s %s' by itself" % (route, input),
                    docs=getdoc(FaultCode.STANDALONE_SWITCH)
                ))

            if getattr(argument, "terminator", False):
                self._finalize(help=self.switches["--help"] is not argument)
                return

        for input in self._namespace.keys():
            try:
                self._handle(self.cardinals[input], input)
            except KeyError:
                self._handle(self.switches[input], input)

        while cardinals:  # No index needed
            nargs = self.cardinals[cardinals.popleft()].nargs
            if nargs == "?" or isinstance(nargs, int | None):
                route = " ".join(step.name for step in self.path)
                self.trigger(MissingCardinalsError(
                    "one or more required cardinals are missing",
                    title="missing cardinals",
                    code=FaultCode.MISSING_CARDINALS,
                    hint="add the missing cardinal values — run '%s --help' to see the expected order" % route,
                    docs=getdoc(FaultCode.MISSING_CARDINALS),
                ))

                break

        if self._tokens:  # No index needed
            route = " ".join(step.name for step in self.path)
            self.trigger(UnparsedTokensError(
                "unparsed input remains",
                title="unparsed input",
                code=FaultCode.UNPARSED_TOKENS,
                # optional payload if your reporter wants to show it
                leftover=list(self._tokens),
                hint="remove the extra inputs — run '%s --help' to see valid forms" % route,
                docs=getdoc(FaultCode.UNPARSED_TOKENS),
            )) # Unparsed remaining tokens

        self._finalize()  # If any error this statement is terminative

        args = ()
        kwargs = {}

        for name, parameter in inspect.signature(self._callback).parameters.items():
            argument = parameter.default
            if parameter.kind is not Parameter.KEYWORD_ONLY:
                object = self._namespace.get(next(iter(getattr(argument, "names", (name,)))), argument.default)
                args += (object,)
            else:
                kwargs[name] = self._namespace.get(next(iter(argument.names)), False)

        self._callback(*args, **kwargs)

    def __invoke__(self, prompt=Unset):
        """
        Execute this command with a token stream.

        Parameters
        - prompt:
          • Unset: read tokens from sys.argv[1:].
          • str: shell-like string; will be split via shlex.split.
          • Iterable[str]: pre-tokenized sequence; each element is trimmed.

        Behavior
        - Normalizes the prompt into a list[str] of tokens and forwards it to
          the internal parser queue (self._parseargs).
        - Rejects invalid prompt types and non-string items in iterables.

        Raises
        - TypeError: when prompt is not Unset/str/Iterable[str], or when an
          iterable contains a non-string element.
        """
        if prompt is Unset:
            tokens = sys.argv[1:]  # Default: execute with current CLI arguments
        elif isinstance(prompt, str):
            tokens = shlex.split(prompt)  # Shell-style splitting for a single string
        elif isinstance(prompt, Iterable):
            # Normalize an iterable of values into a clean list[str] without leading/trailing spaces.
            def _sanitized(iterable):
                """
                Yield trimmed string items from an iterable, validating element types.

                Raises
                - TypeError: if any element is not a string.
                """
                for item in iterable:
                    if not isinstance(item, str):
                        raise TypeError(f"__invoke__() argument must be a string or an iterable of strings")
                    if item := item.strip():
                        yield item
            tokens = list(_sanitized(prompt))
        else:
            raise TypeError(f"__invoke__() argument must be a string or an iterable of strings")

        # Hand off to the argument parser (expects a deque of tokens).
        self._parseargs(deque(tokens))  # type: ignore[attr-defined]


def command(source=Unset, /, *args, **kwargs):
    """
    Create a Command or return a decorator to build it later (non-command template-like).

    Invocation modes
    - Direct callback:
        cmd = command(func, ..., name="x", ...)
      Returns a Command bound to 'func'.

    - Template cloning:
        cmd2 = command(template_cmd, ..., name="y", ...)
      Clones an existing Command, applying overrides.

    - Decorator:
        @command(name="x", ...)
        def func(...): ...
      Returns a decorator that will wrap 'func' into a Command.

    Parameters
    - source: Unset | Callable | Command
      When Unset, a decorator is returned. Otherwise a Command is created.
    - *args, **kwargs: forwarded to Command.__new__ (metadata, runtime flags, etc.).

    Returns
    - Command | Callable[[Callable | Command], Command]
    """
    @rename("command")
    def wrapper(source, /):
        # Accept either a callable (callback) or an existing Command template
        if not callable(source) and not isinstance(source, Command):
            raise TypeError("@command() must be applied to a callable or a command template")
        return Command(source, *args, **kwargs)

    # Direct mode if a source was provided, otherwise return the decorator.
    return wrapper(source) if source is not Unset else wrapper


def invoke(object, prompt=Unset, /):
    """
    Convenience runner for commands or callables.

    Parameters
    - object: an instance providing __invoke__(prompt) or a plain callable.
    - prompt:
      • Unset: read sys.argv[1:].
      • str: split with shlex.split.
      • Iterable[str]: use items as tokens (each must be str).

    Behavior
    - If 'object' implements __invoke__, call it with prompt.
    - If 'object' is a plain callable, wrap it as a Command and then invoke.
    - Otherwise, raise TypeError with a clear hint.

    Raises
    - TypeError: when 'object' cannot be invoked via the above contract or
      when prompt type is invalid for __invoke__.
    """
    # Preferred path: explicit command-like object
    if hasattr(object, "__invoke__") and callable(object.__invoke__):
        object.__invoke__(prompt)
        return

    # Allow a plain callable for convenience (useful in quick scripts/tests)
    # NOTE: This implicit wrapping is kept for developer ergonomics.
    if callable(object):
        return invoke(command(object), prompt)  # type: ignore[arg-type]

    # Construct a helpful error depending on whether prompt was provided
    target = "argument" if prompt is Unset else "first argument"
    raise TypeError(f"invoke() {target} must implement __invoke__ method") from None


__all__ = (
    # Public API surface for consumers of argonaut.commands.
    # These names are re-exported from the package __init__.
    # Keep this list stable: it defines the supported, documented entry points.
    "Command",
    "command",
    "invoke",
)

# Remove the internal metaclass from the module namespace to avoid accidental
# exposure in docs, autocompletion, or star-imports. Not part of the public API.
del CommandType
