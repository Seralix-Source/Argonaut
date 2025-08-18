import functools
import inspect
import os
import re
import shlex
import sys
import textwrap
from collections import deque, defaultdict
from collections.abc import Iterable, Mapping
from inspect import Parameter
from types import MemberDescriptorType

from rich.style import Style

from .arguments import Operand, Option, Switch
from .faults import *
from .null import null
from .null import _update_name, _frozen_property  # NOQA: Internal


class SkipToken(Exception):
    """internal sentinel: skip the current token without aborting parsing."""
    pass


def _get_invoker(callback):
    """
    Synthesize an instance-bound __call__ that forwards to the registered handler,
    mirroring the handler's signature as closely as possible.

    What this does
    - Builds a __call__(...) method dynamically so that a Command instance can be
      invoked with the same parameter shape as the original handler function.
    - Preserves positional-only, positional-or-keyword, and keyword-only sections:
        • Inserts '/' once to mark the end of positional-only parameters.
        • Inserts '*' once to mark the beginning of keyword-only parameters.
    - Renames the implicit instance parameter to avoid shadowing a handler whose
      first parameter is literally named "self".

    Parameters
    - callback: an object with:
        • parameters: list[inspect.Parameter] in declaration order
          (each with .name, .kind, .default),
        • (optional) other attributes used upstream for defaults extraction.

    Generated behavior
    - __call__(self, ...) simply dispatches to self._callback(...)
      with positional args first and keyword-only args passed as keywords.
    - __defaults__ is populated from non-keyword-only parameters that provide a
      default; internal sentinels are normalized via null.nullify(...).
    - __kwdefaults__ is seeded for keyword-only parameters to signal presence
      flags (False by default), aligning with the surrounding invocation logic.

    Notes
    - This is performance-sensitive. It compiles exactly once per unique callback
      shape (per construction site) and avoids repeated signature manipulation.
    - The resulting __call__ carries a stable name via @_update_name("__call__")
      to keep tracebacks and pretty output clean.
    """
    # Choose an instance name that won't collide with a handler named "self"
    parameters = [self := "__self__" if callback.parameters and callback.parameters[0] == "self" else "self"]

    # Track whether we've emitted the positional-only separator '/' or the
    # keyword-only separator '*'
    slashed = False
    starred = False

    # Recreate the handler's shape:
    # - emit '/' once before the first non-pos-only parameter (if any)
    # - emit '*' once before the first keyword-only parameter (if any)
    for parameter in callback.parameters:
        if parameter.kind is Parameter.POSITIONAL_OR_KEYWORD and not slashed:
            parameters.append("/")
            slashed = True
        if parameter.kind is Parameter.KEYWORD_ONLY and not starred:
            if not slashed:
                parameters.append("/")
                slashed = True
            parameters.append("*")
            starred = True
        parameters.append(parameter.name)

    # Build the forwarding lists:
    # - positional-or-keyword and positional-only go as positional args
    # - keyword-only become named arguments
    args = ", ".join(
        f"{parameter.name}" for parameter in callback.parameters
        if parameter.kind is not Parameter.KEYWORD_ONLY
    )
    kwargs = ", ".join(
        f"{parameter.name}={parameter.name}" for parameter in callback.parameters
        if parameter.kind is Parameter.KEYWORD_ONLY
    )

    # Compile a concrete __call__ with the reconstructed signature
    exec(textwrap.dedent(f"""
        @_update_name("__call__")
        def __call__({", ".join(parameters)}):
            return {self}._callback({", ".join([args, kwargs])})
    """), globals(), namespace := {})

    # Positional defaults (excluding keyword-only): null → real default, else pass-through
    namespace["__call__"].__defaults__ = tuple(
        null.nullify(parameter.default.default)
        for parameter in callback.parameters if parameter.kind is not Parameter.KEYWORD_ONLY
    )

    # Keyword-only defaults: presence flags (False by default)
    namespace["__call__"].__kwdefaults__ = {
        parameter.name: False
        for parameter in callback.parameters if parameter.kind is Parameter.KEYWORD_ONLY
    }

    return namespace["__call__"]


