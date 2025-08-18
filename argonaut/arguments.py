import builtins
import functools
import inspect
import re
import textwrap
import warnings
from collections.abc import Iterable, Set
from types import MemberDescriptorType, MethodType

from .null import null
from .null import _update_name, _frozen_property  # NOQA: Internal


@functools.cache
def _get_invoker(nargs):
    """
    Internal: compile a __call__ compatible with the given nargs spec.

    Supported specs
    - null        → no parameters
    - "?"         → optional single parameter (defaults to null; no-op when omitted)
    - "+"         → one required plus varargs
    - "*" | "..." → varargs (zero or more)
    - int ≥ 1     → exactly N positional-only parameters

    Returns
    - A function object named __call__ that forwards into self._callback(...)
      when set (self._callback is not null); otherwise no-ops.

    Notes
    - Fixed-arity forms use positional-only parameters for clarity.
    - Results are memoized per `nargs` to avoid repeated compilation.
    """
    # Defaults: one required positional-only parameter
    signature = "(self, param, /)"
    arguments = "param"
    guard = ""

    if nargs is null:
        # No parameters (zero-arity)
        signature = "(self)"
        arguments = ""
        guard = ""
    elif nargs == "?":
        # Optional single parameter; omitted means no callback invocation
        signature = "(self, param=null, /)"
        arguments = "param"
        guard = " or param is null"
    elif nargs == "+":
        # One required plus varargs
        signature = "(self, param, /, *params)"
        arguments = "param, *params"
        guard = ""
    elif nargs in ("*", Ellipsis):
        # Zero or more positional parameters
        signature = "(self, *params)"
        arguments = "*params"
        guard = ""
    elif isinstance(nargs, int):
        # Exactly N positional-only parameters
        names = ", ".join(f"p{i}" for i in range(nargs))
        signature = f"(self, {names}, /)"
        arguments = names
        guard = ""

    exec(textwrap.dedent(f"""
        @_update_name("__call__")
        def __call__{signature}:
            # If no callback is attached, do nothing.
            if self._callback is null{guard}:
                return
            return self._callback({arguments})
    """), globals(), namespace := {})

    return namespace["__call__"]


