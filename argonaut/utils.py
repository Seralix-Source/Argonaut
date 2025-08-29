"""
Argonaut utilities (internal helpers, carefully exposed)

Scope
- Core building blocks used across the package for consistent semantics and UX.
- Public-but-internal leaning: stable enough for consumers, designed primarily
  to support the higher-level arguments/commands layers.

Overview
- UnsetType / Unset
  • Singleton sentinel to represent “value not provided” without conflating with None.
  • Falsey (bool(Unset) is False), printable as "Unset", and non-subclassable.

- coalesce(value, default=None)
  • Replace Unset with a concrete default, but preserve legitimate falsey values like None/0/""/[].

- rename(callable, name) / @rename("name")
  • Assign stable __name__/__qualname__ to generated wrappers for clean tracebacks and help.

- mirror("attr")
  • Read-only property factory exposing a private backing field (self._attr) with deep, defensive copies
    for containers to discourage accidental mutation of public API state.

- pluralize(text)
  • Best-effort English pluralization for labels/messages; preserves casing and whitespace.

- mglob(pattern)
  • Module globbing support: expands "pkg.**.tools" style patterns into importable module names.
  • Pattern features: '*', '?', character classes [...]/[!...], and '**' for whole-segment wildcards.

Internal helpers
- _immortalize(object): recursively materializes container copies (used by mirror()).
- _resolve_segment/_compile_regex: translate module-glob patterns into efficient regexes.

Usage guidance
- Prefer Unset for API defaults when None is a meaningful user value; materialize with coalesce().
- Use rename() on dynamic callables so help/tracebacks remain readable.
- Use mirror() to expose internal state safely as read-only properties.

Stability and contract
- These utilities are part of the package’s supported surface and are re-exported via __all__.
- Names not in __all__ are internal and may change without notice.

Quick examples
    >>> one = coalesce(Unset, "fallback")  # "fallback"
    >>> two = coalesce(None, "fallback")    # None  (None is preserved)
    >>> @rename("do_work")
    ... def work(): ...
    ...
    >>> class X:
    ...     _items = [1, 2]
    ...     items = mirror("items")
    ... X().items
    [1, 2]
"""
import builtins
import functools
import importlib
import pkgutil
import re
from collections.abc import Sequence, Mapping, Set
from typing import final


@final
class UnsetType:
    """
    Internal sentinel type representing a value that was not provided.

    This is used when None is a legitimate user value, but the API needs a way
    to distinguish “not provided” from “provided as None”. A single instance,
    Unset, is exposed for use as the default in internal parameters.

    Characteristics
    - Boolean-false: bool(Unset) is False, but it is distinct from None and 0.
    - Printable: repr(Unset) -> "Unset" for friendly diagnostics.
    - Non-subclassable: this type is sealed; do not subclass.
    - Singleton per process: UnsetType() always yields the same instance.

    Typical use
    - Use Unset as a default to signal “no user input”.
    - Downstream, call coalesce(value, default) to materialize a concrete value.
    """

    def __or__(self, other, /):
        """
        Support PEP 604 unions in annotations (e.g., str | UnsetType).
        """
        try:
            return other | type(self)
        except TypeError:
            return NotImplemented

    def __ror__(self, other, /):
        """
        Support reversed PEP 604 unions when UnsetType appears on the right.
        """
        try:
            return other | type(self)
        except TypeError:
            return NotImplemented

    @functools.cache
    def __new__(cls):
        """
        Ensure a single instance for this sentinel type.
        """
        return super().__new__(cls)

    def __bool__(self):
        """
        Falsey sentinel: allows simple truthiness checks without equating Unset to None.
        """
        return False

    def __repr__(self):
        """
        Human-friendly representation used in logs and errors.
        """
        return "Unset"

    def __init_subclass__(cls, **options):
        """
        Disallow subclassing to preserve sentinel semantics.
        """
        raise TypeError("type 'UnsetType' is not an acceptable base type")


