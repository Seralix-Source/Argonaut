"""
argument specifications and factories (public API with localized internals).

what this module provides (public)
- specs:
  • Cardinal: positional (supports greedy remainder via Ellipsis).
  • Option: named, value-bearing.
  • Flag: presence-only switch.
- factories/decorators:
  • cardinal(...), option(...), flag(...): build specs and attach callbacks in a
    single step; each decorator also exposes a retrieval hook (__cardinal__/__option__/__flag__)
    to satisfy Supports* protocols in type checkers.

core behaviors
- each spec instance:
  • carries normalized, read-only metadata (exposed via properties).
  • may hold a single-assignment callback (installed by the decorators).
  • renders compact, predictable representations (__repr__/__rich_repr__).

- arity and invocation:
  • __call__ is generated for “factory” classes based on nargs (single, optional,
    variadic '*', variadic '+', fixed-N, or greedy ellipsis for cardinals) and
    forwards to the stored callback when set.

- validation/normalization:
  • _process_metadata: group/descr (strings, non-empty); group defaults via _pluralize.
  • _process_named_metadata: option/flag names (unicode-friendly, hyphen segments, no '_').
  • _process_variadic_metadata: metavar/type/nargs/default/choices, including cardinal
    rules (ellipsis and “no explicit metavar” when greedy).

about “internals” in this file
- this module is public, but contains a few one-domain internal helpers to keep the
  public surface small and predictable (e.g., _call, _pluralize, _process_*).
- StorageGuard and view come from argonaut.internals and are used to implement
  read-only backing storage and safe property exposure; their presence here is an
  implementation detail and not intended for direct use by applications.

stability
- classes and decorator/factory functions in this module are part of the exposed API.
- helpers prefixed with '_' are internal and may change without notice.
"""
import builtins
import functools
import inspect
import re
import textwrap
import warnings
from collections.abc import Iterable, Set
from types import EllipsisType, MethodType

from .internals import *


def _call(options):
    """
    internal: compile a __call__ compatible with the given nargs spec.

    purpose
    - generate a bound method whose signature matches the arity encoded in 'options'.
      this keeps instances callable in a way that mirrors how many values an argument
      accepts (single, optional, variadic, fixed-N, or greedy remainder).

    inputs
    - options: dict-like containing at least:
        • "nargs": one of
            Unset           → zero-arity (used by flags/switches)
            None | "?"      → optional single parameter
            "*" | Ellipsis  → 0..* parameters (variadic)
            "+"             → 1..* parameters (variadic with one required)
            int >= 1        → exactly N positional-only parameters
        • "default": any (only consulted when nargs == "?" to seed __defaults__)

    generated behavior
    - builds a function named __call__ with a signature that matches the selected arity:
        • "/" is used to mark positional-only parameters for clarity.
        • the method fetches the stored callback from the internal backing field
          "-callback"; if unset, it no-ops; otherwise, it forwards the collected
          arguments.

    notes
    - this function does not validate 'options' beyond what pattern matching implies;
      callers are responsible for providing coherent specs.
    - for the optional single-parameter form ("?"), the default value is attached to
      __call__.__defaults__ so omitting the argument calls the callback with the
      provided default.
    """
    # Decide the callable signature and forwarding expression based on 'nargs'
    match nargs := options.get("nargs", Unset):
        case UnsetType():                  # zero-arity (e.g., flags)
            signature = "(self)"
            arguments = ""
        case "?" | None:                   # optional single parameter
            signature = "(self, param, /)"
            arguments = "param"
        case "*" | EllipsisType():         # variadic (0..*)
            signature = "(self, *params)"
            arguments = "*params"
        case "+":                          # variadic with one required (1..*)
            signature = "(self, param, /, *params)"
            arguments = "param, *params"
        case _:
            # fixed-N positional-only parameters (N >= 1)
            signature = "(self, %s, /)" % (arguments := ", ".join("param%d" % index for index in range(nargs)))

    # Compile the __call__ with the computed signature and simple forwarding logic.
    # - Access the backing field "-callback" directly (bypassing property/guards).
    # - If unset (Unset), do nothing; otherwise, invoke with the prepared arguments list.
    exec(textwrap.dedent(f"""
        @rename("__call__")
        def __call__{signature}:
            if (callback := object.__getattribute__(self, "-callback")) is Unset:
                return
            return callback({arguments})
    """), globals(), namespace := locals())

    # Seed a default only for the optional-single-parameter form, so the call
    # can be made as __call__() and the callback receives the provided default.
    if nargs == "?":
        namespace["__call__"].__defaults__ = (options["default"],)

    namespace["__call__"].__doc__ = textwrap.dedent(f"""\
            internal: generated invoker for this spec.

            signature
            - __call__{signature}

            behavior
            - no-op when the stored callback is Unset.
            - otherwise forwards to the stored callback as: callback({arguments or ""})

            arity
            - derived from options['nargs'] = {nargs!r}
              • Unset      → zero-arity (flags/switches)
              • None | "?" → optional single
              • "*"        → zero or more (variadic)
              • "+"        → one or more (variadic)
              • int >= 1   → exactly N positional-only
              • Ellipsis   → greedy remainder
        """)

    return namespace["__call__"]