def __type__(cls, metadata):
    """
    Internal: synthesize a concrete spec type from a base class and a metadata map.

    Purpose
    - We build a lightweight, immutable “view” over the construction-time metadata
      dict and expose each field as a read-only attribute via _frozen_property.
    - We attach a single-assignment callback(...) setter and a __call__ whose
      signature matches the normalized nargs (compiled by _get_invoker).
    - We also provide minimal repr/rich_repr hooks for diagnostics and help.

    Inputs
    - cls: the abstract spec base (Operand/Option/Switch) whose name determines
      the user-facing typename (converted to kebab-case).
    - metadata: dict[str, any] whose keys correspond to the spec’s attributes
      (already validated and normalized by the processing helpers).

    Generated members
    - Read-only properties for all metadata keys (via _frozen_property).
    - callback(self, func): single-assignment; returns func for decorator style.
    - __call__(...): arity-specialized invoker that forwards to self._callback.
    - __repr__/__rich_repr__: concise diagnostics and friendly Rich support.
    - __init_subclass__: forbid subclassing of the synthesized type.

    Notes
    - This factory only wires behavior and freezes the current metadata snapshot.
      Any mutations should have been completed by the processing helpers upstream.
    """
    # User-facing typename: convert CamelCase to kebab-case and lowercase.
    __name__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    @_update_name("callback")
    def callback(self, callback):
        """
        Single-assignment callback setter.

        Contract
        - Must be callable; raises TypeError otherwise.
        - Can only be set once; raises TypeError if already set.
        - Returns the same callable to support decorator usage:

            @option(...)
            def on_opt(...): ...
        """
        if not callable(callback):
            raise TypeError(f"{__name__} callback must be callable")
        if self._callback is not null:
            raise TypeError(f"{__name__} callback cannot be changed after initialization")
        self._callback = callback
        return callback

    @_update_name("__repr__")
    def __repr__(self):
        """
        Debug-friendly repr.

        Shape
        - <typename>(key=value, ...) using the current metadata snapshot.
        - Intentionally compact; rich rendering uses __rich_repr__.
        """
        return f"{__name__}({', '.join('%s=%r' % item for item in metadata.items())})"

    @_update_name("__rich_repr__")
    def __rich_repr__(self):
        """
        Rich-friendly representation.

        Behavior
        - Yield (key, value) pairs so Rich can render a table-like display.
        - Keeps parity with __repr__ while allowing styled output in help.
        """
        yield from metadata.items()

    @_update_name("__init_subclass__")
    def __init_subclass__(cls, **options):
        """
        Forbid subclassing of synthesized spec types.

        Rationale
        - These concrete types are generated and finalized at construction time.
          Allowing subclassing would break immutability guarantees and increase
          surface area without benefit.
        """
        raise TypeError(f"type {__name__!r} is not an acceptable base type")

    # Build the concrete type:
    # - Copy over non-descriptor members from the base to avoid conflicts.
    # - Install frozen properties for each metadata field.
    # - Install the single-assignment callback setter.
    # - Install an arity-specialized __call__ based on metadata.get("nargs", null).
    # - Install minimal repr/rich_repr and subclassing guard.
    return type(cls)(
        __name__,
        (cls, *cls.__bases__),
        {
            # Keep only plain attributes (skip descriptors); avoids overshadowing.
            name: object for name, object in cls.__dict__.items() if not isinstance(object, MemberDescriptorType)
        } | {
            # Freeze and expose metadata fields as read-only properties.
            name: _frozen_property(name, metadata) for name in metadata.keys()
        } | {
            # Single-assignment callback setter.
            "callback": callback
        } | {
            # Arity-sensitive invoker (compiled once per nargs value).
            "__call__": _get_invoker(metadata.get("nargs", null))
        } | {
            # Diagnostics and subclassing guard.
            "__repr__": __repr__,
            "__rich_repr__": __rich_repr__,
            "__init_subclass__": __init_subclass__
        } | {
            "__module__": "<argonaut-dynamic>"
        }
    )


@functools.cache
def _pluralize(name):
    """
    Internal: derive a human‑friendly plural from a normalized spec name.

    Assumptions
    - `name` arrives normalized by the caller (lowercase, hyphen-separated tokens),
      e.g. "operand", "option", "switch", "custom-type".
      This function also tolerates CamelCase inputs defensively.

    Behavior
    - Tokenize the name, pluralize only the last token, and return a
      space-separated, lowercase label suitable for grouping in help output.
    - Pluralization strategy:
      1) Try a small irregulars table (domain-relevant first).
      2) Apply simple suffix rules:
         • consonant + 'y'  → replace 'y' with 'ies'  (policy → policies)
         • (s|x|z|ch|sh)$   → add 'es'               (switch → switches)
         • fallback         → add 's'
    - Results are cached for repeat calls with the same input.

    Notes
    - This is intentionally minimal and fast, intended for sensible defaults.
      Callers can always override the group explicitly at the spec level.
    """
    # Split tokens; tolerate CamelCase and normalize to lowercase once.
    # If the input already uses hyphens, this still works and remains fast.
    tokens = re.sub(r"(?<!^)(?=[A-Z])", r" ", name).lower().split()
    if not tokens:
        return "items"

    head, tail = " ".join(tokens[:-1]), tokens[-1]

    # Irregular plurals (domain-relevant ones first; extend if needed)
    irregulars = {
        "man": "men",
        "woman": "women",
        "person": "people",
        "child": "children",
        "mouse": "mice",
        "goose": "geese",
        "foot": "feet",
        "tooth": "teeth",
        # Domain-relevant
        "operand": "operands",
        "option": "options",
        "switch": "switches",
        "flag": "flags",
        "argument": "arguments",
        "information": "information",  # uncountable
    }

    if tail in irregulars:
        tail = irregulars[tail]
    elif re.search(r"[^aeiou]y$", tail):
        tail = tail[:-1] + "ies"
    elif re.search(r"(s|x|z|ch|sh)$", tail):
        tail = tail + "es"
    else:
        tail = tail + "s"

    return f"{head} {tail}".strip()