def coalesce(object, default=None, /):
    """
    Resolve an internal Unset sentinel to a concrete default.

    This returns the given object unless it is the Unset sentinel, in which case
    the provided default is returned. Importantly, falsey values like None, 0, "",
    or [] are preserved as-is—they are not treated as “unset”.

    Intended use
    - Pair with parameters that default to Unset when None is a legitimate user value.
      For example, when a parameter must be a string (metavar) yet the overall
      default is None, use Unset as the function default and coalesce(...) where
      a concrete value is needed.

    Parameters
    - object: any value that may be Unset.
    - default: value to use only if object is Unset (defaults to None).

    Returns
    - object, if object is not Unset.
    - default, if object is Unset.

    Examples
    - coalesce("name", "fallback") -> "name"
    - coalesce(Unset, "fallback")  -> "fallback"
    - coalesce(None, "fallback")   -> None   # None is preserved, not replaced
    """
    return object if object is not Unset else default


def rename(*parameters):
    """
    Set a stable __name__/__qualname__ on a callable, or return a decorator
    that will do so later.

    This internal helper supports two forms:
    - Function form: rename(callable, name) -> callable
      Immediately updates the callable’s __name__ and __qualname__ in place.
    - Decorator form: rename(name) -> (decorator)
      Returns a decorator that assigns the given name to a future callable.

    Scope
    - Exposed but intended for internal API use (keeps generated callables and
      wrappers readable in tracebacks, logs, and help output).

    Parameters
    - callable: Callable
      The target callable to be renamed (function form).
    - name: str
      The desired name to assign.

    Returns
    - Callable (same object) when used as rename(callable, name).
    - Callable[[Callable], Callable] when used as rename(name), i.e., a decorator.

    Notes
    - This utility does not alter behavior beyond metadata; it only updates
      __name__ and __qualname__ for nicer diagnostics.
    - Some built-in or C-implemented callables are not updatable and will
      raise TypeError.

    Examples
    - Function form:
        def f(): ...
        rename(f, "do_work")  # f.__name__ == f.__qualname__ == "do_work"

    - Decorator form:
        @rename("do_work")
        def f(): ...
        # f.__name__ == f.__qualname__ == "do_work"
    """
    match len(parameters):
        case 2:
            # Function form: rename(callable, name)
            callable, name = parameters
            if not builtins.callable(callable):
                raise TypeError("rename() first argument must be callable")
            if not isinstance(name, str):
                raise TypeError("rename() second argument must be a string")
            try:
                # Update both names for consistent introspection across contexts.
                callable.__qualname__ = name
                callable.__name__ = name
            except (AttributeError, TypeError):
                # Some callables (e.g., built-ins) disallow attribute updates.
                raise TypeError("rename() first argument must be a updatable callable") from None
            return callable
        case 1:
            # Decorator form: @rename("new_name")
            name, = parameters
            if not isinstance(name, str):
                raise TypeError("@rename() argument must be a string")

            def wrapper(callable):
                """Decorator wrapper that applies the new name to the target callable."""
                if not builtins.callable(callable):
                    raise TypeError("@rename() must be applied to a callable")
                return rename(callable, name)

            # Give the wrapper a stable identity as well (helps during debugging).
            return rename(wrapper, "rename")
        case _:
            # Wrong arity: guide the caller with an explicit count.
            raise TypeError("rename takes 1 to 2 arguments but %d were given" % len(parameters))


def _immortalize(object):
    """
    Recursively copy container values, processing nested items.

    Behavior
    - Sequence (non-string): returns a new list with each element processed.
    - Mapping: returns a new dict, preserving original keys and applying this
      function to values (via dict(zip(keys, map(...)))).
    - Set: returns a new set with each element processed.
    - Anything else: returned as-is (coalesce used defensively).

    Notes
    - This creates fresh containers; it does not return read-only views.
    - Keys in mappings are preserved exactly; only values are transformed.
    """
    if isinstance(object, Sequence) and not isinstance(object, str):
        return list(map(_immortalize, object))
    elif isinstance(object, Mapping):
        return dict(zip(object.keys(), map(_immortalize, object.values())))
    elif isinstance(object, Set):
        return set(map(_immortalize, object))
    else:
        return coalesce(object)  # Just in case


def mirror(name, /):
    """
    Define a read-only property that mirrors a private backing attribute.

    The generated property reads from an attribute named "_{name}" on the
    instance, and returns a read-only view for container types to discourage
    accidental mutation through the public API.

    Parameters
    - name: str
      The public property name and the suffix of the backing field "_{name}".

    Returns
    - property object bound to a getter that reads self._{name} and wraps the
      result via _immortalize.

    Example
    - Given self._items, declare items = mirror("items") to expose it safely.
    """
    if not isinstance(name, str):
        raise TypeError("mirror() argument must be a string")

    @rename(name)
    def getter(self):
        """
        Property getter that wraps the backing field in an immutable view.
        """
        return _immortalize(getattr(self, "_" + name))

    # Use the built-in property to publish a read-only accessor.
    return property(getter)