class _ArgumentType(type):
    """
    internal metaclass that synthesizes the public surface of argument specs.

    responsibilities
    - normalize the public class name to kebab-case (for consistent diagnostics).
    - expose declared metadata fields (__fields__) as read-only properties via view(...).
    - inject friendly representations:
      • __repr__: compact, debug-oriented one-liner
      • __rich_repr__: yields (key, value) pairs for pretty/rich printers
    - optionally install an arity-aware __call__ when building “factory” classes
      (options["factory"] is truthy). the generated invoker is compiled by _call(...)
      and carries its own docstring describing signature and behavior.
    - optionally fence generated classes against subclassing by installing a
      raising __init_subclass__ (when options["factory"] is truthy).

    inputs (via class creation)
    - __fields__: tuple[str, ...] (class attribute in the namespace)
      names of metadata fields to expose as read-only views.
    - **options:
      • factory: bool (default False)
        when true, injects __call__ and forbids subclassing of the generated type.

    notes
    - __module__ is set to Unset when building “limbo” types; callers finalize/attach
      the dynamic class later. consumers should not rely on this value during build.
    - this metaclass does not validate field values; upstream helpers perform
      normalization/validation (e.g., _process_* functions).
    """
    __fields__ = ()

    def __new__(metacls, name, bases, namespace, /, **options):
        # when building a “factory” class, inject an arity-aware __call__ upfront.
        # _call attaches its own docstring describing the generated invoker.
        if options.get("factory", False):
            namespace |= {"__call__": _call(options)}

        # normalize the public class name to kebab-case; keep limbo semantics by
        # setting __module__ to Unset (internal sentinel) until the dynamic class
        # is finalized/attached by the caller.
        cls = super().__new__(
            metacls,
            name := re.sub(r"(?<!^)(?=[A-Z])", r"-", name.strip("_")).lower(),
            bases,
            namespace | {
                "__module__": Unset,  # internal: “limbo” module marker until finalized
                "__qualname__": name  # present the kebab-case name consistently
            } | {
                # expose read-only public attributes for declared fields
                # (each returns an immutable “view” of its backing storage)
                name: view(name) for name in namespace.get("__fields__", ())
            }
        )

        @rename("__repr__")
        def __repr__(self):
            """
            debug-friendly repr.

            shape
            - <typename>(key=value, ...) using the current metadata snapshot.

            notes
            - values are read through the same pairs yielded by __rich_repr__ so
              representation stays consistent between plain and rich output.
            """
            return f"{name}({", ".join("%s=%r" % pair for pair in self.__rich_repr__())})"

        cls.__repr__ = __repr__

        @rename("__rich_repr__")
        def __rich_repr__(self):
            """
            rich-friendly representation.

            behavior
            - yield (key, value) pairs for all declared __fields__ so rich/pretty
              printers can render a compact table-like view.

            notes
            - this function reads backing values directly (bypassing properties) to
              avoid any additional wrapping; public properties already return frozen
              views of container types.
            """
            for field in type(self).__fields__:
                yield field, object.__getattribute__(self, "-" + field)

        cls.__rich_repr__ = __rich_repr__

        if options.get("factory", False):  # dynamic, generated class instance
            @rename("__init_subclass__")
            def __init_subclass__(cls, **options):
                """
                forbid subclassing of generated types.

                rationale
                - factory-built classes are finalized at construction time; allowing
                  subclassing would break immutability guarantees and widen surface
                  area without benefit.
                """
                raise TypeError(f"type {name!r} is not an acceptable base type")

            cls.__init_subclass__ = classmethod(__init_subclass__)

        return cls


