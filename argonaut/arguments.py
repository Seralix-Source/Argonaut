r"""
Argonaut argument specifications and decorators.

Overview
- Specs
  • Cardinal[_T]: positional, value-bearing argument (supports fixed/optional/variadic and greedy arity).
  • Option[_T]: named, value-bearing option with one or more aliases (e.g., -o/--output).
  • Flag: named, presence-only switch (no payload), e.g., -v/--verbose.

- Decorators
  • @cardinal(...): build and bind a Cardinal to a handler function.
  • @option(...): build and bind an Option to a handler function.
  • @flag(...): build and bind a Flag to a handler function.
  Each decorator returns a configured spec whose __call__ forwards to the bound handler.

- Introspection & representation
  • ArgumentType metaclass provides stable __repr__/__rich_repr__ and exposes selected
    fields via read-only properties declared in __introspectable__/__displayable__.
  • Dynamic __call__ signatures are generated to match declared arity for clean help
    and introspection (see _invoker).

Metadata (sanitized on construction)
- Shared (all specs)
  • group: Unset | str (defaults to pluralized typename), non-empty when provided.
  • descr: Unset | str | Text (short help), non-empty when provided.
- Cardinal/Option only (value-bearing)
  • metavar: Unset | str (label in help; forbidden for greedy "...").
  • type: Callable (converter/validator).
  • nargs: Unset | "?" | "+" | "*" | int (>=1) | Ellipsis (greedy, Cardinal only).
  • choices: Iterable (duplicates rejected unless a Set).
- Named (Option/Flag)
  • names: Iterable[str] validated as shell-style identifiers; duplicates rejected.
  • helper/standalone/terminator/nowait wiring is normalized (helper implies standalone+terminator; terminator implies nowait).
- Visibility
  • hidden: bool (suppresses from help).
  • deprecated: bool (marked and styled accordingly).

Validation highlights
- Names must match r"--?[^\W\d_](-?[^\W_]+)*" and be unique within a spec.
- Cardinal must not specify a metavar when nargs is Ellipsis ("...").
- Option cannot combine metavar and choices simultaneously.
- Collections (choices) reject duplicates unless provided as a Set.
- group/descr strings are trimmed; empty strings are rejected.

Dynamic calling
- _invoker(nargs) builds a cached __call__ that:
  • No-ops if _callback is Unset; otherwise forwards arguments unchanged.
  • Presents a clean, introspectable signature that matches the declared arity.
  • Binds defaults for optional-single forms where applicable.

Quick example:
    >>> from argonaut.arguments import cardinal, option, flag
    >>> @cardinal("FILE")
    >>> def on_file(file): ...
    ...
    >>> @option("-t", "--threads", metavar="THD", type=int, nargs="?")
    >>> def on_threads(threads): ...
    ...
    >>> @flag("-v", "--verbose")
    >>> def on_verbose(): ...
    ...

Public API
- Classes: Cardinal, Option, Flag
- Decorators: cardinal, option, flag
"""
import builtins
import functools
import operator
import re
import textwrap
from collections.abc import Iterable, Set
from types import EllipsisType, MethodType

from rich.text import Text

from .utils import *


