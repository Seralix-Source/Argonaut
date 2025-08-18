"""
Internal null sentinel (implementation detail).

This module defines a process-wide singleton `null` and its type `nulltype`.
It is used internally to distinguish “not provided” from explicit parameters such
as None or empty collections.

Important
- This sentinel is for internal use only. Do not rely on it in application code
  or public APIs. Prefer higher-level helpers that accept optional parameters or
  explicit default markers exposed by the library.
- Semantics:
  • Falsy: bool(null) is False.
  • Stable string form: repr(null) == "null" (and Rich uses a dim style).
  • Identity: nulltype() always returns the same instance per interpreter.

Typical internal usage
- Use `nullify(object, default)` to coerce the sentinel to a real default while
  passing through normal objects unchanged.

Example (internal)
    def do_something(object=null):
        object = null.nullify(object, default="fallback")
        ...
"""
import functools
from collections.abc import Sequence, Mapping, Set
from types import MappingProxyType

from rich.text import Text


class nulltype:
    """
    Internal singleton type representing an “unset” marker.

    Notes
    - This type is final; subclassing is blocked to preserve semantics.
    - Instances are singletons per interpreter process. Calling nulltype()
      repeatedly yields the same object.
    - The instance is falsy and has a stable string/console representation.
    """

    @functools.cache
    def __new__(cls):
        """
        Return the unique instance of nulltype (per process).

        Implementation details
        - functools.cache on __new__ provides a simple, thread-safe memoization
          in CPython for returning a stable object identity.
        """
        return super().__new__(cls)

    def nullify(self, object, default=None, /):
        """
        Replace the sentinel with a concrete default; pass through other objects.

        Parameters
        - object: any
          Value that may be the sentinel instance (self).
        - default: any | None
          Replacement object when `object` is the sentinel. Defaults to None.

        Returns
        - default when `object is self`, otherwise `object` unchanged.

        Rationale
        - This makes it easy to normalize parameters that use the internal
          sentinel to mean “not provided” without conflating that with None.
        """
        # Treat the sentinel as “missing” and substitute the default.
        if object is self:
            return default
        # All other objects, including None, are preserved.
        return object

    def __bool__(self):
        """
        Return False to indicate a “missing/unset” truthiness.
        """
        return False

    def __rich__(self):
        """
        Rich protocol hook: render a dim 'null' token for human-friendly output.
        """
        return Text(repr(self), style="dim")

    def __repr__(self):
        """
        Stable string form used in logs and diagnostics.
        """
        return "null"

    def __init_subclass__(cls, **options):
        """
        Prevent subclassing to preserve the sentinel’s guarantees.
        """
        raise TypeError("type 'nulltype' is not an acceptable base type")


# Module-level singleton (internal). Do not use directly in application code.
null = nulltype()


__all__ = (
    "nulltype",
    "null",
)


def _update_name(x, /, name=null):
    """
    Internal: set a stable __name__/__qualname__ on a callable, or return a curried renamer.

    Purpose
    - When generating callables dynamically (e.g., via exec), Python assigns verbose
      names that leak implementation details.
      This helper centralizes renaming so user-facing traces, reprs, and helps output stay clean and intentional.

    Parameters
    - x: callable | str (positional-only)
      • If a callable is given, its __name__ and __qualname__ are set to `name`
        when provided; otherwise they are left unchanged.
      • If a string is given, it is treated as the desired name, and a new
        callable-returning partial is produced (see Returns).
    - name: str | null (positional-only, default: null)
      • Target name to assign.
      When `null`, existing names are preserved.

    Returns
    - callable: when `x` is a callable, the same callable after renaming (fluent style).
    - functools.partial: when `x` is a string, a curried function that will rename
      a future callable to that string when invoked.

    Notes
    - This function is purely cosmetic; it does not alter behavior beyond metadata.
    - Prefer using it where dynamically synthesized functions would otherwise surface
      internals (e.g., wrappers, invokers, or adapter methods).
    """
    # If we already have a callable, rename in-place (only if a new name is provided)
    if callable(x):
        x.__qualname__ = null.nullify(name, x.__qualname__)
        x.__name__ = null.nullify(name, x.__name__)
        return x
    # If `x` is a string, return a partial that will apply this name later to a callable
    return functools.partial(_update_name, name=name)


def _frozen_property(name, metadata):
    """
    Internal: expose a read-only snapshot of a construction-time metadata object.

    Context
    - During class construction, a mutable `metadata` dict is used as a staging
      area to accumulate attributes (lists, dicts, sets, etc.). Once the class
      is finalized, those objects must be presented immutably to prevent
      accidental mutation via the public API.

    What this does
    - Takes metadata[name], performs a shallow “freeze” (tuple/MappingProxyType/
      frozenset as appropriate), stores the frozen snapshot back into
      metadata[name] for the remainder of construction, and returns a property
      that serves that frozen snapshot thereafter. The returned property does
      not depend on the `metadata` dict at runtime.

    Freezing rules (shallow)
    - Sequence (non-string) → tuple(seq)
    - Mapping → MappingProxyType(dict(mapping))  # read-only snapshot
    - Set → frozenset(setlike)
    - Other types → returned as-is

    Parameters
    - name: str
      Key of the object inside `metadata` to freeze and expose.
    - metadata: dict
      Construction-time metadata bag used as the source of truth during class
      assembly.

    Returns
    - property
      A data descriptor that returns the frozen snapshot of metadata[name].

    Notes
    - Freezing is shallow by design. Pre-freeze nested containers if deeper
      immutability is required.
    - This helper is intended for objects that will not be updated further once
      the class is finalized. If you need to defer freezing until first access
      (because the object may still change late in construction), prefer a
      lazy-freezing variant that resolves from the instance and then caches.
    """
    object = metadata[name]

    # Freeze common container types to prevent external mutation.
    if isinstance(object, Sequence) and not isinstance(object, (str, bytes, bytearray)):
        object = tuple(object)
    elif isinstance(object, Mapping):
        # Snapshot to a read-only view.
        object = MappingProxyType(object)
    elif isinstance(object, Set):
        object = frozenset(object)

    # Persist the frozen snapshot back into the construction metadata (hygiene).
    metadata[name] = object

    # Expose a read-only property bound to the captured snapshot (not to `metadata`).
    return property(_update_name(lambda self: metadata[name], name))