@functools.cache
def _pluralize(typename):
    """
    internal: derive a human-friendly plural from a kebab-case typename.

    assumptions
    - typename arrives already normalized in kebab-case (e.g., "option", "cardinal",
      "some-custom-name"). hyphens are replaced with spaces for presentation.

    behavior
    - pluralizes only the last token; rejoins with spaces.
    - applies a small set of irregulars and simple english suffix rules.

    examples
    - "cardinal"         → "cardinals"
    - "option"           → "options"
    - "switch"           → "switches"
    - "policy"           → "policies"
    - "some-custom-name" → "some custom names"
    """
    name = typename.strip().lower()
    if not name:
        return "items"

    tokens = name.split("-")

    head, tail = " ".join(tokens[:-1]), tokens[-1]

    irregulars = {
        "person": "people",
        "man": "men",
        "woman": "women",
        "child": "children",
        "mouse": "mice",
        "goose": "geese",
        "foot": "feet",
        "tooth": "teeth",
        # domain-relevant
        "operand": "operands",
        "option": "options",
        "switch": "switches",
        "flag": "flags",
        "argument": "arguments",
        # uncountable
        "information": "information",
    }

    if tail in irregulars:
        tail = irregulars[tail]
    elif tail.endswith("y") and len(tail) > 1 and tail[-2] not in "aeiou":
        tail = tail[:-1] + "ies"
    elif tail.endswith(("ch", "sh", "s", "x", "z")):
        tail = tail + "es"
    else:
        tail = tail + "s"

    return f"{head} {tail}".strip()


def _process_metadata(cls, group, descr):
    """
    normalize and validate non-parametric metadata (group, descr).

    parameters
    - cls: class used only to prefix error messages.
    - group: str | Unset
      help section name. when provided, must be a non-empty string after trim.
      Unset is a sentinel; note: UnsetType implements the union operator (|)
      so runtime checks like `isinstance(x, str | Unset)` are valid here.
      when group is Unset, a default is derived from the class name (via _pluralize).
    - descr: str | Unset
      short description for help. when provided, must be a non-empty string after trim.
      as with group, Unset participates in unions (str | Unset) for direct isinstance checks.

    returns
    - dict with normalized fields: {group, descr}
    """
    name = cls.__name__

    # group: allow Unset via the union-aware runtime check; enforce str then trim
    if not isinstance(group, str | Unset):
        raise TypeError(f"{name} group must be a string")
    if isinstance(group, str) and not (group := group.strip()):
        raise ValueError(f"{name} group must be a non-empty string")

    # descr: allow Unset via the union-aware runtime check; enforce str then trim
    if not isinstance(descr, str | Unset):
        raise TypeError(f"{name} descr must be a string")
    if isinstance(descr, str) and not (descr := descr.strip()):
        raise ValueError(f"{name} descr must be a non-empty string")

    return dict(
        group=nullify(group, _pluralize(cls.__name__)),
        descr=nullify(descr),
    )