@functools.cache
def _invoker(nargs, /):
    """
    Build and cache a tailored __call__ method for a given arity pattern.

    This internal factory emits a small trampoline that:
    - No-ops when self._callback is Unset (silently returns None).
    - Otherwise forwards all received arguments to self._callback unchanged.

    The generated signature depends on nargs:
    - "?" or "+" or None: a single positional-only parameter named 'param'
    - int n: exactly n positional-only parameters named 'parameter0'..'parameter{n-1}'
    - "*", "+", or Ellipsis: a variadic tail '*params'

    Notes
    - The default value for the nargs="?" case is managed by the caller/builder,
      not here. This function only shapes the call interface and forwarding logic.
    - The result is cached per nargs to avoid re-emitting identical trampolines.

    Returns
    - A function object suitable to be bound as __call__(self, ...).
    """
    signature = ["self"]
    arguments = []

    # Optional/single argument forms
    if nargs in ("?", "+", None):
        signature.append("param")
        arguments.append("param")

    # Fixed-arity: parameter0, parameter1, ...
    if isinstance(nargs, int):
        signature.extend("parameter" + str(index) for index in range(nargs))
        arguments.extend("parameter" + str(index) for index in range(nargs))

    # Mark preceding args as positional-only if any were added
    if len(signature) > 1 and len(arguments) > 0:
        signature.append("/")  # positional-only marker

    # Variadic tail forms
    if nargs in ("*", "+", Ellipsis):
        signature.append("*params")
        arguments.append("*params")

    # Emit a tiny forwarding trampoline with the computed signature.
    # Using exec here allows us to present a clean, introspectable signature.
    exec(textwrap.dedent(f"""
        @rename("__call__")
        def __call__({", ".join(signature)}):
            # If no callback is provided, do nothing (return None).
            if self._callback is Unset:
                return
            # Forward arguments as-is to the underlying callback.
            return self._callback({", ".join(arguments)})
    """), globals(), namespace := locals())

    # Document the dynamically generated __call__ for better introspection.
    namespace["__call__"].__doc__ = textwrap.dedent(f"""
        Dynamically generated __call__ for nargs={nargs!r}.

        Behavior
        - If self._callback is Unset, returns None (no-op).
        - Otherwise forwards all received arguments to self._callback unchanged.

        Signature shape
        - "?" or "+" or None: one positional-only argument 'param'
        - int n: positional-only 'parameter0'..'parameter{{n-1}}'
        - "*", "+", or Ellipsis: variadic tail '*params'

        Notes
        - The default for the nargs="?" case (when no value is provided) is set
          by the builder outside of this function.
        - This method is internal and intended to be bound on instances that
          provide a _callback attribute.
    """)

    return namespace["__call__"]


