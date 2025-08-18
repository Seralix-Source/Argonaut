from rich.pretty import pprint

from argonaut import *


@command
def callback(
        file=Operand(),
        /,
        args=Option("--args"),
        *,
        debug=Switch("--debug", "-d"),
):
    pass


if __name__ == '__main__':
    pprint(callback)
