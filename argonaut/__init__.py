__title__ = 'argonaut'
__author__ = 'Eiko Reishin (影皇嶺臣)'
__license__ = 'MIT'
# Placeholder, modified by dynamic-versioning.
__version__ = "0.0.0"

__path__ = __import__("pkgutil").extend_path(__path__, __name__)  # NOQA


from typing import NamedTuple, Literal

from .arguments import *
from .commands import *
from .triggers import *


class VersionInfo(NamedTuple):
    major: int
    minor: int
    micro: int
    releaselevel: Literal["alpha", "beta", "candidate", "final"]
    serial: int


# Placeholder, modified by dynamic-versioning.
version_info = VersionInfo(0, 0, 0, "final", 0)

__all__ = (
    "__title__",
    "__author__",
    "__license__",
    "__version__",
    "version_info"
)

# Load the exposed API of the arguments
__all__ += arguments.__all__  # type: ignore[attr-defined]
# Load the exposed API of the commands
__all__ += commands.__all__  # type: ignore[attr-defined]
# Load the exposed API of the triggers
__all__ += triggers.__all__  # type: ignore[attr-defined]