def __type__(cls, metadata):
    """
    Internal: synthesize a concrete command type from a base class and a metadata map.

    Purpose
    - Build a lightweight, immutable “view” over the construction‑time metadata
      dict and expose each field as a read‑only attribute via _frozen_property.
    - Attach a one‑shot fallback(...) setter and, when a real callback exists,
      a __call__ whose signature mirrors the handler (compiled by _get_invoker).
    - Provide minimal __repr__/__rich_repr__ hooks for diagnostics and help.

    Inputs
    - cls: the abstract base (e.g., Command) whose name determines the user‑facing
      typename (converted to kebab‑case).
    - metadata: dict[str, Any] whose keys correspond to the command’s attributes
      (already validated and normalized upstream).

    Generated members
    - Read‑only properties for all metadata keys (via _frozen_property).
    - fallback(self, func): single‑assignment fallback handler; returns the func
      for decorator‑style usage.
    - __call__(...): only when a real callback is present; forwards to self._callback
      with a signature built by _get_invoker.
    - __repr__/__rich_repr__: concise diagnostics and Rich‑friendly iteration.
    - __init_subclass__: forbid subclassing of the synthesized type.

    Notes
    - This factory only wires behavior and freezes the current metadata snapshot.
      Any mutations should be completed by processing helpers before calling this.
    - Properties reflect the snapshot; later mutations of the original dict do
      not affect instances.
    """
    __name__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    @_update_name("fallback")
    def fallback(self, fallback):
        """
        Single‑assignment fallback setter.

        Contract
        - Must be callable; raises TypeError otherwise.
        - Can only be set once; raises TypeError if already set.
        - Returns the same callable to support decorator usage.
        """
        if not callable(fallback):
            raise TypeError(f"{__name__} fallback must be callable")
        if self._fallback is not null:
            raise TypeError(f"{__name__} fallback cannot be changed after initialization")
        self._fallback = fallback
        return fallback

    @_update_name("__repr__")
    def __repr__(self):
        """
        Debug‑friendly repr.

        Shape
        - <typename>(key=value, ...) using the current metadata snapshot.
        - Intentionally compact; rich rendering uses __rich_repr__.
        """
        return f"{__name__}({', '.join('%s=%r' % item for item in metadata.items())})"

    @_update_name("__rich_repr__")
    def __rich_repr__(self):
        """
        Rich‑friendly representation.

        Behavior
        - Yield (key, value) pairs so Rich can render a table‑like display.
        - Keeps parity with __repr__ while allowing styled output in help.
        """
        yield from metadata.items()

    @_update_name("__init_subclass__")
    def __init_subclass__(cls, **options):
        """
        Forbid subclassing of synthesized command types.

        Rationale
        - These concrete types are generated and finalized at construction time.
          Allowing subclassing would break immutability guarantees and expand
          surface area without benefit.
        """
        raise TypeError(f"type {__name__!r} is not an acceptable base type")

    return type(cls)(
        __name__,
        (cls, *cls.__bases__),
        {
            # Keep only plain attributes (skip descriptors) to avoid conflicts.
            name: object for name, object in cls.__dict__.items() if not isinstance(object, MemberDescriptorType)
        } | {
            # Freeze and expose metadata fields as read‑only properties.
            name: _frozen_property(name, metadata) for name in metadata.keys()
        } | {
            # Single‑assignment fallback setter.
            "fallback": fallback
        } | ({ # Arity‑sensitive invoker (only when a real callback exists).
            "__call__": _get_invoker(callback)
         } if (callback := metadata.pop("callback")) is not _dummy_callback else {}) | {
            # Diagnostics and subclassing guard.
            "__repr__": __repr__,
            "__rich_repr__": __rich_repr__,
            "__init_subclass__": __init_subclass__
        } | {
            "__module__": "<argonaut-dynamic>"
        }
    )