class ArgumentType(type):
    """
    Metaclass that turns specs into callable, introspectable descriptors.

    Responsibilities
    - Inject a tailored __call__ when constructing factory-backed spec classes.
      The shape of __call__ depends on 'nargs' and is created via _invoker.
    - Provide stable, readable __repr__/__rich_repr__ implementations for
      diagnostics and help output.
    - Expose selected fields as read-only properties using mirror() for all
      names listed in __introspectable__.
    - Seal factory-backed spec classes against subclassing to keep semantics
      predictable.

    Conventions
    - __typename__ is derived from the class name (camel-case split with hyphens)
      and used in messages and help output.
    - __displayable__ (if set) narrows which properties are shown by __rich_repr__;
      otherwise __introspectable__ is used.
    """
    __introspectable__ = ()
    __displayable__ = Unset

    def __new__(cls, name, bases, namespace, **options):
        """
        Construct a new spec class and wire dynamic behavior if requested.

        Options
        - factory: when True, the resulting class represents a concrete,
          ready-to-use spec that should receive a generated __call__ and be
          sealed against subclassing.
        - nargs: arity pattern forwarded to _invoker to shape __call__.
        - default: default value used to bind __call__ when nargs == "?".

        Returns
        - type: the newly constructed class with introspection and call plumbing.
        """
        # If this is a factory-backed spec, generate a tailored __call__ upfront.
        if options.get("factory", False):
            namespace["__call__"] = _invoker(nargs := options.get("nargs", Unset))
            # For optional-single args, bind the default as the sole parameter default.
            namespace["__call__"].__defaults__ = (options.get("default"),) if nargs == "?" else ()

        # Build the class with:
        # - __typename__ derived from the class name for consistent messaging.
        # - __module__ marked as dynamic to make the origin explicit in tooling.
        # - Read-only properties for all declared __introspectable__ names.
        self = super().__new__(
            cls,
            name,
            bases,
            namespace | {
                "__typename__": re.sub(r"(?<!^)(?=[A-Z])", r"-", name).lower(),
                "__module__": "dynamic-factory::arguments",
            } | {
                name: mirror(name) for name in namespace.get("__introspectable__", ())
            },
            )

        # Provide a compact, stable string representation for diagnostics.
        @rename("__repr__")
        def __repr__(self):
            """
            Return a concise, stable representation with key metadata.

            Example
            - option(names={'-v', '--verbose'}, group='options', ...)
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
            # Factory-backed spec classes are sealed to avoid subclassing surprises.
            @rename("__init_subclass__")
            def __init_subclass__(cls, **options):  # NOQA: F-841
                """
                Disallow subclassing of factory-backed spec classes.
                """
                raise TypeError(f"type {self.__name__!r} is not an acceptable base type")
            self.__init_subclass__ = classmethod(__init_subclass__)

        return self


def _sanitize_metadata(cls, metadata, /):
    """
    Internal: normalize and validate shared argument metadata.

    This helper is used by Cardinal[_T], Option[_T], and Flag to enforce
    consistent semantics for the 'group' and 'descr' fields:
    - group: optional human-readable category name. If omitted (Unset),
      it defaults to the pluralized typename (e.g., "cardinals", "options", "flags").
      If provided, it must be a non-empty string after trimming.
    - descr: optional short description. If omitted (Unset), it becomes None.
      If provided, it must be a non-empty string after trimming.

    Parameters
    - cls: the specification class providing a __typename__ attribute.
    - metadata: dict containing at least the keys 'group' and 'descr'.
      The dict is modified in place with sanitized values.

    Raises
    - TypeError: if 'group' or 'descr' is not a string or Unset.
    - ValueError: if 'group' or 'descr' is a string but empty after trimming.

    Notes
    - This function mutates the provided metadata dict in place.
    - The default for nargs="?" use cases is handled elsewhere by the builder.
    """
    # Validate and normalize the 'group' metadata
    if not isinstance(group := metadata["group"], str | Unset):
        raise TypeError(f"{cls.__typename__} 'group' must be a string")
    elif isinstance(group, str) and not (group := group.strip()):
        # Non-empty after trimming
        raise ValueError(f"{cls.__typename__} 'group' cannot be empty")

    # Default group: pluralized typename (hyphens replaced for nicer output)
    metadata["group"] = coalesce(group, pluralize(cls.__typename__.replace("-", " ")))

    # Validate and normalize the 'descr' metadata
    if not isinstance(descr := metadata["descr"], str | Text | Unset):
        raise TypeError(f"{cls.__typename__} 'descr' must be a string")
    elif isinstance(descr, str) and not (descr := descr.strip()):
        # Non-empty after trimming
        raise ValueError(f"{cls.__typename__} 'descr' cannot be empty")

    # Default description: None when Unset; preserve provided non-empty string
    metadata["descr"] = coalesce(descr)


def _sanitize_named_metadata(cls, metadata, /):
    r"""
    Internal: validate and normalize metadata for named (option-like) specs.

    Scope
    - Applies to value-bearing and presence-only named arguments (e.g., Option, Flag).

    Responsibilities
    - names: required. Each name must be a non-empty string matching a shell-style
      option pattern. Accepted forms include:
        - short: "-x"
        - long with single hyphen: "-long", "-long-name"
        - long with double hyphen: "--long", "--long-name"
      Unicode letters are allowed. Duplicates are rejected. The collection is
      normalized into a set (order is not significant).
    - helper/standalone/terminator/nowait wiring:
        - standalone := standalone or helper
        - terminator := terminator or helper
        - nowait     := nowait or terminator
      This ensures helper options imply standalone+terminator and that terminators
      are executed immediately.

    Parameters
    - cls: the specification class, used for typename in diagnostics.
    - metadata: dict with at least these keys
        'names' (Iterable[str]),
        'helper' (bool),
        'standalone' (bool),
        'terminator' (bool),
        'nowait' (bool).
      The dict is mutated in place with sanitized values.

    Raises
    - TypeError: when names are missing or contain non-string entries.
    - ValueError: when a name is empty after trimming, fails validation, or duplicates appear.

    Notes
    - Name format regex: r"--?[^\W\d_](-?[^\W_]+)*"
      - Optional single or double hyphen prefix.
      - Segments separated by single hyphens (e.g., "-long-name", "--long-name").
      - Segments start with a Unicode letter and may include Unicode letters/digits.
      - Disallows underscores and leading digits to keep CLI style conventional.
    """
    names = set()
    if not metadata["names"]:
        raise TypeError(f"{cls.__typename__} must specify at least one name")

    for name in metadata["names"]:
        if not isinstance(name, str):
            raise TypeError(f"{cls.__typename__} names must be strings")
        elif not (name := name.strip()):
            raise ValueError(f"{cls.__typename__} names cannot be empty-strings")
        elif not re.fullmatch(r"--?[^\W\d_](-?[^\W_]+)*", name):
            raise ValueError(f"{cls.__typename__} names must be valid shell-style option names (unicodes are allowed)")
        elif name in names:
            raise ValueError(f"{cls.__typename__} names cannot contain duplicates")
        names.add(name)

    metadata["names"] = names

    metadata["standalone"] |= metadata["helper"]
    metadata["terminator"] |= metadata["helper"]
    metadata["nowait"] |= metadata["terminator"]


def _sanitize_parametric_metadata(cls, metadata, /):
    """
    Internal: validate and normalize metadata for value-bearing arguments.

    Scope
    - Intended exclusively for Cardinal[_T] and Option[_T], where an argument
      carries a typed value. Flag is not handled here.

    Responsibilities
    - metavar: must be Unset or a non-empty string after trimming. For greedy
      arity (Ellipsis), an explicit metavar is forbidden.
    - type: must be callable (converter/validator). No further contract enforced.
    - nargs: must be Unset | str ("?", "+", "*") | int (>= 1) and, for Cardinal,
      may also be Ellipsis.
      by callers to Ellipsis; this function accepts both where applicable.
    - choices: must be iterable. If not a Set, duplicates are rejected and
      the collection is normalized to a tuple.

    Explicitly not responsible for
    - default: not validated here; it may be any value (including None) and is
      wired externally for nargs="?" cases.
    - group/descr: handled by the generic _sanitize_metadata routine.

    Side effects
    - Mutates the provided metadata dict in place.
    """
    # Validate and normalize 'metavar'
    if not isinstance(metavar := metadata["metavar"], str | Unset):
        raise TypeError(f"{cls.__typename__} 'metavar' must be a string")
    elif isinstance(metavar, str) and not (metavar := metavar.strip()):
        raise ValueError(f"{cls.__typename__} 'metavar' cannot be empty")
    metadata["metavar"] = coalesce(metavar)

    # Validate 'type' (converter). Trust its signature; only require callability.
    if not callable(metadata["type"]):
        raise TypeError(f"{cls.__typename__} 'type' must be callable")

    # Cardinal supports greedy arity (Ellipsis), Option does not.
    cardinal = issubclass(cls, Cardinal)

    # Validate 'nargs' value per kind
    if not isinstance(nargs := metadata["nargs"], str | int | Unset | (EllipsisType if cardinal else Unset)):
        if not cardinal:
            raise TypeError(f"{cls.__typename__} 'nargs' must be a string or an integer")
        raise TypeError(f"{cls.__typename__} 'nargs' must be a string, an integer, or ellipsis")
    if isinstance(nargs, str) and nargs not in ("?", "+", "*"):
        raise ValueError(f"{cls.__typename__} 'nargs' must be one of '?', '+', or '*'")
    if isinstance(nargs, int) and nargs < 1:
        raise ValueError(f"{cls.__typename__} 'nargs' must be a positive integer")
    metadata["nargs"] = coalesce(nargs)

    # Validate and normalize 'choices'
    if not isinstance(choices := metadata["choices"], Iterable):
        raise TypeError(f"{cls.__typename__} 'choices' must be iterable")
    if not isinstance(choices, Set):
        # Enforce no duplicates and stabilize ordering into a tuple.
        sanitized = []
        for choice in choices:
            if choice in sanitized:
                raise ValueError(f"{cls.__typename__} 'choices' cannot contain duplicates")
            sanitized.append(choice)
        choices = tuple(sanitized)
    metadata["choices"] = choices


class Cardinal[_T](metaclass=ArgumentType):
    """
    Positional, value-bearing argument specification.

    Cardinal[_T] declares how a positional value is parsed, converted, validated,
    and rendered in help. It is a lightweight descriptor that becomes a callable
    handler at construction time (its __call__ is generated based on 'nargs').

    Highlights
    - Generic over the payload type _T (converter provided via 'type').
    - Arity: fixed (int >= 1), optional single ("?"), one-or-more ("+"),
      zero-or-more ("*"), and greedy (Ellipsis).
    - Help/UX metadata: metavar, group, descr, hidden, deprecated.
    - Defaults: allowed to be any Python value; not validated here. For
      nargs="?" cases, the default is wired into the generated __call__.

    Properties
    - The names listed in __introspectable__ are exposed as read-only attributes
      on instances, mirroring the sanitized metadata values.
    """

    __introspectable__ = (
        "metavar",
        "type",
        "nargs",
        "default",
        "choices",
        "group",
        "descr",
        "nowait",
        "hidden",
        "deprecated",
    )

    def __new__(
            cls,
            metavar=Unset,
            /,
            type=str,
            nargs=Unset,
            default=None,
            choices=(),
            group=Unset,
            descr=Unset,
            *,
            nowait=False,
            hidden=False,
            deprecated=False
    ):
        """
        Construct a Cardinal spec with the provided metadata.

        Parameters
        - metavar: Unset | str
          Display name for the value in help. Must be non-empty if provided.
          For greedy arity (Ellipsis/"..."), an explicit metavar is forbidden.
        - type: Callable
          Converter/validator applied to each parsed token. Only callability
          is enforced here.
        - nargs: Unset | "?" | "+" | "*" | int | Ellipsis
          Arity of the argument. Integers must be >= 1.
        - default: Any
          Default value to use when arity is optional. This is intentionally
          not validated here; it may be any value, including None.
          Note: For nargs="?" cases, the default binding to __call__ is handled
          by the factory, not in this initializer.
        - choices: Iterable
          Allowed values. If not a Set, duplicates are rejected and the
          sequence is normalized to a tuple for stable display.
        - group: Unset | str
          Category used in help. Defaults to the pluralized typename if Unset.
        - descr: Unset | str
          Short description for help. If Unset, becomes None.
        - nowait: bool
          If True, the handler is invoked immediately after parsing.
        - hidden: bool
          If True, the argument is suppressed from help output.
        - deprecated: bool
          If True, mark as deprecated in help and warn when specified.

        Notes
        - Metadata is sanitized in two passes:
          • _sanitize_metadata handles shared fields like group/descr.
          • _sanitize_parametric_metadata handles value-bearing fields such as
            metavar/type/nargs/choices.
        - The dynamically generated __call__ (via the factory) is responsible
          for forwarding parsed values to the bound callback.
        """

        metadata = {
            "metavar": metavar,
            "type": type,
            "nargs": nargs,
            "default": default,
            "choices": choices,
            "group": group,
            "descr": descr,
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        # Normalize and validate shared + value-bearing metadata.
        _sanitize_metadata(cls, metadata)
        _sanitize_parametric_metadata(cls, metadata)

        # Create a sealed, factory-backed instance with a generated __call__.
        self = super().__new__(builtins.type(cls)(cls.__name__, (cls,), dict(cls.__dict__), factory=True, **metadata))
        self._callback = Unset  # Bound by decorators/api later.

        # Mirror sanitized metadata into private fields; read-only properties expose them.
        for name, object in metadata.items():
            setattr(self, "_" + name, coalesce(object))

        if self.nargs is Ellipsis:
            # Greedy arity consumes all remaining tokens; in help/usage we render this
            # as "..." to signal unbounded input. For clarity, we forbid an explicit
            # user-provided metavar here because it would be misleading alongside "...".
            if self.metavar:
                raise TypeError(f"greedy {cls.__typename__} cannot specify a 'metavar'")
            self._metavar = "..."

        # UI/UX rule: either show a metavar (generic label) or enumerate concrete choices,
        # but not both at the same time. Mixing them leads to confusing help output.
        if self.metavar and self.choices:
            raise TypeError(f"{cls.__typename__} cannot have both 'metavar' and 'choices'")

        return self

    def __cardinal__(self):
        """
        Introspection hook: identify this spec as a Cardinal.
        """
        return self


class Option[_T](metaclass=ArgumentType):
    """
    Named, value-bearing option specification.

    Option[_T] declares how a named option (e.g., -o/--output) is parsed,
    converted, validated, and rendered in help. It is a lightweight descriptor
    that becomes a callable handler at construction time (its __call__ is
    generated based on 'nargs').

    Highlights
    - Generic over the payload type _T (converter provided via 'type').
    - Supports aliases via 'names' (e.g., "-o", "--output", "-output").
    - Arity: fixed (int >= 1), optional single ("?"), one-or-more ("+"),
      zero-or-more ("*"). Greedy (Ellipsis) is not applicable to options.
    - Inline form: when inline is True, enforces --name=value style (no space).
    - Help/UX metadata: metavar, group, descr, hidden, deprecated.
    - Defaults: allowed to be any Python value; not validated here. For
      nargs="?" cases, the default is wired into the generated __call__.
    - Helper and termination semantics:
      • helper implies standalone and terminator
      • terminator implies nowait

    Properties
    - The names listed in __introspectable__ are exposed as read-only attributes
      on instances, mirroring the sanitized metadata values.
    """

    __introspectable__ = (
        "names",
        "metavar",
        "type",
        "nargs",
        "default",
        "choices",
        "group",
        "descr",
        "inline",
        "helper",
        "standalone",
        "terminator",
        "nowait",
        "hidden",
        "deprecated",
    )

    def __new__(
            cls,
            *names,
            metavar=Unset,
            type=str,
            nargs=Unset,
            default=None,
            choices=(),
            group=Unset,
            descr=Unset,
            inline=False,
            helper=False,
            standalone=False,
            terminator=False,
            nowait=False,
            hidden=False,
            deprecated=False
    ):
        """
        Construct an Option spec with the provided metadata.

        Parameters
        - names: one or more str
          Aliases for the option. Accepted forms include:
            "-x", "-long", "-long-name", "--long", and "--long-name".
          Names must be unique and valid shell-style identifiers (Unicode letters allowed).
        - metavar: Unset | str
          Display name for the value in help. Must be non-empty if provided.
        - type: Callable
          Converter/validator applied to each parsed token. Only callability
          is enforced here.
        - nargs: Unset | "?" | "+" | "*" | int
          Arity of the option. Integers must be >= 1.
        - default: Any
          Default value to use when arity is optional. This is intentionally
          not validated here; it may be any value, including None. For
          nargs="?" cases, the default binding to __call__ is handled by
          the factory, not in this initializer.
        - choices: Iterable
          Allowed values. If not a Set, duplicates are rejected and the
          sequence is normalized to a tuple for stable display.
        - group: Unset | str
          Category used in help. Defaults to the pluralized typename if Unset.
        - descr: Unset | str
          Short description for help. If Unset, becomes None.
        - inline: bool
          If True, the option must be specified inline as --name=value (space-separated
          form is disallowed).
        - helper: bool
          Marks a help-like option. Implies standalone=True and terminator=True.
        - standalone: bool
          Option must be specified alone (cannot be combined with others).
        - terminator: bool
          After handling this option, parsing should stop and the program should
          exit or return control immediately.
        - nowait: bool
          Execute the handler immediately after parsing (implied by terminator).
        - hidden: bool
          Suppress from help output.
        - deprecated: bool
          Mark as deprecated in help and warn when specified.

        Notes
        - Metadata is sanitized in three passes:
          • _sanitize_metadata handles shared fields like group/descr.
          • _sanitize_named_metadata validates names and wires helper semantics.
          • _sanitize_parametric_metadata handles value-bearing fields such as
            metavar/type/nargs/choices (without greedy arity for options).
        - The dynamically generated __call__ (via the factory) is responsible
          for forwarding parsed values to the bound callback.
        """
        metadata = {
            "names": names,
            "metavar": metavar,
            "type": type,
            "nargs": nargs,
            "default": default,
            "choices": choices,
            "group": group,
            "descr": descr,
            "inline": bool(inline),
            "helper": bool(helper),
            "standalone": bool(standalone),
            "terminator": bool(terminator),
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        # Normalize and validate shared + named + value-bearing metadata.
        _sanitize_metadata(cls, metadata)
        _sanitize_named_metadata(cls, metadata)
        _sanitize_parametric_metadata(cls, metadata)

        # Create a sealed, factory-backed instance with a generated __call__.
        self = super().__new__(builtins.type(cls)(cls.__name__, (cls,), dict(cls.__dict__), factory=True, **metadata))
        self._callback = Unset  # Bound by decorators/api later.

        # Mirror sanitized metadata into private fields; read-only properties expose them.
        for name, object in metadata.items():
            setattr(self, "_" + name, coalesce(object))

        # Helper options cannot be hidden or deprecated.
        if self.helper:
            if self.hidden:
                raise TypeError(f"helper {cls.__typename__} cannot be hidden")
            if self.deprecated:
                raise TypeError(f"helper {cls.__typename__} cannot be deprecated")

        # UI/UX rule: either show a metavar (generic label) or enumerate concrete choices,
        # but not both at the same time. Mixing them leads to confusing help output.
        if self.metavar and self.choices:
            raise TypeError(f"{cls.__typename__} cannot have both 'metavar' and 'choices'")

        return self

    def __option__(self):
        """
        Introspection hook: identify this spec as an Option.
        """
        return self


class Flag(metaclass=ArgumentType):
    """
    Named, presence-only option specification.

    Flag declares how a switch-like option (e.g., -v/--verbose, --help) is
    presented and handled. Unlike Cardinal/Option, a Flag does not carry a
    payload value—its presence is the signal. A callable handler is generated
    at construction time and invoked when the flag is specified.

    Highlights
    - Supports aliases via 'names' (e.g., "-v", "--verbose", "-verbose").
    - Presence-only: no metavar, type, nargs, or choices.
    - Helper/termination semantics:
      • helper implies standalone and terminator
      • terminator implies nowait
    - Help/UX metadata: group, descr, hidden, deprecated.

    Properties
    - The names listed in __introspectable__ are exposed as read-only attributes
      on instances, mirroring the sanitized metadata values.
    """

    __introspectable__ = (
        "names",
        "group",
        "descr",
        "helper",
        "standalone",
        "terminator",
        "nowait",
        "hidden",
        "deprecated",
    )

    def __new__(
            cls,
            *names,
            group=Unset,
            descr=Unset,
            helper=False,
            standalone=False,
            terminator=False,
            nowait=False,
            hidden=False,
            deprecated=False
    ):
        """
        Construct a Flag spec with the provided metadata.

        Parameters
        - names: one or more str
          Aliases for the flag. Accepted forms include:
            "-v", "-verbose", "--verbose", "--no-color", etc.
          Names must be unique and valid shell-style identifiers (Unicode letters allowed).
        - group: Unset | str
          Category used in help. Defaults to the pluralized typename if Unset.
        - descr: Unset | str
          Short description for help. If Unset, becomes None.
        - helper: bool
          Marks a help-like flag. Implies standalone=True and terminator=True.
        - standalone: bool
          Flag must be specified alone (cannot be combined with others).
        - terminator: bool
          After handling this flag, parsing should stop and the program should
          exit or return control immediately.
        - nowait: bool
          Execute the handler immediately after parsing (implied by terminator).
        - hidden: bool
          Suppress from help output.
        - deprecated: bool
          Mark as deprecated in help and warn when specified.

        Notes
        - Metadata is sanitized in two passes:
          • _sanitize_metadata handles shared fields like group/descr.
          • _sanitize_named_metadata validates names and wires helper semantics.
        - Flags do not accept value-bearing fields (no metavar/type/nargs/choices).
        """
        metadata = {
            "names": names,
            "group": group,
            "descr": descr,
            "helper": bool(helper),
            "standalone": bool(standalone),
            "terminator": bool(terminator),
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        # Normalize and validate shared and named-argument metadata.
        _sanitize_metadata(cls, metadata)
        _sanitize_named_metadata(cls, metadata)

        # Create a sealed, factory-backed instance with a generated __call__.
        self = super().__new__(builtins.type(cls)(cls.__name__, (cls,), dict(cls.__dict__), factory=True, **metadata))
        self._callback = Unset  # Bound later by the @flag(...) decorator.

        # Mirror sanitized metadata into private fields exposed via properties.
        for name, object in metadata.items():
            setattr(self, "_" + name, coalesce(object))

        # Helper flags must be visible and not deprecated to avoid conflicting UX.
        if self.helper:
            if self.hidden:
                raise TypeError(f"helper {cls.__typename__} cannot be hidden")
            if self.deprecated:
                raise TypeError(f"helper {cls.__typename__} cannot be deprecated")
        return self


    def __flag__(self):
        """
        Introspection hook: identify this spec as a Flag.
        """
        return self


def cardinal(*args, **kwargs):
    """
    Decorator/factory for defining a positional argument handler.

    Usage
    - As a decorator with metadata:
        @cardinal("X", type=str, nargs="?", default="DEF")
        def on_x(x): ...
      The decorated function becomes the handler; the decorator returns a
      Cardinal instance whose __call__ forwards to the handler.

    - As a two-step decorator:
        dec = cardinal("FILE")
        @dec
        def handle(file): ...

    Behavior
    - Validates that it decorates a callable and enforces single application.
    - Binds the provided function as the Cardinal's callback.
    - Returns the configured Cardinal instance.

    Parameters
    - *args, **kwargs: forwarded to Cardinal(...) to construct the spec.

    Returns
    - Cardinal: a value-bearing positional argument specification with the
      decorated function bound as its handler.
    """
    cardinal = Cardinal(*args, **kwargs)

    @rename("cardinal")
    def wrapper(callback, /):
        # Ensure proper usage: must decorate a callable.
        if not callable(callback):
            raise TypeError("@cardinal() must be applied to a callable")
        # Prevent reusing the same decorator instance multiple times.
        if cardinal._callback is not Unset:  # NOQA: E-501
            raise TypeError("@cardinal() must be applied only once")
        # Bind the user's function as the handler.
        cardinal._callback = callback
        return cardinal

    # Advertise SupportsCardinal[_T] by attaching an introspection hook.
    wrapper.__cardinal__ = MethodType(rename(lambda self: cardinal, "__cardinal__"), wrapper)
    return wrapper


def option(*args, **kwargs):
    """
    Decorator/factory for defining a named option handler.

    Usage
    - As a decorator with metadata:
        @option("-o", "--output", type=str, nargs="?", default="out.txt")
        def on_output(value): ...
      The decorated function becomes the handler; the decorator returns an
      Option instance whose __call__ forwards to the handler.

    - As a two-step decorator:
        dec = option("-v", "--verbose")
        @dec
        def on_verbose(): ...

    Behavior
    - Validates that it decorates a callable and enforces single application.
    - Binds the provided function as the Option's callback.
    - Returns the configured Option instance.

    Parameters
    - *args, **kwargs: forwarded to Option(...) to construct the spec
      (names, metavar, type, nargs, default, choices, group, descr, inline,
       helper, standalone, terminator, nowait, hidden, deprecated).

    Returns
    - Option: a value-bearing named option specification with the decorated
      function bound as its handler.
    """
    option = Option(*args, **kwargs)

    @rename("option")
    def wrapper(callback, /):
        # Ensure proper usage: must decorate a callable.
        if not callable(callback):
            raise TypeError("@option() must be applied to a callable")
        # Prevent reusing the same decorator instance multiple times.
        if option._callback is not Unset:  # NOQA: E-501
            raise TypeError("@option() must be applied only once")
        # Bind the user's function as the handler.
        option._callback = callback
        return option

    # Advertise SupportsOption[_T] by attaching an introspection hook.
    wrapper.__option__ = MethodType(rename(lambda self: option, "__option__"), wrapper)
    return wrapper


def flag(*args, **kwargs):
    """
    Decorator/factory for defining a presence-only flag handler.

    Usage
    - As a decorator with metadata:
        @flag("-v", "--verbose")
        def on_verbose(): ...
      The decorated function becomes the handler; the decorator returns a
      Flag instance whose __call__ triggers the handler when the flag is present.

    - As a two-step decorator:
        dec = flag("--help", helper=True, terminator=True)
        @dec
        def show_help(): ...

    Behavior
    - Validates that it decorates a callable and enforces single application.
    - Binds the provided function as the Flag's callback.
    - Returns the configured Flag instance.

    Parameters
    - *args, **kwargs: forwarded to Flag(...) to construct the spec
      (names, group, descr, helper, standalone, terminator, nowait, hidden, deprecated).

    Returns
    - Flag: a presence-only named option specification with the decorated
      function bound as its handler.
    """
    flag = Flag(*args, **kwargs)

    @rename("flag")
    def wrapper(callback, /):
        # Ensure proper usage: must decorate a callable.
        if not callable(callback):
            raise TypeError("@flag() must be applied to a callable")
        # Prevent reusing the same decorator instance multiple times.
        if flag._callback is not Unset:  # NOQA: E-501
            raise TypeError("@flag() must be applied only once")
        # Bind the user's function as the handler.
        flag._callback = callback
        return flag

    # Advertise SupportsFlag by attaching an introspection hook.
    wrapper.__flag__ = MethodType(rename(lambda self: flag, "__flag__"), wrapper)
    return wrapper


__all__ = (
    # Public API surface for consumers of argonaut.arguments.
    # These names are re-exported from the package __init__.
    # Keep this list stable: it defines the supported, documented entry points.

    # Classes (specifications)
    "Cardinal",
    "Option",
    "Flag",

    # Decorators (user-facing helpers to bind handlers)
    "cardinal",
    "option",
    "flag",
)

# Remove the internal metaclass from the module namespace to avoid accidental
# exposure in docs, autocompletion, or star-imports. Not part of the public API.
del ArgumentType