def _process_metadata(cls, metadata):
    """
    Internal: normalize and validate non‑parametric spec metadata (group, descr).

    Policy (spec layer)
    - Strings only (no Rich/Text) for predictable, renderer‑agnostic specs.
    - Consistent error taxonomy:
      • TypeError for wrong types.
      • ValueError for invalid values (e.g., empty strings after trim).

    Fields
    - group: str
      • If provided (not the internal null), must be a non‑empty string after trim.
      • If not provided, default is:
          "information" when helper=True,
          otherwise a plural derived from the class name (e.g., "options", "operands").
      • Explicit group is always respected, even when helper=True.
    - descr: str | None
      • Optional short description for help.
      • If provided (not the internal null), must be a non‑empty string after trim.
      • Null is normalized to None.

    Side effects
    - Mutates metadata["group"] and metadata["descr"] in place.
    """
    __name__ = re.sub(r"(?<!^)(?=[A-Z])", r"-", cls.__name__).lower()

    group = metadata["group"]
    # Validate explicit group only when user provided it
    if group is not null and not isinstance(group, str):
        raise TypeError(f"{__name__} 'group' must be a string")
    elif group is not null and not (group := group.strip()):
        raise ValueError(f"{__name__} 'group' must be a non-empty string")
    metadata["group"] = null.nullify(group, "information" if metadata.get("helper") else _pluralize(__name__))

    descr = metadata["descr"]
    # Validate explicit description only when user provided it
    if descr is not null and not isinstance(descr, str):
        raise TypeError(f"{__name__} 'descr' must be a string")
    elif descr is not null and not (descr := descr.strip()):
        raise ValueError(f"{__name__} 'descr' must be a non-empty string")
    metadata["descr"] = null.nullify(descr)


def _process_named_metadata(cls, metadata):
    r"""
    Internal: validate and normalize named spec metadata (option/switch names).

    What this does
    - Ensures at least one name is provided.
    - Validates each name is:
      • a string,
      • non-empty after stripping whitespace,
      • matches the CLI naming pattern (see “Naming pattern”),
      • not duplicated within the provided collection.
    - Normalizes the collection into a set of unique names.

    Naming pattern (Unicode-safe, no underscore)
    - Prefix: must start with '-' (short) or '--' (long).
    - Body: one or more segments of “word” characters without underscore,
      separated by single hyphens:
        ^--?[^\W_\d][^\W_]*(?:-?[^\W_]+)*$
      • [^\W_] is “Unicode word” minus underscore (i.e., letters, digits, marks).
      • First body character cannot be a digit ([^\W_\d]).
      • Hyphens may separate segments; no trailing hyphen and no double hyphen
        inside the body (the only allowed double hyphen is the long-option prefix).
    - Examples (valid):
      • -v, -α, --version, --名-前, --versión, --お試し, --a1-β2
      Examples (invalid):
      • --with_underscore (underscore forbidden)
      • -- (no body), - (no body)
      • --trailing- (trailing hyphen)
      • --double--dash (double hyphen inside body)
      • --1start (body cannot start with a digit)

    Behavior notes
    - Order is not preserved (names are stored as a set).
      This is intentional here and sufficient for validation and lookup semantics.
      If a deterministic presentation order is required elsewhere (e.g., help rendering), order there.
    - Helper wiring:
      • helper=True implies standalone=True and terminator=True.
      • terminator=True implies nowait=True.

    Errors (consistent style)
    - TypeError when the input type is wrong (e.g., names missing, non-string).
    - ValueError when a value is invalid (e.g., empty after strip, bad prefix/body, duplicate).
    """
    __name__ = re.sub(r"(?<!^)(?=[A-Z])", r"-", cls.__name__).lower()

    names = metadata["names"]
    if not names:
        raise TypeError(f"{__name__} must define at least one name")

    unique = set()
    for name in names:
        # Type check
        if not isinstance(name, str):
            raise TypeError(f"{__name__} names must be strings")
        # Trim and ensure not empty
        if not (name := name.strip()):
            raise ValueError(f"{__name__} names must be non-empty strings")
        # Validate against the Unicode-safe, underscore-free pattern
        if not re.fullmatch(r"--?[^\W\d_][^\W_]*(?:-?[^\W_]+)*", name):
            raise ValueError(f"{__name__} names must start with '-' or '--' and use unicode word segments without '_'")
        # No duplicates within the set of aliases
        if name in unique:
            raise ValueError(f"name {name!r} for {__name__} is already defined")
        unique.add(name)

    # Store as a set of unique names (unordered; adequate for identity/lookup)
    metadata["names"] = unique

    # Helper semantics:
    # - helper implies standalone + terminator
    # - terminator implies nowait
    metadata["standalone"] |= metadata["helper"]
    metadata["terminator"] |= metadata["helper"]
    metadata["nowait"] |= metadata["terminator"]

    if metadata["helper"]:
        if metadata["hidden"]:
            raise TypeError(f"helper {__name__} cannot be hidden")
        if metadata["deprecated"]:
            warnings.warn(f"helper {__name__} is not recommended to be deprecated", stacklevel=len(inspect.stack()))