def _dummy_callback(*args, **kwargs):
    """
    Sentinel handler used for schema‑only commands (no explicit callback).

    When it is set
    - The command was constructed from an iterable of specs (operands/option/switch),
      or otherwise without a callable handler.

    Contract
    - This function must never be executed as a real handler.
      Its presence signals the invoker/parser to return the parsed namespace instead of calling a callback.
      If it is reached, something bypassed the normal control flow.

    Why raise here
    - Failing loudly prevents accidental execution paths that would otherwise
      silently “succeed” without running user code, making debugging harder.

    Typical flow
    - Build the command without a handler (iterable schema).
    - Invoke it: the runtime detects the sentinel and returns a dict‑like
      namespace of parsed values (rather than calling a handler).

    Developer note
    - Other parts of the system rely on identity checks against this sentinel
      (e.g., `self._callback is _dummy_callback`) to choose the correct return
      behavior.
      Do not replace or wrap it with another callable.
    """
    raise NotImplementedError("no callback: command returns a parsed namespace")


_number = lambda number: {
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


def _get_callback(cls, source, metadata):
    """
    Normalize the provided `source` (callable or iterable of specs) into a concrete callback
    and populate the command's argument indices in-place.

    Purpose
    - Build the argument schema for a Command by inspecting either:
      • a handler signature (decorator style), or
      • an iterable of specs (factory style).
    - While doing so, enforce ordering and shape constraints that yield a predictable UX.
    - Record every declared spec into a read-only grouping index (metadata["groups"]),
      keyed by the spec's group name, preserving declaration order.

    Inputs
    - cls:     the Command class (used for error messages and normalization).
    - source:  a callable with defaulted parameters whose defaults are “argument‑resoluble”
               objects (operand/option/switch), or an iterable of such resoluble objects.
               An “argument‑resoluble” object provides exactly one of:
                 __operand__(), __option__(), or __switch__().
    - metadata: dict with the mutable indices to be populated:
        • operands:   name→Operand (for callable) or index→Operand (for iterable)
        • qualifiers: alias→Option/Switch (names deduplicated/validated)
        • groups:     group-name→tuple(specs...) (filled here in declaration order)

    Rules and validations
    - Argument‑resoluble: defaults (or iterable items) must yield exactly one of Operand/Option/Switch.
    - Ordering:
      • Operands must come first; once a qualifier is seen, no more operands are allowed.
      • Within qualifiers: options and switches may interleave, but name collisions are forbidden.
    - Handler signature shape (callable only):
      • No *args/**kwargs.
      • Operands must be positional‑only.
      • Options must be positional‑or‑keyword (standard parameters).
      • Switches must be keyword‑only.
    - Greedy positional (Operand with nargs=Ellipsis):
      • Only one greedy operand is permitted.
      • It must be the last operand in the declaration.
    - Hidden/deprecated sequencing for operands:
      • Non‑hidden operands cannot follow a hidden one.
      • Non‑deprecated operands cannot follow a deprecated one.

    Returns
    - The concrete callback stored in metadata["callback"], or the internal _dummy_callback
      if `source` was an iterable (schema‑only command).

    Raises
    - TypeError for any shape/order violations or name collisions.
    """
    __name__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()
    operands = metadata["operands"]
    qualifiers = metadata["qualifiers"]

    # Parsing state for ordering/constraints across declaration
    active = "operand"        # "operand" → still accepting operands; otherwise "option" or "switch"
    greedy = None             # tracks the position/parameter of a greedy operand if seen
    hidden = False            # once a hidden operand appears, subsequent operands must also be hidden
    deprecated = False        # once a deprecated operand appears, subsequent operands must also be deprecated


    def _resolve_spec(x):
        """
        Resolve a single default/item into a concrete spec (Operand/Option/Switch).

        Contract
        - Exactly one of the resolver hooks must be present and callable.
        - The returned object must be an instance of the expected spec type.
        """
        if sum((
            hasattr(x, "__operand__") and callable(x.__operand__),
            hasattr(x, "__option__") and callable(x.__option__),
            hasattr(x, "__switch__") and callable(x.__switch__)
        )) != 1:
            if isinstance(parameter, Parameter):
                raise TypeError(f"{__name__} callback parameter {parameter.name!r} default must be argument-resoluble")
            raise TypeError(f"{__name__} object at {_number(index)} position must be argument-resoluble")

        if hasattr(x, "__operand__"):
            operand = x.__operand__()
            if not isinstance(operand, Operand):
                raise TypeError("__operand__() non-operand returned")
            return operand
        elif hasattr(x, "__option__"):
            option = x.__option__()
            if not isinstance(option, Option):
                raise TypeError("__option__() non-option returned")
            return option
        elif hasattr(x, "__switch__"):
            switch = x.__switch__()
            if not isinstance(switch, Switch):
                raise TypeError("__switch__() non-switch returned")
            return switch
        raise RuntimeError("unreachable")

    def _resolve_operand(operand):
        """
        Register an Operand, enforcing operand‑specific rules.

        Enforced
        - Handler: operand parameters must be positional‑only.
        - Ordering: operands cannot appear after any qualifier was declared.
        - Greedy: a greedy operand (nargs=Ellipsis) must be the last operand.
        - Hidden/deprecated sequencing: once hidden/deprecated is seen, all following
          operands must also be hidden/deprecated respectively.
        """
        nonlocal active, greedy, hidden, deprecated
        if isinstance(parameter, Parameter) and parameter.kind is not Parameter.POSITIONAL_ONLY:
            raise TypeError(f"operand at parameter {parameter.name!r}, parameter must be positional-only")
        if active != "operand":
            raise TypeError(f"operand at {_number(index)} position cannot be defined after a {active}")

        # Greedy (remainder) must be the last positional
        if greedy:
            if isinstance(greedy, Parameter):
                raise TypeError(f"greedy operand at parameter {greedy.name!r} must be the last operand")
            raise TypeError(f"greedy operand at {_number(greedy)} position must be the last operand")
        greedy = parameter if operand.nargs is Ellipsis else None

        # Visibility/deprecation sequencing
        # Once a hidden/deprecated operand appears, forbid non-hidden/non-deprecated thereafter
        hidden |= operand.hidden
        if hidden and not operand.hidden:
            if isinstance(parameter, Parameter):
                raise TypeError(f"non-hidden operand at parameter {parameter.name!r} cannot follow a hidden one")
            raise TypeError(f"non-hidden operand at {_number(index)} position cannot follow a hidden one")

        deprecated |= operand.deprecated
        if deprecated and not operand.deprecated:
            if isinstance(parameter, Parameter):
                raise TypeError(f"non-deprecated operand at parameter {parameter.name!r} cannot follow a deprecated one")
            raise TypeError(f"non-deprecated operand at {_number(index)} position cannot follow a deprecated one")

        # Indexing strategy:
        # - Callable: index by parameter name.
        # - Iterable: index by 1-based position.
        if isinstance(parameter, Parameter):
            operands[parameter.name] = operand
        else:
            operands[index] = operand

    def _resolve_qualifier(qualifier):
        """
        Register an Option or Switch, enforcing qualifier‑specific shape and name constraints.

        Enforced
        - Handler:
          • Option params must be POSITIONAL_OR_KEYWORD (standard).
          • Switch params must be KEYWORD_ONLY.
        - Iterable:
          • Options are not allowed to follow a switch when `active == "switch"`.
        - Names:
          • All aliases for the qualifier must be unique across previously seen qualifiers.
        """
        nonlocal active

        # Parameter shape constraints (callable source)
        if isinstance(parameter, Parameter):
            if isinstance(qualifier, Option) and parameter.kind is not Parameter.POSITIONAL_OR_KEYWORD:
                raise TypeError(f"option at parameter {parameter.name!r}, parameter must be standard")
            elif isinstance(qualifier, Switch) and parameter.kind is not Parameter.KEYWORD_ONLY:
                raise TypeError(f"switch at parameter {parameter.name!r}, parameter must be keyword-only")

        # Iterable ordering constraint: after a switch batch starts, no options can follow
        if isinstance(qualifier, Option) and active == "switch":
            raise TypeError(f"option at {_number(index)} position cannot follow a switch")

        # Track which qualifier kind is currently active (affects iterable ordering)
        active = "option" if isinstance(qualifier, Option) else "switch"

        # Guard against alias collisions across all previously seen qualifiers
        for name in qualifier.names & qualifiers.keys():
            raise TypeError(f"{active} name {name!r} is already being used")
        else:
            qualifiers.update(dict.fromkeys(qualifier.names, qualifier))

    # Source: callable handler
    if callable(source):
        try:
            source.signature = inspect.signature(source)
            source.parameters = list(source.signature.parameters.values())
        except ValueError:
            raise TypeError(f"{__name__} callback must be callable with a signature") from None
        except AttributeError:
            raise TypeError(f"{__name__} callback must allow attribute assignments") from None

        for parameter in source.parameters:
            # For clarity/predictability we forbid *args/**kwargs in handlers
            if parameter.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD):
                raise TypeError(f"{__name__} callback parameter {parameter.name!r} cannot be variadic")
            # Each parameter must have a default that is argument‑resoluble
            if parameter.default is Parameter.empty:
                raise TypeError(f"{__name__} callback parameter {parameter.name!r} must have a default")

            # Resolve and register
            if isinstance(argument := _resolve_spec(parameter.default), Operand):
                _resolve_operand(argument)
            else:
                _resolve_qualifier(argument)
            # Grouping index: record the spec under its group name (declaration order)
            metadata["groups"][argument.group] += (argument,)


    # Source: iterable of specs (schema‑only)
    elif isinstance(source, Iterable):
        for index, parameter in enumerate(source, start=1):
            if isinstance(argument := _resolve_spec(parameter), Operand):
                _resolve_operand(argument)
            else:
                _resolve_qualifier(argument)
            # Grouping index: record the spec under its group name (declaration order)
            metadata["groups"][argument.group] += (argument,)


    # Invalid source
    else:
        raise TypeError(f"{__name__} first argument must be a callable or an iterable of argument-resoluble")

    return metadata.setdefault("callback", source if callable(source) else _dummy_callback)