def _process_named_metadata(cls, names):
    r"""
    normalize and validate named argument metadata (option/flag names).

    parameters
    - cls: class used only to prefix error messages.
    - names: Iterable[str]
      one or more command-line names (e.g., "-v", "--verbose"). order is not
      preserved; a deduplicated set is returned.

    validation
    - at least one name is required.
    - each name must be a string and non-empty after strip.
    - syntax must match:
        ^--?[^\W\d_](?:-?[^\W_]+)*$
      which permits:
        • short "-x" (single leading '-'), and
        • long "--name" with hyphen-separated unicode word segments (no '_').
      this allows internationalized names as long as they consist of “word”
      characters (unicode letters/digits/marks) without underscores.
    - duplicate names are rejected.

    returns
    - dict with normalized field: {"names": set[str]}
    """
    typename = cls.__name__
    if not names:
        raise TypeError(f"{typename} requires at least one name")
    unique = set()
    for name in names:
        if not isinstance(name, str):
            raise TypeError(f"all {typename} names must be strings")
        elif not (name := name.strip()):
            raise ValueError(f"{typename} names must be non-empty strings")
        elif not re.fullmatch(r"--?[^\W\d_](?:-?[^\W_]+)*", name):
            raise ValueError(f"{typename} names must be valid command-line argument names")
        elif name in unique:
            raise ValueError(f"name {name!r} for {typename} names is duplicated")
        unique.add(name)

    return dict(names=unique)


def _process_variadic_metadata(cls, metavar, type, nargs, default, choices):
    """
    normalize and validate variadic-style metadata for arguments.

    scope
    - works for both positional (cardinal) and named arguments.
    - the “cardinal rules” (greedy remainder via ellipsis) are enabled when this
      function infers a cardinal context (see “nargs” notes below).

    parameters
    - cls: class
      the argument class; used only to prefix error messages.
    - metavar: str | Unset
      help placeholder. when provided, must be a non-empty string; Unset means
      “not provided”. when greedy remainder is active (ellipsis), explicit
      metavar is not allowed.
    - type: callable
      per-item caster; applied to each token. for variadic arities, applied per item.
    - nargs: str | int | Ellipsis | Unset | type
      arity spec:
        • None/Unset → single value
        • "?"        → optional single value
        • "*"        → zero or more values
        • "+"        → one or more values
        • int>=1     → exactly N values
        • Ellipsis   → greedy remainder (cardinal-only)
        • "..."      → normalized to Ellipsis when in a cardinal context
        • special case: when a type is passed and issubclass(nargs, Cardinal) is true,
          this function treats the context as “cardinal” for the purposes of
          validating/normalizing “...” → Ellipsis and enforcing greedy rules.
    - default: any
      default value used when arity permits omission.
    - choices: Iterable
      allowed values. duplicates are rejected. ranges/sets are compressed to
      frozenset; other iterables are deduplicated in order and frozen to a tuple.

    returns
    - dict with normalized fields: {metavar, type, nargs, default, choices}

    notes
    - Unset is a sentinel distinct from None; normalization to None is performed by nullify.
    - messages are short and lowercased for consistency.
    - cardinal context is inferred in-code (see implementation) rather than passed explicitly.
    """
    cardinal = issubclass(cls, Cardinal)
    typename = cls.__name__

    # metavar
    if not isinstance(metavar, str | Unset):
        raise TypeError(f"{typename} metavar must be a string")
    if isinstance(metavar, str) and not metavar:
        raise ValueError(f"{typename} metavar must be a non-empty string")

    # type
    if not callable(type):
        raise TypeError(f"{typename} type must be callable")

    # nargs
    # allow Unset/None/Ellipsis; allow strings "*", "+", "?", "..." (the latter only when cardinal); allow int>=1
    if not (
            nargs is Unset
            or nargs is None
            or nargs is Ellipsis
            or isinstance(nargs, str)
            or isinstance(nargs, int)
    ):
        if not cardinal:
            raise TypeError(f"{typename} nargs must be a string or an integer")
        raise TypeError(f"{typename} nargs must be a string, an integer, or ellipsis (cardinal only)")

    if isinstance(nargs, str):
        if cardinal and nargs == "...":
            nargs = Ellipsis
        elif nargs not in ("*", "+", "?"):
            if not cardinal:
                raise ValueError(f"{typename} nargs must be '*', '+', or '?'")
            raise ValueError(f"{typename} nargs must be '*', '+', '?', or '...'")

    if isinstance(nargs, int) and nargs < 1:
        raise ValueError(f"{typename} nargs must be a positive integer")

    if (nargs is Ellipsis) and not cardinal:
        raise TypeError(f"{typename} nargs ellipsis is only valid for cardinals")

    # greedy remainder cannot have explicit metavar
    if cardinal and (nargs is Ellipsis) and isinstance(metavar, str):
        raise TypeError(f"greedy {typename} does not allow explicit 'metavar'")

    # choices
    if not isinstance(choices, Iterable):
        raise TypeError(f"{typename} choices must be iterable")
    # freeze and de-duplicate; keep order for general iterables, compress sets/ranges
    if isinstance(choices, (range, Set)):
        choices = frozenset(choices)
    else:
        unique = []  # list to allow unhashable choices while preserving order
        for choice in choices:
            if choice in unique:
                raise ValueError(f"choice {choice!r} for {typename} choices is duplicated")
            unique.append(choice)
        choices = tuple(unique)

    return dict(
        metavar=nullify(metavar),
        type=type,
        nargs=nullify(nargs),
        default=default,
        choices=choices,
    )