@functools.cache
def pluralize(text, /):
    """
    Best-effort English pluralizer for internal messages and labels.

    Accepts a single word or a multi-word phrase. For phrases, only the last
    lexical word is pluralized; preceding text and whitespace are preserved.

    Behavior
    - Returns the input unchanged for uncountable nouns and empty strings.
    - Preserves basic casing of the pluralized word:
      • UPPER → UPPER, Title → Title, otherwise lower/mixed preserved where sensible.
    - Covers common patterns (s/sh/ch/x/z → +es, consonant+y → -ies, f/fe → -ves),
      plus a small set of irregular forms (e.g., person → people).

    Parameters
    - text: str
      Singular word or phrase whose final word should be pluralized.

    Returns
    - str: pluralized form (phrase preserved, last word pluralized).

    Examples
    - pluralize("error")           -> "errors"
    - pluralize("category")        -> "categories"
    - pluralize("leaf")            -> "leaves"
    - pluralize("Person")          -> "People"
    - pluralize("command option")  -> "command options"
    - pluralize("SERIES")          -> "SERIES"   # uncountable
    """
    if not isinstance(text, str):
        raise TypeError("pluralize() argument must be a string")

    # Fast path: empty string — nothing to do.
    if not text:
        return text

    # Find the last lexical word and preserve any trailing whitespace exactly.
    # Example: "command option  " → head="command ", last="option", trail="  "
    match = re.search(r'(\S+)(\s*)$', text)
    if not match:
        # String is all whitespace; preserve as-is.
        return text

    head = text[:match.start(1)]
    last = match.group(1)
    trail = match.group(2)

    # Work in lowercase for rule application; preserve casing at the end.
    original = last
    lower = last.lower()

    # Uncountables where singular == plural
    uncountables = {
        "series", "species", "sheep", "fish", "deer", "moose",
        "aircraft", "rice", "information", "equipment", "money",
        "news",
    }
    if lower in uncountables:
        plural = lower
    else:
        # Selected irregulars (extend as needed)
        irregulars = {
            "person": "people",
            "man": "men",
            "woman": "women",
            "child": "children",
            "tooth": "teeth",
            "foot": "feet",
            "mouse": "mice",
            "goose": "geese",
            "ox": "oxen",
            "louse": "lice",
            # Latin/Greek-ish
            "cactus": "cacti",
            "focus": "foci",
            "nucleus": "nuclei",
            "syllabus": "syllabi",
            "analysis": "analyses",
            "diagnosis": "diagnoses",
            "ellipsis": "ellipses",
            "thesis": "theses",
            "crisis": "crises",
            "phenomenon": "phenomena",
            "criterion": "criteria",
        }
        if lower in irregulars:
            plural = irregulars[lower]
        else:
            # Rule-based fallbacks
            if lower.endswith(("s", "sh", "ch", "x", "z")):
                plural = lower + "es"
            elif lower.endswith("y") and len(lower) > 1 and lower[-2] not in "aeiou":
                plural = lower[:-1] + "ies"
            elif lower.endswith("fe") and len(lower) > 2:
                plural = lower[:-2] + "ves"
            elif lower.endswith("f") and len(lower) > 1:
                plural = lower[:-1] + "ves"
            elif lower.endswith("o"):
                es_o = {"hero", "echo", "potato", "tomato", "veto", "torpedo"}
                plural = lower + ("es" if lower in es_o else "s")
            else:
                plural = lower + "s"

    # Match basic casing style of the original token.
    def _match_casing(src: str, target: str) -> str:
        if src.isupper():
            return target.upper()
        if src.istitle():
            return target[:1].upper() + target[1:]
        if src[:1].isupper():
            return target[:1].upper() + target[1:]
        return target

    plural_cased = _match_casing(original, plural)

    # Reassemble: original head + pluralized last word + original trailing whitespace.
    return head + plural_cased + trail