def _process_conflicts(cls, conflicts, metadata):
    """
    Internal: validate and normalize mutually exclusive group sets.

    Purpose
    - Read metadata["conflicts"] (iterable of iterables of group names), validate
      shape and membership, and expand it into a symmetric lookup using the
      provided `conflicts` mapping.

    Expected input (user-facing)
    - metadata["conflicts"]: iterable of iterables, where each inner iterable
      denotes a set of groups that cannot co-exist, e.g.:
        (("json", "yaml", "xml"), ("output", "dry-run"), ...)

    Assumptions
    - `conflicts` is an existing mapping with default frozenset values
      (e.g., defaultdict(frozenset)). Values are re-bound with set unions:
        conflicts[g] |= sanitized - {g}
      to produce symmetric relationships (A→B implies B→A).

    Validation rules
    - The outer object and each inner object must be iterable.
    - Group names must be str, trimmed to non-empty.
    - Every referenced group must exist in metadata["groups"].
    - Each conflict set must contain at least two distinct groups.
    - Duplicates within an inner set are not allowed.

    Output
    - metadata["conflicts"] is set to the populated `conflicts` mapping containing
      symmetric frozenset relationships for all declared conflicts.

    Errors (concise, lowercased)
    - TypeError when shape or types are invalid:
        "{name} conflicts must be an iterable of iterables of strings"
        "{name} conflicts must be an iterable of iterables of non-empty strings"
        "group 'x' of {name} conflict is duplicated"
        "group 'x' of {name} conflict is not declared"
    - ValueError when a set has fewer than two distinct groups:
        "{name} conflicts must be an iterable of iterables of at least two groups"
    """
    __name__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    if not isinstance(metadata["conflicts"], Iterable):
        raise TypeError(f"{__name__} conflicts must be an iterable of iterables of strings")

    for groups in metadata["conflicts"]:
        if not isinstance(groups, Iterable):
            raise TypeError(f"{__name__} conflicts must be an iterable of iterables of strings")
        sanitized = set()
        for group in groups:
            if not isinstance(group, str):
                raise TypeError(f"{__name__} conflicts must be an iterable of iterables of strings")
            elif not (group := group.strip()):
                raise ValueError(f"{__name__} conflicts must be an iterable of iterables of non-empty strings")
            if group in sanitized:
                raise TypeError(f"group {group!r} of {__name__} conflict is duplicated")
            sanitized.add(group)
            if group not in metadata["groups"]:
                raise TypeError(f"group {group!r} of {__name__} conflict is not declared")
        if len(sanitized) < 2:
            raise ValueError(f"{__name__} conflicts must be an iterable of iterables of at least two groups")
        for group in sanitized:
            conflicts[group] |= sanitized - {group}

    metadata["conflicts"] = conflicts