def _process_parametric_metadata(cls, metadata, *, operand=False):
    """
    Normalize and validate parametric fields for argument specs.

    Scope
    - Applies to Operand (operand=True) and to Option/Switch (operand=False).
    - Enforces the “spec layer = plain strings” policy (styling belongs to Command/help).

    Validations
    - metavar: str (non-empty) or null
      • Reject non-strings and empty strings.
      • Null is normalized to None.
    - type: callable
      • Must be callable; payload-shape correctness is enforced at parse time.
    - nargs:
      • For operand=False (Option/Switch): None | "?" | "*" | "+" | int>=1
      • For operand=True  (Operand):       None | "?" | "*" | "+" | int>=1 | Ellipsis | "..."
        - Ellipsis (literal ...) or "..." means “consume the remainder”.
        - Ellipsis is operand-only; using it on Option/Switch raises TypeError.
      • Strings must be in the allowed set above; ints must be >= 1.
      • The parser must honor Ellipsis directly (no translation here).
    - choices: Iterable (not str)
      • range/Set accepted as-is.
      • Other iterables are de-duplicated to a tuple (preserving order).

    Side effects
    - metadata["metavar"] is nullified (null → None).
    - metadata["nargs"] is left as-is, except that "..." is normalized to Ellipsis for operands.
    - metadata["choices"] is normalized per rules above.

    Error style (consistent)
    - TypeError for wrong types.
    - ValueError for invalid values.
    """
    __name__ = re.sub(r"(?<!^)(?=[A-Z])", r"-", cls.__name__).lower()

    # metavar: str | null → None
    metavar = metadata["metavar"]
    if metavar is not null and not isinstance(metavar, str):
        raise TypeError(f"{__name__} 'metavar' must be a string")
    elif metavar is not null and not (metavar := metavar.strip()):
        raise ValueError(f"{__name__} 'metavar' must be a non-empty string")
    metadata["metavar"] = null.nullify(metavar)

    # type: callable
    if not callable(metadata["type"]):
        raise TypeError(f"{__name__} 'type' must be callable")

    # nargs: validate/normalize
    nargs = metadata["nargs"]

    # Accept "..." as a convenience alias; normalize to Ellipsis (operands only)
    if nargs == "...":
        if not operand:
            raise TypeError(f"{__name__} use of nargs='...' is not supported for non-operands")
        nargs = Ellipsis

    # Type gate
    if nargs is not null and not (isinstance(nargs, (str, int)) or (operand and nargs is Ellipsis)):
        raise TypeError(f"{__name__} 'nargs' must be a string, an integer, or ellipsis (operands only)")

    # String arity whitelist
    allowed = ("?", "+", "*") + (("...",) if operand else ())
    if isinstance(nargs, str) and nargs not in allowed:
        raise ValueError(f"{__name__} 'nargs' must be one of {", ".join(allowed)}")

    # Integer arity
    if isinstance(nargs, int) and nargs < 1:
        raise ValueError(f"{__name__} 'nargs' must be a positive integer")

    # Defensive: disallow Ellipsis for non-operands
    if (nargs is Ellipsis) and not operand:
        raise TypeError(f"{__name__} 'nargs' ellipsis is only valid for operands")

    # Persist normalized nargs (Ellipsis remains Ellipsis; parser will honor it)
    metadata["nargs"] = null.nullify(nargs)

    # choices: Iterable (not str); de-duplicate unless range/Set
    choices = metadata["choices"]
    if not isinstance(choices, Iterable):
        raise TypeError(f"{__name__} 'choices' must be iterable")
    if isinstance(choices, str):
        raise TypeError(f"{__name__} 'choices' must be an iterable of values, not str")
    if not isinstance(choices, (range, Set)):
        seen = []
        for choice in choices:
            if choice in seen:
                raise ValueError(f"{__name__} 'choices' contains duplicated value: {choice!r}")
            seen.append(choice)
        choices = tuple(seen)
    else:
        choices = frozenset(choices)
    metadata["choices"] = choices

    # default: left as-is (may be any type, including the internal null sentinel)


