"""
argonaut
~~~~~~~~

Argonaut is a Python library for building CLI applications.

Status
------
Argonaut was originally built to power Seralix’s first CLI. It is planned to be
deprecated once Seralix reaches version 1.0.0.

Goals
-----
- A friendly, intuitive API you can pick up in minutes.
- Minimal boilerplate for declaring commands and arguments.
- Well-commented internals so curious users can learn and extend confidently.

Authorship & License
--------------------
Developed by Eiko Reishin (影皇嶺臣). See the LICENSE for terms and conditions.

Quick start
-----------
You can define commands using decorators or a compact iterable-based style.
The recommended import is:

    >>> from argonaut import *  # Curated via __all__

Important
---------
Use the `command` function/method either:
- as a decorator (to decorate a function), or
- as a plain function call with an iterable of parameters,
but never both at the same time (you can mix which style you use across different commands).

Decorator-based
^^^^^^^^^^^^^^^
>>> from argonaut import *
>>>
>>> @command
>>> def cli(
...     file: str = Cardinal(),
...     args: list[str] = Cardinal(greedy=True),
...     /,
...     included: list[str] = Option("-i", "--included-dirs", nargs="*"),
...     excluded: list[str] = Option("-e", "--excluded-dirs", nargs="*"),
...     *,
...     debug: bool = Flag("-d", "--debug"),
... ) -> None:
...     # Do something ...
...     pass
>>>
>>> @cli.command
>>> def subcli() -> None:
...     # Tip: attach subcommands to a parent that has no cardinals.
...     pass

Iterable-based
^^^^^^^^^^^^^^
>>> from argonaut import *
>>>
>>> cli = command([
...     Cardinal(),
...     Cardinal(greedy=True),
...     Option("-i", "--included-dirs", nargs="*"),
...     Option("-e", "--excluded-dirs", nargs="*"),
...     Flag("-d", "--debug"),
... ])
>>>
>>> # Subcommand defined with an iterable (no handler):
>>> subcli = cli.command([
...     Option("-n", "--name"),
...     Flag("-q", "--quiet"),
... ])

Parsing-Process
^^^^^^^^^^^^^^^
>>> # Pass a single string (root command only)
>>> invoke(cli, "--name param --state=example")
>>> # Pass a list/iterable (root command only)
>>> import sys; invoke(cli, sys.argv[1:])
>>> # Or parse argv automatically
>>> invoke(cli)
>>>
>>> # Return value:
>>> # - If the resolved command has a handler, that handler is called.
>>> # - If it has no handler (e.g., iterable-defined), `invoke` returns the parsed Namespace.
>>> #   Make sure to capture it:
>>> ns = invoke(cli, "--name param")
>>> # Access your parsed values from `ns` here.

Notes
-----
- `from argonaut import *` is intentional. The package exposes a curated public
  API via `__all__`, so you get exactly what you need—no surprises, no clutter.
- Choose one style (decorator or iterable) per command/subcommand for clarity.
- If at least one subcommand is iterable-defined (no handler), remember to assign
  the result of `invoke(...)` to a variable to use the parsed Namespace.

"""
__title__: str
__author__: str
__license__: str
# Placeholder, modified by dynamic-versioning.
__version__: str

from typing import NamedTuple, Literal, Self

from .arguments import *
from .commands import *
from .triggers import *


class VersionInfo(NamedTuple):
    major: int
    minor: int
    micro: int
    releaselevel: Literal["alpha", "beta", "candidate", "final"]
    serial: int

    def __new__(
            cls: type[Self],
            major: int,
            minor: int,
            micro: int,
            releaselevel: Literal["alpha", "beta", "candidate", "final"],
            serial: int
    ) -> Self: ...


# Placeholder, modified by dynamic-versioning.
version_info: VersionInfo
