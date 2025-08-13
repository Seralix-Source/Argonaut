"""
argonaut.arguments
~~~~~~~~~~~~~~~~~~

Public API for defining command-line arguments parsed by the command runner.

What this module provides
- Cardinal: positional arguments (that do not trigger subcommand resolution).
- Option: named options (e.g., -o/--opt) that accept values.
- Flag: named switches (e.g., -v/--verbose) that do not accept values.

Defining arguments
- Instantiate Cardinal/Option/Flag directly, or
- Use the decorators: @cardinal(...), @option(...), @flag(...).
  The decorators attach a callback (single-assignment). The callback is invoked
  when the spec is matched during parsing, and you can also call the spec object
  directly; its __call__ signature adapts to `nargs`.

Key semantics
- Values and callback payloads
  - Option accepts values; Flag does not.
  - Cardinal handles positional values.
  - For `nargs`, the callback receives:
    * None: a single value (scalar).
    * "?": an optional single value (scalar). If omitted, the callback is not invoked.
    * "*": a list of zero or more values.
    * "+": a list of one or more values.
    * int (including 1): a list with exactly that many values.
      Note: even when `nargs == 1`, the callback receives a list of length 1.

- Special behaviors (Option and Flag)
  - helper=True: marks a help-like switch (e.g., -h/--help). Implies
    standalone=True and terminator=True; cannot be hidden or deprecated.
  - standalone=True: must be the only user-provided argument for the resolved
    command (mutually exclusive with other args).
  - terminator=True: after parsing (and running any callbacks), short-circuits
    the command run (e.g., for --help or --version).
  - explicit=True (Option only): requires attached values only, such as
    --opt=value or -oVALUE (not --opt value or -o value).

- Names
  - Options/Flags accept one or more names like "-o" and/or "--opt".
    Names are validated, deduplicated, and ordered (short before long).

- Immutability and safety
  - Instances expose read-only attributes. Collections are frozen to prevent
    accidental mutation.

- Callbacks
  - Each spec supports `callback(func)` exactly once (single-assignment). The
    generated `__call__` matches `nargs` and invokes the callback only when
    appropriate (e.g., not for an omitted optional value with "?").

Notes
- An internal “void” sentinel distinguishes “unset” from None. This lets
  optional values be omitted without being mistaken for an explicit None.
- Cardinal with `greedy=True` consumes remaining positionals (and typically also
  consumes options and flags); it must not specify explicit `metavar` or `nargs`.
"""
import functools
import re
import textwrap
from collections.abc import Iterable, Sequence, Mapping, Set
from re import IGNORECASE
from types import MemberDescriptorType, MappingProxyType, MethodType

from rich.text import Text

from .void import void


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
def _build_call(nargs, greedy):
    # Generates the cache to not re-compile the same signature multiple times
    try:
        _cache = _build_call._cache  # NOQA: Owned Attribute
    except AttributeError:
        _cache = _build_call._cache = {}

    # Try to retrieve the cached signature
    try:
        return _cache[nargs, greedy]
    except KeyError:
        pass
    if greedy:
        signature = "(self, *params)"
        arguments = "*params"
        guard = ""
    elif nargs is void:
        signature = "(self)"
        arguments = ""
        guard = ""
    elif nargs == "?":
        signature = "(self, param=void)"
        arguments = "param"
        guard = " or param is void"
    elif nargs == "*":
        signature = "(self, *params)"
        arguments = "*params"
        guard = ""
    elif nargs == "+":
        signature = "(self, param, *params)"
        arguments = "param, *params"
        guard = ""
    elif isinstance(nargs, int):
        signature = f"(self, {', '.join('param' + i for i in map(str, range(nargs)))})"
        arguments = ", ".join("param" + i for i in map(str, range(nargs)))
        guard = ""
    else:
        signature = "(self, param)"
        arguments = "param"
        guard = ""

    exec(textwrap.dedent(f"""
        def function{signature}:
            if self._callback is void{guard}:
                return
            return self._callback({arguments})
    """), globals(), namespace := {})

    return _cache.setdefault((nargs, greedy), namespace["function"])