def _process_strings(cls, metadata):
    __name__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    for name, object in map(lambda x: (x, metadata[x]), (
        "name",
        "descr",
    )):
        if object is not null and not isinstance(object, str):
            raise TypeError(f"{__name__} {name!r} must be a string")
        elif isinstance(object, str) and not (object := object.strip()):
            raise TypeError(f"{__name__} {name!r} must be a non-empty string")
        metadata[name] = null.nullify(object)


def _process_styles(cls, metadata):
    __name__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    styles = {}
    if not isinstance(metadata["styles"], Mapping):
        raise TypeError(f"{__name__} 'styles' must be a mapping")
    for tag, style in metadata["styles"].items():
        if not isinstance(tag, str) or not isinstance(style, (str, Style)):
            raise TypeError(f"{__name__} 'styles' must be a mapping of strings to styles")
        styles[tag] = style

    metadata["styles"] = getattr(metadata["parent"], "styles", {}) | styles


class Command:
    __slots__ = (
        "_callback",
        "_fallback",

        "_children",

        "_namespace",
        "_pos",
    )

    @property
    def root(self):
        """
        Return the root command in this hierarchy.

        Behavior
        - Walks parent links to the top-most ancestor and returns it.
        - Cost is linear in the depth of the hierarchy.

        Notes
        - The “root” is the command whose parent is None.
        """
        child, parent = self, self.parent
        while parent:
            child, parent = parent, parent.parent
        return child

    @property
    def rootpath(self):
        """
        Return the path from the root command to this command (inclusive).

        Behavior
        - Produces a tuple ordered from the root (first) down to this command (last).
        - If this command is itself the root, the tuple contains just this command.

        Implementation
        - Uses a deque and appendleft to avoid building-and-reversing an intermediate list.
          This keeps the operation strictly linear with small constant factors.

        Notes
        - If you need the path without the root, slice: rootpath[1:].
        - If you need the path without self, slice: rootpath[:-1].
        """
        path = deque([command := self])
        while command.parent:
            path.appendleft(command := command.parent)
        return tuple(path)

    def __new__(
            cls,
            source,
            /,
            parent=null,
            name=os.path.basename(sys.argv[0]),
            descr=null,
            styles=null,
            conflicts=(),
            *,
            shell=False,
            fancy=False,
            colorful=False,
            deferred=False,
    ):
        __name__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()
        if parent is not null and not isinstance(parent, Command):
            raise TypeError(f"{__name__} parent must be any-command")
        elif getattr(parent, "operands", {}):
            raise TypeError(f"{__name__} parent cannot have any operands")

        metadata = {
            "name": name,
            "descr": descr if descr is not null else ((inspect.getdoc(source) or null) if callable(source) else null),
            "styles": null.nullify(styles, {}),
            "groups": defaultdict(tuple),
            "conflicts": conflicts,
            "operands": {},
            "qualifiers": (qualifiers := {}),
            "parent": null.nullify(parent),
            "children": (children := {}),
            "shell": bool(shell),
            "fancy": bool(fancy),
            "colorful": bool(colorful),
            "deferred": bool(deferred),
        }
        callback = _get_callback(cls, source, metadata)
        _process_conflicts(cls, defaultdict(frozenset), metadata)

        _process_strings(cls, metadata)
        _process_styles(cls, metadata)

        self = super().__new__(__type__(cls, metadata))

        if not any(name in qualifiers for name in ("-h", "--help")):
            # TODO: Insert help autogenerator
            pass

        if not any(name in qualifiers for name in ("-v", "--version")):
            # TODO: Insert version autogenerator
            pass

        self._callback = callback
        self._fallback = null

        # Needs a mutable reference for future children attachment
        self._children = children

        if self.parent and self.parent._children.setdefault(self.name, self) is not self:  # NOQA: Owned Attribute
            raise TypeError(f"{__name__} name {self.name!r} is already being used")

        # Runtimes
        self._namespace = null
        self._pos = null

        return self

    def command(self, source=null, /, *args, **kwargs):
        # Return builder with the parent=self predefined
        return command(source, self, *args, **kwargs)

    def include(self, spec, /, inherit=False):
        pass

    def _sanitize_token(self, token):
        match = re.fullmatch(r"(?P<input>--?[^\W\d_][^\W_]*(?:-?[^\W_]+)*)(?:=(?P<param>[^\r\n]*))?", token)

        if not match:
            # TODO: Add error handling
            raise SkipToken

        input, param = match["input"], match["param"]

        if input not in self.qualifiers:
            # TODO: Add error handling
            raise SkipToken

        if isinstance(param, str):
            qualifier = self.qualifiers[input]
            if isinstance(qualifier, Option) and not param:
                # TODO: Add warning handling
                pass  # recommended to add explicit parameters if "=", but do not leave empty
            elif isinstance(qualifier, Switch) and param:
                # TODO: Add error handling
                pass

        return input, param

    def _parse(self, tokens, pos=1):
        assert self._namespace is null and self._pos is null
        self._namespace = {}
        self._pos = pos

        operands = deque(self.operands)
        tried = False
        while tokens:
            token = tokens.popleft()

            # Greedy operands match everything regardless if it is an option or no
            if token.startswith("-") and not (operands and self.operands[operands[0]].nargs is Ellipsis):
                try:
                    input, param = self._sanitize_token(token)
                except SkipToken:
                    continue
            elif self.children and not tried:
                try:
                    return self.children[token]._parse(tokens, pos)  # NOQA: Owned Attribute
                except KeyError:
                    # TODO: Add error handling
                    tried = True
                break
            else:
                try:
                    argument = self.operands[input := operands.popleft()]
                    param = null
                except IndexError:
                    # TODO: Add error handling
                    continue
                tokens.appendleft(token)

        if self._callback is null:
            return self._namespace
        self(*args, **kwargs)  # NOQA: Dynamically Injected

    def __invoke__(self, prompt=null, /):
        if prompt is null:
            tokens = sys.argv[1:]
        elif isinstance(prompt, str):
            tokens = shlex.split(prompt)
        elif isinstance(prompt, Iterable):
            tokens = list(prompt)
            if any(not isinstance(token, str) for token in tokens):
                raise TypeError(f"__invoke__() argument must be a string or an iterable of strings")
        else:
            raise TypeError(f"__invoke__() argument must be a string or an iterable of strings")
        return self._parse(deque(tokens))


@functools.wraps(Command, ["__type_params__"])
def command(source=null, *args, **kwargs):
    # Yeah, I know, even if it looks weird, from iterables builds allow this:
    # command([...]).command([...]).command([...]).command([...]).command([...]).command([...])
    decorating = source is null

    @_update_name("command")
    def decorator(source, /):
        if decorating and not callable(source):  # Enforces `command([...], ...)` instead of `command(...)([...])`
            raise TypeError("@command() must be wrapping a callable")
        return Command(source, *args, **kwargs)

    return decorator(source) if source is not null else decorator


__all__ = (
    "Command",
    "command"
)
