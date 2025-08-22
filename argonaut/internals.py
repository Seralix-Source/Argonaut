import functools
import importlib
import pkgutil
import re
from collections.abc import Sequence, Mapping, Set
from contextlib import contextmanager
from types import MappingProxyType
from typing import final


@final
class UnsetType:
    """
    internal singleton sentinel representing an "unset" value.

    intent
    - used by the internal API to distinguish "not provided" from a user‑supplied
      value (including None or other falsy values).
    - although this class is importable, it is intended for internal use only.

    behavior
    - truthiness: bool(Unset) is False.
    - identity: Unset is a process‑wide singleton (see __new__).
    - display: repr(Unset) -> "Unset" (human‑friendly).
    - final: subclassing is forbidden to preserve semantics (see __init_subclass__).

    typing helpers
    - union: UnsetType participates in PEP 604 unions via | to make sentinel‑or‑T
      annotations convenient in internal code paths.
    """

    def __or__(self, other, /):
        """
        support UnsetType | T in annotations (internal convenience only).
        """
        try:
            return type(self) | other
        except TypeError:
            return NotImplemented

    def __ror__(self, other, /):
        """
        support T | UnsetType in annotations (internal convenience only).
        """
        try:
            return other | type(self)
        except TypeError:
            return NotImplemented

    @functools.cache
    def __new__(cls):
        """
        return the singleton instance (process‑wide).
        """
        return super().__new__(cls)

    def __bool__(self):
        """
        make the sentinel falsy to ease guard checks.
        """
        return False

    def __repr__(self):
        """
        stable, concise representation in logs and diagnostics.
        """
        return "Unset"

    def __init_subclass__(cls):
        """
        disallow subclassing to keep sentinel semantics stable.
        """
        raise TypeError("type 'UnsetType' is not an acceptable base type")


Unset = UnsetType()
"""
internal singleton instance of UnsetType.

note
- exposed for completeness, but intended for internal API use only.
"""


def nullify(object, default=None, /):
    """
    internal helper: return `default` when `object` is Unset; otherwise return `object`.

    intent
    - normalize sentinel values at the API boundary so downstream code can treat
      parameters uniformly without branching on Unset.

    parameters
    - object: any
      candidate value that may be the Unset sentinel.
    - default: any | None (positional-only)
      replacement value to use when `object is Unset` (if omitted, None is used).

    returns
    - default when object is Unset; otherwise object unchanged.

    notes
    - exposed for convenience, but intended for internal API use only.
    - this function does not deep-copy; it simply passes through the object.
    """
    return default if object is Unset else object


def rename(x, /, name=None):
    """
    set a stable __name__/__qualname__ on a callable, or return a curried renamer.

    scope
    - exposed but intended for internal API use (keeps generated callables and wrappers
      readable in tracebacks, logs, and help output).

    parameters
    - x: callable | str
      • callable → rename in place (when name is provided; no‑op when None).
      • str      → desired name; returns a callable that will rename a future function.
    - name: str | None
      target name to assign; when None, existing names are preserved.

    returns
    - callable (same object) when x is a callable
    - functools.partial when x is a string (curried renamer)

    errors
    - TypeError if a callable is given and the name is not a string.

    notes
    - this utility does not alter behavior beyond metadata; it only updates
      __name__ and __qualname__ for nicer diagnostics.
    """
    if isinstance(x, str):
        return functools.partial(rename, name=x)
    if not isinstance(name, str):
        raise TypeError("callable name must be a string")
    x.__qualname__ = name
    x.__name__ = name
    return x


class StorageGuard:
    """
    internal mixin to protect backing storage and control mutation.

    intent
    - used by the argument specs to store construction-time metadata under
      non-identifier backing names (prefixed with '-') that must not be
      readable or writable after build.

    rules
    - any attribute whose name starts with '-' is considered internal backing and:
      • cannot be read (AttributeError),
      • cannot be written unless during the guarded init/build phase.

    build phase
    - toggled by the private flag '__building' on the instance.
    - this class provides a context-managed __new__ so specs can write backing
      fields safely:
        with super().__new__(...) as self:
            setattr(self, "-field", value)
        # after the 'with' block, backing fields are locked (read-only).
    """
    __slots__ = ("__building",)

    @contextmanager
    def __new__(cls):
        self = super().__new__(cls)
        self.__building = True
        try:
            yield self
        finally:
            self.__building = False

    def __getattribute__(self, name, /):
        if isinstance(name, str) and name.startswith("-"):
            raise AttributeError("internal storage is not accessible")
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value, /):
        if isinstance(name, str) and name.startswith("-"):
            # allow setting backing fields only during init phase
            if not self.__building:
                raise AttributeError("internal storage is read-only")
            return object.__setattr__(self, name, value)
        return object.__setattr__(self, name, value)


def view(name):
    """
    internal: build a read-only view over a backing field.

    storage convention
    - the actual value is stored under a non-identifier backing name prefixed with '-'
      (e.g., '-metavar'). StorageGuard prevents direct access to these names.

    behavior
    - exposes a property that returns an immutable view of the underlying value:
      • Sequence (non-str) → tuple
      • Mapping           → MappingProxyType
      • Set               → frozenset
      • other types       → returned as-is

    usage
    - for spec classes declaring __fields__, wire properties like:
        namespace |= {field: view(field) for field in __fields__}
      so each public attribute returns a safe, read-only representation.
    """

    @rename(name)
    def getter(self):
        value = object.__getattribute__(self, "-" + name)
        if isinstance(value, Sequence) and not isinstance(value, str):
            return tuple(value)
        if isinstance(value, Mapping):
            return MappingProxyType(value)
        if isinstance(value, Set):
            return frozenset(value)
        return value

    return property(getter)


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


__all__ = (
    "UnsetType",
    "Unset",
    "nullify",
    "rename",
    "StorageGuard",
    "view",
    "mglob"
)