@functools.cache
def _resolve_segment(segment):
    """
    translate a single pattern segment into a regex snippet (dots are not matched).
    supported in-segment metacharacters:
      *       → zero or more non-dot chars
      ?       → exactly one non-dot char
      [...]   → character class (one non-dot char)
      [!...]  → negated character class
      \\x      → escape x literally
    """
    length = len(segment)
    index = 0
    parts = []
    while index < length:
        char = segment[index]
        next = index + 1
        if char == '\\' and next < length:
            parts.append(re.escape(segment[next]))
            index += 2
            continue
        if char == '*':
            parts.append(r'[^.]*')
        elif char == '?':
            parts.append(r'[^.]')
        elif char == '[':
            start = index + 1
            negated = ''
            if start < length and segment[start] in ('!', '^'):
                negated = '^'
                start += 1

            pivot = start
            while pivot < length and segment[pivot] != ']':
                if segment[pivot] == '\\' and pivot + 1 < length:
                    pivot += 2
                else:
                    pivot += 1

            if pivot >= length:
                parts.append(r'\[')
            else:
                parts.append(f'[{negated}{segment[start:pivot]}]')
                index = pivot
        else:
            parts.append(re.escape(char))
        index += 1
    return ''.join(parts)


@functools.cache
def _compile_regex(pattern):
    """
    compile a full module-glob pattern into a regex.
    - segments are split by '.'
    - '**' is a whole-segment wildcard for zero or more segments
    - other segments are translated by _resolve_segment()
    """
    parts = []
    for segment in pattern.split('.'):
        if segment == '**':
            # zero or more whole segments (including none)
            parts.append(r'(?:\.[A-Za-z_]\w*)*')
        else:
            parts.append(r'\.' + _resolve_segment(segment))
    if parts and parts[0].startswith(r'\.'):
        body = parts[0][2:] + ''.join(parts[1:])
    else:
        body = ''.join(parts)
    return re.compile(body)  # Since are using fullmatch, no need the other things


def mglob(source, /):
    """
    expand a dot-separated module glob into fully-qualified module names.

    patterns
    - segments are separated by '.'
    - inside a segment:
      *  → zero or more non-dot chars
      ?  → exactly one non-dot char
      [...] / [!...] → character class / negated (one non-dot char)
      \\x → escape x
    - segment '**' means zero or more whole segments (may span dots)

    rules
    - must start with at least one concrete segment (no wildcard-only prefix).
    - matches are case-sensitive and returned in sorted order.
    - if no wildcards are present, returns [source] if importable prefix matches.

    examples
    - "pkg.*"          → direct children of pkg
    - "pkg.**.tests"   → any tests subpackage under pkg
    - "tools.[a-z]*"   → tools.alpha, tools.beta, ...
    """
    if not isinstance(source, str):
        raise TypeError("mglob() argument must be a string")
    elif not (source := source.strip()):
        raise ValueError("mglob() argument must be a non-empty string")

    if re.fullmatch(r"(?!\d)\w+(\.(?!\d)\w+)*", source):  # this literally checks no wildcards or any other pattern
        return [source]

    prefixes = []
    for segment in source.split('.'):
        if set(segment) & set('*?[]!\\') or not re.fullmatch(r"(?!\d)\w+", segment):
            break
        prefixes.append(segment)

    if not prefixes:
        raise ValueError("mglob() pattern must start with a concrete package segment")

    try:
        package = importlib.import_module(prefix := ".".join(prefixes))
    except ImportError:
        return []


    matches = set()

    if (pattern := _compile_regex(source)).fullmatch(prefix):
        matches.add(prefix)

    if hasattr(package, "__path__"):
        for metadata in pkgutil.walk_packages(package.__path__, prefix + '.'):
            if pattern.fullmatch(name := metadata.name):
                matches.add(name)

    return sorted(matches)


Unset = UnsetType()
"""
Internal sentinel for “not provided”.

Use Unset as a default when None is a valid, user-meaningful value but you still
need to distinguish “no input” from “explicitly passed None”. This keeps APIs
unambiguous without exposing None as a user-settable option.

Notes
- Singleton: there is only one Unset instance.
- Distinct from None: equality and identity checks must not treat it as None.
- Falsey: bool(Unset) is False, but it is not equivalent to None or 0.
- Typical pattern: value = coalesce(user_value, default) to materialize a fallback
  only when user_value is Unset (None and other falsey values are preserved).
"""


__all__ = (
    # Public API surface for consumers of argonaut.utils.
    # Note: Unset and its type are not intended to be used by external users.

    # Functions
    "coalesce",
    "rename",
    "mirror",
    "pluralize",
    "mglob",

    # Types
    "UnsetType",

    # Constants
    "Unset",
)
