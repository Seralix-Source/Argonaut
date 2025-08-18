__title__ = 'argonaut'
__author__ = 'Eiko Reishin (影皇嶺臣)'
__license__ = 'MIT'
# Placeholder, modified by dynamic-versioning.
__version__ = "0.0.0"

from .argonauter import *
from .arguments import *
from .commands import *
from .faults import *

VersionInfo = __import__("collections").namedtuple("VersionInfo", ["major", "minor", "micro", "releaselevel", "serial"])

# Placeholder, modified by dynamic-versioning.
version_info = VersionInfo(0, 0, 0, "final", 0)

__all__ = (
    "__title__",
    "__author__",
    "__license__",
    "__version__",
    "version_info"
)

# Load the exposed API of the argonauter
__all__ += argonauter.__all__  # type: ignore[attr-defined]
# Load the exposed API of the arguments
__all__ += arguments.__all__  # type: ignore[attr-defined]
# Load the exposed API of the commands
__all__ += commands.__all__  # type: ignore[attr-defined]
# Load the exposed API of the faults
__all__ += faults.__all__  # type: ignore[attr-defined]
