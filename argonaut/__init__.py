__path__ = __import__("pkgutil").extend_path(__path__, __name__)  # NOQA: F-821
__title__ = 'argonaut'
__author__ = 'Eiko Reishin (影皇嶺臣)'
__license__ = 'MIT'
# Placeholder, modified by dynamic-versioning.
__version__ = "0.0.0"

from .arguments import *
from .commands import *
from .faults import *

VersionInfo = __import__("collections").namedtuple("VersionInfo", (
    "major",
    "minor",
    "micro",
    "releaselevel",
    "serial",
    "metadata"
))

# Placeholder, modified by dynamic-versioning.
version_info = VersionInfo(0, 0, 0, "final", 0, "")

__all__ = (
    "__path__",
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
# Load the exposed API of the faults
__all__ += faults.__all__  # type: ignore[attr-defined]