class Cardinal[_T](StorageGuard, metaclass=_ArgumentType):
    """
    specification for a positional (cardinal) argument.

    responsibilities
    - carry parse-time semantics (type, nargs, default, choices).
    - provide help metadata (metavar, group, descr).
    - control parse flow (nowait, hidden, deprecated).
    - expose a single-assignment callback via the synthesized mechanism
      (stored in the internal "-callback" backing field).

    notes
    - “cardinal rules” include support for a greedy remainder via Ellipsis:
      when nargs is Ellipsis (greedy), explicit metavar is not allowed.
    - all public fields are read-only “views” backed by internal storage.
    """

    __fields__ = (
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
            deprecated=False,
    ):
        """
        build a new Cardinal spec.

        parameters
        - metavar: str | Unset
          help placeholder; when provided, must be non-empty. forbidden when nargs is Ellipsis (greedy).
        - type: callable
          per-item caster; applied to each token (variadic arities apply per item).
        - nargs: str | int | Ellipsis | Unset
          arity:
            • None/Unset → single value
            • "?"        → optional single value
            • "*"        → 0..* values
            • "+"        → 1..* values
            • int>=1     → exactly N values
            • Ellipsis   → greedy remainder (consume the rest)
        - default: any
          default when arity permits omission.
        - choices: Iterable
          allowed values; duplicates rejected; frozen for safety.
        - group: str | Unset
          help section name; defaults to a plural derived from the typename.
        - descr: str | Unset
          short description for help.

        flags
        - nowait: bool
          invoke callback as soon as this argument resolves.
        - hidden: bool
          omit from help/rendering; still parseable.
        - deprecated: bool
          mark as deprecated; emit a warning when encountered.

        behavior
        - normalizes/validates metadata via helper functions; then constructs a
          fenced dynamic class (factory=True) and writes backing fields during a
          guarded build window. public attributes expose read-only views.
        """
        # normalize boolean flags; then merge with structural metadata
        metadata = {
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        metadata |= _process_metadata(cls, group, descr)
        metadata |= _process_variadic_metadata(cls, metavar, type, nargs, default, choices)  # type: ignore[arg-type]

        # construct a fenced dynamic class and populate backing fields under '-'
        with super().__new__(builtins.type(cls)(cls.__name__, (cls,), {}, factory=True, **metadata)) as self:
            # single-assignment callback backing (unset by default)
            setattr(self, "-callback", Unset)
            # write all declared fields to their backing names
            for field in cls.__fields__:
                setattr(self, "-" + field, metadata[field])
            # greedy remainder forbids explicit metavar (defense-in-depth)
            if self.nargs is Ellipsis and metavar is not Unset:
                raise TypeError(f"greedy {cls.__name__} does not accept metavar")
        return self

    def __cardinal__(self):  # Compatibility with SupportsCardinal
        """
        decorator plumbing helper: return self so @cardinal(...) can expose the spec.
        """
        return self


class Option[_T](StorageGuard, metaclass=_ArgumentType):
    """
    specification for a named, value-bearing option.

    responsibilities
    - carry parse-time semantics (names, type, nargs, default, choices).
    - provide help metadata (metavar, group, descr).
    - control parse flow/ux via flags (inline, helper, standalone, terminator, nowait, hidden, deprecated).
    - expose a single-assignment callback via the synthesized mechanism
      (stored in the internal "-callback" backing field).

    flags (semantics)
    - inline: bool
      require attached/inline values only (e.g., --opt=value or -oVALUE).
      when False, spaced forms (e.g., --opt value) are accepted per nargs rules.
    - helper: bool
      help-like option (e.g., --help, --version).
      wiring: helper → standalone and terminator; and terminator → nowait.
      constraints: helper cannot be hidden; discouraged to be deprecated (warns).
    - standalone: bool
      must be the only user-provided argument for the resolved command.
    - terminator: bool
      short-circuit after callback (e.g., version/help flows).
    - nowait: bool
      invoke callback as soon as this option resolves.
    - hidden: bool
      omit from help/pretty output; still parseable.
    - deprecated: bool
      mark as deprecated; emit a warning when encountered.

    notes
    - all public fields are read-only “views” backed by internal storage.
    - names are validated (short "-x" or long "--name" with unicode word segments).
    """

    __fields__ = (
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
            deprecated=False,
    ):
        """
        build a new Option spec.

        parameters
        - names: Iterable[str]
          one or more command-line names (e.g., "-o", "--output"); validated and deduplicated.
        - metavar: str | Unset
          help placeholder. when provided, must be non-empty.
        - type: callable
          per-item caster; applied to each token (variadic arities apply per item).
        - nargs: str | int | Unset
          arity (same semantics as cardinal, excluding Ellipsis):
            • None/Unset → single value
            • "?"        → optional single
            • "*"        → 0..*
            • "+"        → 1..*
            • int>=1     → exactly N
        - default: any
          default when arity permits omission.
        - choices: Iterable
          allowed values; duplicates rejected; frozen for safety.
        - group: str | Unset
          help section name; defaults to a plural derived from the typename.
        - descr: str | Unset
          short description for help.

        flags
        - inline/helper/standalone/terminator/nowait/hidden/deprecated (see class docstring).

        behavior
        - normalizes/validates metadata via helper functions; wires helper/standalone/terminator/nowait;
          then constructs a fenced dynamic class (factory=True) and writes backing fields during a guarded
          build window. public attributes expose read-only views.
        """
        # normalize boolean flags first
        metadata = {
            "inline": bool(inline),
            "helper": bool(helper),
            "standalone": bool(standalone),
            "terminator": bool(terminator),
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        # helper wiring:
        # - helper implies standalone and terminator
        # - terminator implies nowait
        metadata["standalone"] |= metadata["helper"]
        metadata["terminator"] |= metadata["helper"]
        metadata["nowait"] |= metadata["terminator"]

        # structural metadata (group/descr, names, nargs/type/default/choices)
        metadata |= _process_metadata(cls, group, descr)
        metadata |= _process_named_metadata(cls, names)
        metadata |= _process_variadic_metadata(cls, metavar, type, nargs, default, choices)  # type: ignore[arg-type]

        # construct a fenced dynamic class and populate backing fields under '-'
        with super().__new__(builtins.type(cls)(cls.__name__, (cls,), {}, factory=True, **metadata)) as self:
            # single-assignment callback backing (unset by default)
            setattr(self, "-callback", Unset)
            # write all declared fields to their backing names
            for field in cls.__fields__:
                setattr(self, "-" + field, metadata[field])

            # helper constraints (enforced at construction time)
            if self.helper:
                if self.hidden:
                    raise TypeError(f"helper {cls.__name__} cannot be hidden")
                if self.deprecated:
                    # stacklevel anchored to current stack depth for a useful location
                    warnings.warn(f"helper {cls.__name__} is deprecated", stacklevel=len(inspect.stack()))
        return self

    def __option__(self):
        """
        decorator plumbing helper: return self so @option(...) can expose the spec.
        """
        return self


class Flag(StorageGuard, metaclass=_ArgumentType):
    """
    specification for a named boolean flag (presence-only; no values).

    responsibilities
    - carry presence-only semantics (names).
    - provide help metadata (group, descr).
    - control parse flow/ux via flags (helper, standalone, terminator, nowait, hidden, deprecated).
    - expose a single-assignment callback via the synthesized mechanism
      (stored in the internal "-callback" backing field).

    flags (semantics)
    - helper: bool
      help-like flag (e.g., --help, --version).
      wiring: helper → standalone and terminator; and terminator → nowait.
      constraints: helper cannot be hidden; discouraged to be deprecated (warns).
    - standalone: bool
      must be the only user-provided argument for the resolved command.
    - terminator: bool
      short-circuit after callback (e.g., version/help flows).
    - nowait: bool
      invoke callback as soon as this flag resolves.
    - hidden: bool
      omit from help/pretty output; still parseable.
    - deprecated: bool
      mark as deprecated; emit a warning when encountered.

    notes
    - all public fields are read-only “views” backed by internal storage.
    - names are validated (short "-x" or long "--name" with unicode word segments).
    """

    __fields__ = (
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
            deprecated=False,
    ):
        """
        build a new Flag spec.

        parameters
        - names: Iterable[str]
          one or more command-line names (e.g., "-v", "--verbose"); validated and deduplicated.
        - group: str | Unset
          help section name; defaults to a plural derived from the typename.
        - descr: str | Unset
          short description for help.

        flags
        - helper/standalone/terminator/nowait/hidden/deprecated (see class docstring).

        behavior
        - normalizes/validates metadata via helper functions; wires helper/standalone/terminator/nowait;
          then constructs a fenced dynamic class (factory=True) and writes backing fields during a guarded
          build window. public attributes expose read-only views.
        """
        # normalize boolean flags first
        metadata = {
            "helper": bool(helper),
            "standalone": bool(standalone),
            "terminator": bool(terminator),
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        # helper wiring:
        # - helper implies standalone and terminator
        # - terminator implies nowait
        metadata["standalone"] |= metadata["helper"]
        metadata["terminator"] |= metadata["helper"]
        metadata["nowait"] |= metadata["terminator"]

        # structural metadata (group/descr, names)
        metadata |= _process_metadata(cls, group, descr)
        metadata |= _process_named_metadata(cls, names)

        # construct a fenced dynamic class and populate backing fields under '-'
        with super().__new__(builtins.type(cls)(cls.__name__, (cls,), {}, factory=True, **metadata)) as self:
            # single-assignment callback backing (unset by default)
            setattr(self, "-callback", Unset)
            # write all declared fields to their backing names
            for field in cls.__fields__:
                setattr(self, "-" + field, metadata[field])

            # helper constraints (enforced at construction time)
            if self.helper:
                if self.hidden:
                    raise TypeError(f"helper {cls.__name__} cannot be hidden")
                if self.deprecated:
                    warnings.warn(f"helper {cls.__name__} is deprecated", stacklevel=len(inspect.stack()))
        return self

    def __flag__(self):
        """
        decorator plumbing helper: return self so @flag(...) can expose the spec.
        """
        return self


def cardinal(*args, **kwargs):
    """
    decorator/factory for a Cardinal (positional) specification.

    usage
    - factory form:
        spec = cardinal(metavar="FILE", nargs="+", type=str)
        # later: apply the callback with decorator-style fluency
        @spec
        def on_files(*files): ...
    - decorator form:
        @cardinal(metavar="FILE", nargs="+", type=str)
        def on_files(*files): ...
        # returns the Cardinal instance with the callback attached.

    behavior
    - constructs a Cardinal spec immediately from *args/**kwargs.
    - the returned inner decorator enforces a callable and installs it as the
      single-assignment callback by writing to the internal backing field "-callback".
    - attaches a retrieval method __cardinal__ to the decorator so it conforms to
      the SupportsCardinal protocol at type-checking time:
        decorator.__cardinal__() -> Cardinal

    notes
    - returning the spec (not the original function) is intentional; this enables
      fluent composition while keeping a single source of truth for metadata+callback.
    """

    cardinal = Cardinal(*args, **kwargs)

    @rename("cardinal")
    def decorator(callback, /):
        # validate the target; only callables can be used as handlers
        if not callable(callback):
            raise TypeError("@cardinal() must be applied to a callable")
        # (turning it into multiple specs would be ambiguous and error-prone)
        if object.__getattribute__(cardinal, "-callback") is not Unset:
            raise TypeError("@cardinal() factory cannot be used twice")
        # install the handler into the internal backing storage
        object.__setattr__(cardinal, "-callback", callback)
        # return the spec instance (supports fluent usage)
        return cardinal

    # expose a retrieval method to satisfy typing SupportsCardinal:
    # calling decorator.__cardinal__() returns the underlying spec.
    decorator.__cardinal__ = MethodType(rename(lambda self: cardinal, "__cardinal__"), decorator)
    return decorator


def option(*args, **kwargs):
    """
    decorator/factory for an Option (named, value-bearing) specification.

    usage
    - factory form:
        spec = option("--output", "-o", metavar="PATH")
        @spec
        def on_output(path): ...
    - decorator form:
        @option("--mode", "-m", choices=("fast", "safe"))
        def on_mode(value): ...
        # returns the Option instance with the callback attached.

    behavior
    - constructs an Option spec immediately from *args/**kwargs.
    - the returned inner decorator enforces a callable and installs it as the
      single-assignment callback by writing to "-callback".
    - attaches a retrieval method __option__ so the decorator conforms to a
    - SupportsOption protocol at type-checking time:
        decorator.__option__() -> Option
    """
    option = Option(*args, **kwargs)

    @rename("option")
    def decorator(callback, /):
        # validate the target; only callables can be used as handlers
        if not callable(callback):
            raise TypeError("@option() must be applied to a callable")
        # single-assignment guard: prevent reusing the same factory/decorator twice
        # (turning it into multiple specs would be ambiguous and error-prone)
        if object.__getattribute__(option, "-callback") is not Unset:
            raise TypeError("@option() factory cannot be used twice")
        # install the handler into the internal backing storage
        object.__setattr__(option, "-callback", callback)
        # return the spec instance (supports fluent usage)
        return option

    # satisfy typing: allow retrieving the concrete spec from the decorator
    # calling decorator.__option__() returns the underlying spec instance.
    decorator.__option__ = MethodType(rename(lambda self: option, "__option__"), decorator)
    return decorator


def flag(*args, **kwargs):
    """
    decorator/factory for a Flag (presence-only switch) specification.

    usage
    - factory form:
        spec = flag("--verbose", "-v")
        @spec
        def on_verbose(): ...
    - decorator form:
        @flag("--debug")
        def on_debug(): ...
        # returns the Flag instance with the callback attached.

    behavior
    - constructs a Flag spec immediately from *args/**kwargs.
    - the returned inner decorator enforces a callable and installs it as the
      single-assignment callback by writing to "-callback".
    - attaches a retrieval method __flag__ so the decorator conforms to a
      SupportsFlag protocol at type-checking time:
        decorator.__flag__() -> Flag
    """
    flag = Flag(*args, **kwargs)

    @rename("flag")
    def decorator(callback, /):
        # validate the target; only callables can be used as handlers
        if not callable(callback):
            raise TypeError("@flag() must be applied to a callable")
        # single-assignment guard: prevent reusing the same factory/decorator twice
        # (turning it into multiple specs would be ambiguous and error-prone)
        if object.__getattribute__(flag, "-callback") is not Unset:
            raise TypeError("@flag() factory cannot be used twice")
        # install the handler into the internal backing storage
        object.__setattr__(flag, "-callback", callback)
        # return the spec instance (supports fluent usage)
        return flag

    # satisfy typing: allow retrieving the concrete spec from the decorator
    # calling decorator.__flag__() returns the underlying spec instance.
    decorator.__flag__ = MethodType(rename(lambda self: flag, "__flag__"), decorator)
    return decorator


__all__ = (
    "Cardinal",
    "Option",
    "Flag",
    "cardinal",
    "option",
    "flag",
)