# Dynamically initiates a new type according to the metadata info
def _argtype(cls, metadata):
    __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()

    def _callback(self, callback):
        if not callable(callback):
            raise TypeError(f"{__typename__} callback must be callable")
        if self._callback is not void:
            raise TypeError(f"{__typename__} callback already set")
        self._callback = callback
        return callback

    def _init_subclass():
        raise TypeError(f"type {__typename__!r} is not an acceptable base type")

    return type(cls)(
        __typename__,
        (cls,) + cls.__bases__,
        {  # Clear members of the class to avoid conflicts with the slots
            name: object for name, object in cls.__dict__.items() if not isinstance(object, MemberDescriptorType)
        } | {  # Inject the readonly attributes
            name: _build_property(name, metadata) for name in metadata.keys()
        } | {  # Inject the callback setter (one-life usable)
            "callback": _update_name(
                lambda self, callback, /: _callback(self, callback), "callback"
            ),
        } | {  # Inject the dynamic call method (signature match with the nargs)
            "__call__": _update_name(
                _build_call(metadata.get("nargs", void), metadata.get("greedy", False)), "__call__"
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
        }
    )


# Used to pluralize the groups
def _pluralize(typename):
    # Map of irregular nouns to their plural forms
    irregulars = {
        # People & beings
        "man": "men",
        "woman": "women",
        "child": "children",
        "person": "people",
        "elf": "elves",

        # Animals
        "mouse": "mice",
        "goose": "geese",
        "ox": "oxen",
        "calf": "calves",

        # Body parts
        "tooth": "teeth",
        "foot": "feet",

        # Common nouns in programming contexts
        "series": "series",
        "datum": "data",
        "analysis": "analyses",
        "diagnosis": "diagnoses",
        "thesis": "theses",
        "crisis": "crises",
        "criterion": "criteria",
        "phenomenon": "phenomena",
        "focus": "foci",
        "nucleus": "nuclei",
        "syllabus": "syllabi",
        "cactus": "cacti",
        "fungus": "fungi",
        "oasis": "oases",

        # Objects / common OOP entities
        "wife": "wives",
        "leaf": "leaves",
        "knife": "knives",
        "life": "lives",
        "loaf": "loaves",
        "self": "selves",
        "shelf": "shelves",
    }

    # Return irregular plural if an exact match exists
    try:
        return irregulars[typename]
    except KeyError:
        pass

    # Handle nouns ending with consonant + 'y' (e.g., lady → ladies)
    if re.search("[^aeiou]y$", typename):
        return typename.removesuffix("y") + "ies"

    # Handle nouns ending with s, x, z, ch, or sh (e.g., bus → buses)
    if re.search("(s|x|z|ch|sh)$", typename):
        return typename + "es"

    # Fallback: regular case, just add 's'
    return typename + "s"


# Sanitize and check all common metadata constraints (inplace)
def _prepare_metadata(cls, metadata):
    __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    descr = metadata["descr"]
    if descr is not void and not isinstance(descr, (str, Text)):
        raise TypeError(f"{__typename__} 'descr' must be a string")
    elif isinstance(descr, (str, Text)) and not descr:
        raise ValueError(f"{__typename__} 'descr' must be a non-empty string")
    metadata["descr"] = void.nullify(descr)

    group = metadata["group"]
    if group is not void and not isinstance(group, (str, Text)):
        raise TypeError(f"{__typename__} 'group' must be a string")
    elif isinstance(group, (str, Text)) and not group:
        raise ValueError(f"{__typename__} 'group' must be a non-empty string")
    metadata["group"] = void.nullify(group, "information" if metadata.get("helper") else _pluralize(__typename__))


# Sanitize and check all named metadata constraints (inplace)
def _prepare_named_metadata(cls, metadata):
    __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    # Sanitize the names ensuring they are non-empty strings starting with a dash or a letter
    if not (names := metadata["names"]):
        raise TypeError(f"{__typename__} cannot be unnamed")
    seen = set()
    for name in names:
        if not isinstance(name, (str, Text)):
            raise TypeError(f"{__typename__} names must be strings")
        elif not name:
            raise ValueError(f"{__typename__} names must be non-empty strings")
        elif not re.fullmatch(r"--?[A-Z](?:-?[A-Z0-9+])*", name := str(name), IGNORECASE):
            raise ValueError(f"{__typename__} name {name!r} bad format")
        elif name in seen:
            raise ValueError(f"{__typename__} name {name!r} is duplicated")
        seen.add(name)
    metadata["names"] = tuple(sorted(
        name for name in names if not str(name).startswith("--")
    ) + sorted(
        name for name in names if str(name).startswith("--")
    ))

    metadata["standalone"] |= metadata["helper"]
    metadata["terminator"] |= metadata["helper"]
    metadata["nowait"] |= metadata["terminator"]

    if metadata["helper"]:
        if metadata["hidden"]:
            raise TypeError(f"helper {__typename__} cannot be hidden")
        if metadata["deprecated"]:
            raise TypeError(f"helper {__typename__} cannot be deprecated")


# Sanitize and check all parametric metadata constraints (inplace)
def _prepare_parametric_metadata(cls, metadata):
    __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()

    # Sanitize the metavar forcing non-empty strings
    metavar = metadata["metavar"]
    if metavar is not void and not isinstance(metavar, (str, Text)):
        raise TypeError(f"{__typename__} 'metavar' must be a string")
    elif isinstance(metavar, (str, Text)) and not metavar:
        raise ValueError(f"{__typename__} 'metavar' must be a non-empty string")
    metadata["metavar"] = void.nullify(metavar)

    # Sanitize the type ensuring callable (trust that users give a `callable[[str], _T]`)
    type = metadata["type"]
    if not callable(type):
        raise TypeError(f"{__typename__} 'type' must be callable")
    metadata["type"] = type

    # Sanitize nargs ensuring one of the allowed strings or integer
    nargs = metadata["nargs"]
    if nargs is not void and not isinstance(nargs, (str, int)):
        raise TypeError(f"{__typename__} 'nargs' must be a string or an integer")
    elif isinstance(nargs, str) and nargs not in ("?", "*", "+"):
        raise ValueError(f"{__typename__} 'nargs' must be one of '?', '*', '+'")
    elif isinstance(nargs, int) and nargs < 1:
        raise ValueError(f"{__typename__} 'nargs' must be a positive integer")
    metadata["nargs"] = void.nullify(nargs)

    # Sanitize choices only if apply
    choices = metadata["choices"]
    if not isinstance(choices, Iterable):
        raise TypeError(f"{__typename__} 'choices' must be iterable")
    elif not isinstance(choices, (range, Set)):
        sanitized = list()
        for choice in choices:
            if choice in sanitized:
                raise ValueError(f"{__typename__} choice {choice!r} is duplicated")
            sanitized.append(choice)
        choices = tuple(sanitized)
    metadata["choices"] = choices


# Positional Arguments
class Cardinal[_T]:
    """
    Positional argument specification.

    Purpose
        - Captures positional values (does not trigger subcommand resolution).
        - Supports optional, repeated, or fixed-arity consumption via `nargs`.

    Key parameters
        - metavar: label shown in help/usage (optional).
        - type: callable converting a single token (default: str).
        - nargs: one of None, "?", "*", "+", or int>=1.
          • None: a single value; callback receives a scalar.
          • "?": optional single value; if omitted, the callback is not invoked.
          • "*": zero or more values; callback receives a list.
          • "+": one or more values; callback receives a list.
          • int (including 1): exactly N values; callback receives a list of length N.
        - choices: allowed values (duplicates rejected unless `range`/Set).
        - default: used by the parser when applicable (internal sentinel `Void` means unset).
        - descr: human-friendly description for help.
        - group: group label in help (defaults to a pluralized typename).
        - greedy: when True, consumes remaining positionals (and typically prevents
          explicit `metavar`/`nargs`).
        - nowait: indicates parsing should not wait for additional tokens (used by the parser).
        - hidden, deprecated: visibility and deprecation hints for help/UX.

    Callbacks
        - Attach with `callback(func)` exactly once (single-assignment).
        - The generated `__call__` adapts to `nargs` and invokes the callback only when appropriate
          (e.g., omitted optional value means no invocation).
    """

    __slots__ = (
        "_callback",
    )

    def __new__(
            cls,
            metavar=void,
            /,
            type=str,
            nargs=void,
            choices=(),
            default=void,
            descr=void,
            group=void,
            *,
            greedy=False,
            nowait=False,
            hidden=False,
            deprecated=False,
    ):
        metadata = {
            "metavar": metavar,
            "type": type,
            "nargs": nargs,
            "default": default,
            "choices": choices,
            "descr": descr,
            "group": group,
            "greedy": bool(greedy),
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        _prepare_metadata(cls, metadata)
        _prepare_parametric_metadata(cls, metadata)
        self = super().__new__(_argtype(cls, metadata))
        self._callback = void

        if metadata["greedy"]:
            __typename__ = re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__).lower()
            if metavar is not void:
                raise TypeError(f"greedy {__typename__} cannot have an explicit 'metavar'")
            if nargs is not void:
                raise TypeError(f"greedy {__typename__} cannot have an explicit 'nargs'")
            metadata["default"] = void.nullify(metadata["default"], [])

        return self

    def __cardinal__(self):
        """Return self to satisfy SupportsCardinal protocol and decorator plumbing."""
        return self


@functools.wraps(Cardinal, ["__type_params__"])   # Hide the real signature
def cardinal(*args, **kwargs):
    """
    Decorator/factory for a Cardinal specification.

    Usage patterns
    - As a decorator:
        @cardinal(metavar="PATH", nargs="+")
        def on_values(values: list[str]) -> None: ...
      The decorator returns the original function, and the created Cardinal can be
      retrieved via decorator.__cardinal__().

    - As a factory:
        c = Cardinal(metavar="PATH", nargs="+")
        c.callback(on_values)

    Returns
    - A decorator that:
      • sets the callback once (single-assignment), and
      • exposes __cardinal__() to retrieve the bound spec instance.
    """
    cardinal = Cardinal(*args, **kwargs)

    @_update_name("cardinal")
    def decorator(callback):
        if not callable(callback):
            raise TypeError("@cardinal must be applied to a callable")
        assert cardinal._callback is void, "illegal set of callback outside the @cardinal logic"  # NOQA: Owned Attribute
        cardinal._callback = callback
        return callback

    # Force the decorator to be a SupportsCardinal
    decorator.__cardinal__ = MethodType(_update_name(lambda self: cardinal, "__cardinal__"), decorator)
    return decorator


# Named Parameterizable Arguments
class Option[_T]:
    """
    Named option specification (e.g., -o/--opt) that accepts values.

    Purpose
        - Captures named values with flexible arity and validation.
        - Supports attached-only value forms with `explicit=True`.

    Key parameters
        - names: one or more names like "-o" and/or "--opt" (validated, deduplicated, and ordered:
          short before long).
        - metavar: label shown in help/usage (optional).
        - type: callable converting a single token (default: str).
        - nargs: one of None, "?", "*", "+", or int>=1.
          • None: a single value; callback receives a scalar.
          • "?": optional single value; if omitted, the callback is not invoked.
          • "*": zero or more values; callback receives a list.
          • "+": one or more values; callback receives a list.
          • int (including 1): exactly N values; callback receives a list of length N.
        - choices: allowed values (duplicates rejected unless `range`/Set).
        - default: used by the parser when applicable (internal sentinel `Void` means unset).
        - descr: human-friendly description for help.
        - group: group label in help (defaults to a pluralized typename).
        - explicit: when True, requires attached values only:
          • Long: `--opt=value` (not `--opt value`).
          • Short: `-oVALUE` or `-o=VALUE` (not `-o VALUE`).
          For repeated values (e.g., `+` or int>1), repeat the option per value.
        - helper: marks a help-like switch (e.g., -h/--help). Implies standalone=True and
          terminator=True; cannot be hidden or deprecated.
        - standalone: must be the only user-provided argument for the resolved command.
        - terminator: after parsing (and running callbacks), short-circuits the command run.
        - nowait: indicates parsing should not wait for additional tokens (used by the parser).
        - hidden, deprecated: visibility and deprecation hints for help/UX.

    Callbacks
        - Attach with `callback(func)` exactly once (single-assignment).
        - The generated `__call__` adapts to `nargs` and invokes the callback only when appropriate.

    Notes
        - Flags do not accept values; use `Flag` for boolean switches.
    """

    __slots__ = (
        "_callback",
    )

    def __new__(
            cls,
            *names,
            metavar=void,
            type=str,
            nargs=void,
            choices=(),
            default=void,
            descr=void,
            group=void,
            explicit=False,
            helper=False,
            standalone=False,
            terminator=False,
            nowait=False,
            hidden=False,
            deprecated=False,
    ):
        metadata = {
            "names": names,
            "metavar": metavar,
            "type": type,
            "nargs": nargs,
            "default": default,
            "choices": choices,
            "descr": descr,
            "group": group,
            "explicit": bool(explicit),
            "helper": bool(helper),
            "standalone": bool(standalone),
            "terminator": bool(terminator),
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        _prepare_metadata(cls, metadata)
        _prepare_named_metadata(cls, metadata)
        _prepare_parametric_metadata(cls, metadata)
        self = super().__new__(_argtype(cls, metadata))
        self._callback = void
        return self

    def __option__(self):
        """Return self to satisfy SupportsOption protocol and decorator plumbing."""
        return self


@functools.wraps(Option, ["__type_params__"])  # Hide the real signature
def option(*args, **kwargs):
    """
    Decorator/factory for an Option specification.

    Usage patterns
    - As a decorator:
        @option("-o", "--opt", metavar="VAL")
        def on_opt(value: str) -> None: ...
      The decorator returns the original function, and the created Option can be
      retrieved via decorator.__option__().

    - As a factory:
        o = Option("-o", "--opt", metavar="VAL")
        o.callback(on_opt)

    Returns
    - A decorator that:
      • sets the callback once (single-assignment), and
      • exposes __option__() to retrieve the bound spec instance.
    """
    option = Option(*args, **kwargs)

    @_update_name("option")
    def decorator(callback):
        if not callable(callback):
            raise TypeError("@option must be applied to a callable")
        assert option._callback is void, "illegal set of callback outside the @option logic"  # NOQA: Owned Attribute
        option._callback = callback
        return callback

    # Force the decorator to be a SupportsOption
    decorator.__option__ = MethodType(_update_name(lambda self: option, "__option__"), decorator)
    return decorator


# Named Non-Parameterizable Arguments
class Flag:
    """
    Named switch specification (e.g., -v/--verbose) that does not accept values.

    Purpose
        - Toggles behavior without consuming a value.
        - Commonly used for verbosity, dry-run, feature toggles, etc.

    Key parameters
        - names: one or more names like "-v" and/or "--verbose" (validated, deduplicated, ordered).
        - descr: human-friendly description for help.
        - group: group label in help (defaults to a pluralized typename).
        - helper: marks a help-like switch (e.g., -h/--help). Implies standalone=True and
          terminator=True; cannot be hidden or deprecated.
        - standalone: must be the only user-provided argument for the resolved command.
        - terminator: after parsing (and running callbacks), short-circuits the command run.
        - nowait: indicates parsing should not wait for additional tokens (used by the parser).
        - hidden, deprecated: visibility and deprecation hints for help/UX.

    Callbacks
        - Attach with `callback(func)` exactly once (single-assignment).
        - The generated `__call__` takes no value parameters and invokes the callback when present.
    """

    __slots__ = (
        "_callback",
    )

    def __new__(
            cls,
            *names,
            descr=void,
            group=void,
            helper=False,
            standalone=False,
            terminator=False,
            nowait=False,
            hidden=False,
            deprecated=False,
    ):
        metadata = {
            "names": names,
            "descr": descr,
            "group": group,
            "helper": bool(helper),
            "standalone": bool(standalone),
            "terminator": bool(terminator),
            "nowait": bool(nowait),
            "hidden": bool(hidden),
            "deprecated": bool(deprecated),
        }
        _prepare_metadata(cls, metadata)
        _prepare_named_metadata(cls, metadata)
        self = super().__new__(_argtype(cls, metadata))
        self._callback = void
        return self

    def __flag__(self):
        """Return self to satisfy SupportsFlag protocol and decorator plumbing."""
        return self


@functools.wraps(Flag, ["__type_params__"])  # Hide the real signature
def flag(*args, **kwargs):
    """
    Decorator/factory for a Flag specification.

    Usage patterns
    - As a decorator:
        @flag("-v", "--verbose")
        def on_verbose() -> None: ...
      The decorator returns the original function, and the created Flag can be
      retrieved via decorator.__flag__().

    - As a factory:
        f = Flag("-v", "--verbose")
        f.callback(on_verbose)

    Returns
    - A decorator that:
      • sets the callback once (single-assignment), and
      • exposes __flag__() to retrieve the bound spec instance.
    """
    flag = Flag(*args, **kwargs)

    @_update_name("flag")
    def decorator(callback):
        if not callable(callback):
            raise TypeError("@flag must be applied to a callable")
        assert flag._callback is void, "illegal set of callback outside the @flag logic"  # NOQA: Owned Attribute
        flag._callback = callback
        return callback

    # Force the decorator to be a SupportsFlag
    decorator.__flag__ = MethodType(_update_name(lambda self: flag, "__flag__"), decorator)
    return decorator


__all__ = (
    "Cardinal",
    "Option",
    "Flag",
    "cardinal",
    "option",
    "flag"
)
