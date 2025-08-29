__path__: list[str]
__title__: str
__author__: str
__license__: str
# Placeholder, modified by dynamic-versioning.
__version__: str

from typing import NamedTuple, Literal, Final, Self

from .arguments import *
from .commands import *
from .faults import *


class VersionInfo(NamedTuple):
    major: int
    minor: int
    micro: int
    releaselevel: Literal["alpha", "beta", "candidate", "final"]
    serial: int
    metadata: str

    def __new__(
            cls,
            major: int,
            minor: int,
            micro: int,
            releaselevel: Literal["alpha", "beta", "candidate", "final"],
            serial: int,
            metadata: str,
    ) -> Self: ...


# Placeholder, modified by dynamic-versioning.
version_info: Final[VersionInfo]