class Operand[_T]:
    """
    Specification for a positional argument (operand).

    Responsibilities
    - Carry parse-time semantics (type, nargs, default, choices).
    - Provide help metadata (metavar, group, descr).
    - Control parse flow (nowait, hidden, deprecated).
    - Expose a single-assignment callback via the synthesized 'callback' method
      (attached in __type__).

    Notes
    - Styling belongs to the renderer (Command/help); this spec accepts strings only.
    - Ellipsis for nargs is allowed here and means “consume the remainder”.
      See the constructor for the exact rules.
    """
    __slots__ = ("_callback", "_operand_conflict")

    def __new__(
            cls,
            metavar=null,
            /,
            type=str,
            nargs=null,
            default=null,
            choices=(),
            group=null,
            descr=null,
            *,
            nowait=False,
            hidden=False,
            deprecated=False,
    ):
        """
        Build a new Operand spec.

        Parameters (spec layer; strings only)
        - metavar: placeholder label for help (optional).
        - type: callable(token: str) -> Any. Converter applied to each token.
        - nargs: None | "?" | "*" | "+" | int>=1 | Ellipsis (operands only).
          • Ellipsis (literal ...) means “consume the remainder” and must be the
            last positional in the command signature.
        - default: fallback value for optional arities.
        - choices: iterable of allowed values (deduplicated; not str).
        - group: section header for help (default derived; see _process_metadata).
        - descr: short description for help.

        Flags
        - nowait: run the callback as soon as this operand resolves.
        - hidden: omit from help (still parseable).
        - deprecated: mark as deprecated; emit a warning when encountered.

        Behavior
        - Metadata is assembled, normalized, and validated by:
          • _process_metadata (group/descr),
          • _process_parametric_metadata (metavar/type/nargs/choices).
        - The synthesized type (via __type__) exposes read-only properties and
          installs 'callback' and an arity-specialized '__call__'.
        - When nargs is Ellipsis (greedy remainder), an explicit metavar is not
          allowed; the conventional "..." is applied.
        """
        # Assemble raw metadata (booleans normalized to plain bool)
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
        # Normalize non-parametric fields (group/descr).
        _process_metadata(cls, metadata)

        # Normalize parametric fields (metavar/type/nargs/choices); operand=True enables Ellipsis semantics.
        _process_parametric_metadata(cls, metadata, operand=True)

        # Synthesize the concrete, frozen view type and create the instance.
        self = super().__new__(__type__(cls, metadata))

        # Single-assignment callback starts unset (internal sentinel).
        self._callback = null

        # Ellipsis (remainder) forbids explicit metavar; force conventional "..."
        if self.nargs is Ellipsis:
            if metavar is not null:
                raise TypeError(f"greedy {builtins.type(self).__name__} does not allow explicit 'metavar'")
            metadata["metavar"] = "..."

        return self

    def __operand__(self):
        """
        Decorator plumbing helper.

        Returns
        - self (satisfies the SupportsOperand protocol in decorator usage).
        """
        return self


