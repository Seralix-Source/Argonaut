# python
"""
Internal sentinel for “missing” values.

This module exposes a single instance: `void`. It is used throughout the codebase to
distinguish “not provided” from an explicit None. It is falsy, pretty-prints as "(void)",
and renders with colors in Rich.

Important
- Although `void` is a typed, exported singleton, it is INTERNAL. Do not use it directly
  in user/application code. Prefer higher-level APIs and let the library handle this sentinel.

Common patterns (for internal use)
- Default parameters:
    def func(x=void): ...
- Distinguish provided vs. not-provided:
    value = void.nullify(maybe_value, default=None)

Notes
- `void` is a cached singleton (per-process).
- Rich rendering uses Text.assemble for a colored "(void)".
"""
from rich.text import Text

# Internal sentinel
# Used as a non-provided singleton and parametric argument default if not provided.
# Don't be afraid to see it in representations; it will be replaced by a default or None
# at the end of parsing or where nullify(...) is applied.
void = type("void-type", (), {
    "__module__": None,
    "__slots__": (),
    "__rich__": lambda self: Text.assemble(("(", "yellow"), ("void", "red"), (")", "yellow")),
    "__repr__": lambda self: "(void)",
    "__bool__": lambda self: False,
    "__doc__": "internal singleton for missing values (do not use directly in user code)",
    # Cache the singleton creation so repeated instantiation returns the same object.
    "__new__": __import__("functools").cache(lambda cls: super(type, cls).__new__(cls)),
    # Return the object as-is unless it is the sentinel; in that case, return `default` (None by default).
    "nullify": lambda self, object, default=None, /: object if object is not self else default
})()


__all__ = ("void",)