# python
class Option[_T]:
    """
    Specification for a named option (accepts values).

    Responsibilities
    - Carry parse-time semantics (names, type, nargs, default, choices).
    - Provide help metadata (metavar, group, descr).
    - Control parse flow and UX (explicit, helper, standalone, terminator, nowait, hidden, deprecated).
    - Expose a single-assignment callback via the synthesized 'callback' method
      (attached in __type__).

    Notes
    - Spec layer accepts plain strings only for text fields; styling belongs to the
      rendering layer (Command/help).
    - Ellipsis (remainder) is NOT supported for options; use standard nargs only.
    - explicit=True enforces attached value forms (e.g., --name=value, -nVALUE).

    Lifecyle
    - Metadata is assembled here, then normalized/validated by:
      • _process_metadata (group/descr),
      • _process_named_metadata (names & helper wiring),
      • _process_parametric_metadata (metavar/type/nargs/choices).
    - The synthesized type (via __type__) exposes read-only properties and installs
      'callback' and an arity-specialized '__call__'.
    """
    __slots__ = ("_callback", "_option_conflict")

    def __new__(
            cls,
            *names,
            metavar=null,
            type=str,
            nargs=null,
            default=null,
            choices=(),
            group=null,
            descr=null,
            explicit=False,    # require attached value forms only: --opt=value or -oVALUE
            helper=False,      # help-like option (e.g., -h/--help); see helper wiring below
            standalone=False,  # must be the only user-provided arg for the resolved command
            terminator=False,  # short-circuit after callback (e.g., version/help flows)
            nowait=False,      # invoke callback as soon as parsing completes for this option
            hidden=False,      # omit from help; still parseable
            deprecated=False,  # emit a warning when encountered; still parseable
    ):
        """
        Build a new Option spec.

        Parameters
        - names: one or more aliases, each starting with '-' (short) or '--' (long).
          • Duplicates are rejected.
          • Order is not preserved internally for identity/validation; renderers
            may sort or present deterministically later.
        - metavar: label for the value in help (optional).
        - type: callable(token: str) -> Any. Converter applied to each token.
        - nargs: None | "?" | "*" | "+" | int>=1. Standard CLI arity semantics.
          • Ellipsis is not allowed for options.
        - default: fallback value when arity permits omission.
        - choices: iterable of allowed values (deduplicated, not str).
        - group: section header for help (default derived; see _process_metadata).
        - descr: short description for help.

        Flow/UX flags
        - explicit: require attached value forms only (e.g., --opt=value, -oVALUE).
          Spaced forms (e.g., --opt value) are rejected.
        - helper: marks a help-like option (e.g., --help, --version).
          Helper wiring:
            • implies standalone=True and terminator=True,
            • terminator=True implies nowait=True.
          Hidden/deprecated combinations are allowed but discouraged; you can
          choose to forbid them upstream if desired.
        - standalone: enforce that this option appears alone for the resolved command.
        - terminator: after callback, short-circuit command execution.
        - nowait: invoke callback as soon as this option resolves (useful with terminator).
        - hidden: omit from help output but keep parsing behavior.
        - deprecated: mark as deprecated and emit a warning when encountered.
        """
        # Assemble raw metadata (normalize booleans to plain bool)
        metadata = {
            "names": names,
            "metavar": metavar,
            "type": type,
            "nargs": nargs,
            "default": default,
            "choices": choices,
            "group": group,
            "descr": descr,
            "explicit": bool(explicit),
            "helper": bool(helper),
            "standalone": bool(standalone),
            "terminator": bool(terminator),
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        # Normalize non-parametric fields (group/descr).
        _process_metadata(cls, metadata)

        # Validate/normalize names and wire helper→standalone/terminator and terminator→nowait.
        _process_named_metadata(cls, metadata)

        # Normalize parametric fields (metavar/type/nargs/choices); options do not allow Ellipsis.
        _process_parametric_metadata(cls, metadata)

        # Synthesize the concrete, frozen view type and create the instance.
        self = super().__new__(__type__(cls, metadata))

        # Single-assignment callback starts unset (internal sentinel).
        self._callback = null

        return self

    def __option__(self):
        """
        Decorator plumbing helper.

        Returns
        - self (satisfies the SupportsOption protocol in decorator usage).
        """
        return self


class Switch:
    """
    Specification for a named boolean switch (no values).

    Responsibilities
    - Carry presence-only semantics for named toggles (on/off).
    - Provide help metadata (group, descr).
    - Control parse flow and UX (helper, standalone, terminator, nowait, hidden, deprecated).
    - Expose a single-assignment callback via the synthesized 'callback' method
      (attached in __type__).

    Notes
    - Switches never accept values. Presence sets the switch as active.
    - Spec layer accepts plain strings only for text fields; styling belongs to
      the rendering layer (Command/help).
    - Helper switches (e.g., -h/--help, -v/--version) are supported via flags
      and helper wiring in _process_named_metadata.
    """
    __slots__ = ("_callback", "_switch_conflict")

    def __new__(
            cls,
            *names,
            group=null,
            descr=null,
            helper=False,      # help-like switch (e.g., -h/--help, -v/--version)
            standalone=False,  # must be the only user-provided arg for the resolved command
            terminator=False,  # short-circuit after callback (e.g., version/help flows)
            nowait=False,      # invoke callback as soon as the switch resolves
            hidden=False,      # omit from help; still parseable
            deprecated=False,  # emit a warning when encountered; still parseable
    ):
        """
        Build a new Switch spec.

        Parameters
        - names: one or more aliases, each starting with '-' (short) or '--' (long).
          • Duplicates are rejected.
          • Order is not preserved internally for identity/validation; renderers
            may impose a deterministic order later.
        - group: section header for help (default derived; see _process_metadata).
        - descr: short description for help.

        Flow/UX flags
        - helper: marks a help-like switch (e.g., --help, --version).
          Helper wiring (performed in _process_named_metadata):
            • implies standalone=True and terminator=True,
            • terminator=True implies nowait=True.
        - standalone: enforce that this switch appears alone for the resolved command.
        - terminator: after callback, short-circuit command execution.
        - nowait: invoke callback as soon as this switch resolves (useful with terminator).
        - hidden: omit from help output but keep parsing behavior.
        - deprecated: mark as deprecated and emit a warning when encountered.
        """
        # Assemble raw metadata (normalize booleans to plain bool)
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
        # Normalize non-parametric fields (group/descr).
        _process_metadata(cls, metadata)

        # Validate/normalize names and wire helper→standalone/terminator and terminator→nowait.
        _process_named_metadata(cls, metadata)

        # Synthesize the concrete, frozen view type and create the instance.
        self = super().__new__(__type__(cls, metadata))

        # Single-assignment callback starts unset (internal sentinel).
        self._callback = null

        return self

    def __switch__(self):
        """
        Decorator plumbing helper.

        Returns
        - self (satisfies the SupportsSwitch protocol in decorator usage).
        """
        return self


# python
@functools.wraps(Operand, ["__type_params__"])
def operand(*args, **kwargs):
    """
    Decorator/factory for an Operand (positional) specification.

    Usage
    - As a factory:
        op = operand(metavar="FILE", nargs="+", type=str)
        # later: op.callback(handler)

    - As a decorator (single-assignment; returns the spec instance):
        @operand(metavar="FILE", nargs="+", type=str)
        def on_files(values): ...
        # The decorator returns the Operand instance with callback attached.

    Behavior
    - Builds an Operand spec immediately from *args/**kwargs.
    - The returned decorator:
        • sets the callback exactly once (TypeError on a second attempt),
        • returns the Operand instance for fluency (not the original function).
    - The outer function returns the decorator, enabling both factory and
      decorator styles without separate APIs.

    Notes
    - The @functools.wraps(...) call is used to emulate the generic signature
      and improve introspection/help for tooling, without changing runtime
      behavior or the underlying spec’s immutability.
    """
    op = Operand(*args, **kwargs)

    @_update_name("operand")
    def decorator(x, /):
        # Single-assignment callback; returns the spec instance (not the function).
        op.callback(x)
        return op

    # Expose a retrieval hook so users can fetch the spec from the decorator.
    decorator.__operand__ = MethodType(_update_name(lambda self: op, "__operand__"), decorator)
    return decorator


@functools.wraps(Option, ["__type_params__"])
def option(*args, **kwargs):
    """
    Decorator/factory for an Option (named, value-bearing) specification.

    Usage
    - As a factory:
        opt = option("--mode", "-m", metavar="MODE", choices=("fast", "safe"))
        # later: opt.callback(handler)

    - As a decorator (single-assignment; returns the spec instance):
        @option("--output", "-o", metavar="PATH")
        def on_output(path: str): ...
        # The decorator returns the Option instance with callback attached.

    Behavior
    - Builds an Option spec immediately from *args/**kwargs.
    - The returned decorator:
        • sets the callback exactly once (TypeError on a second attempt),
        • returns the Option instance for fluency (not the original function).

    Notes
    - The @functools.wraps(...) call mirrors the generic signature to keep IDEs
      and help output friendly while the spec’s runtime behavior stays simple.
    """
    opt = Option(*args, **kwargs)

    @_update_name("option")
    def decorator(x, /):
        # Single-assignment callback; returns the spec instance (not the function).
        opt.callback(x)
        return opt

    # Expose a retrieval hook so users can fetch the spec from the decorator.
    decorator.__option__ = MethodType(_update_name(lambda self: opt, "__option__"), decorator)
    return decorator


@functools.wraps(Switch, ["__type_params__"])
def switch(*args, **kwargs):
    """
    Decorator/factory for a Switch (named boolean) specification.

    Usage
    - As a factory:
        sw = switch("--verbose", "-v")
        # later: sw.callback(handler)

    - As a decorator (single-assignment; returns the spec instance):
        @switch("--debug")
        def on_debug(): ...
        # The decorator returns the Switch instance with callback attached.

    Behavior
    - Builds a Switch spec immediately from *args/**kwargs.
    - The returned decorator:
        • sets the callback exactly once (TypeError on a second attempt),
        • returns the Switch instance for fluency (not the original function).

    Notes
    - The @functools.wraps(...) call is used to emulate a helpful signature for
      tooling and docs while leaving the underlying object model unchanged.
    """
    sw = Switch(*args, **kwargs)

    @_update_name("switch")
    def decorator(x, /):
        # Single-assignment callback; returns the spec instance (not the function).
        sw.callback(x)
        return sw

    # Expose a retrieval hook so users can fetch the spec from the decorator.
    decorator.__switch__ = MethodType(_update_name(lambda self: sw, "__switch__"), decorator)
    return decorator


__all__ = (
    "Operand",
    "Option",
    "Switch",
    "operand",
    "option",
    "switch",
)
