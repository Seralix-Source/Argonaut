"""
Commands module behavioral tests (parsing, faults, conflicts, delegation).

Scope
- Validate friendly faults for unknown/duplicate/inline policies.
- Validate converter faults and choices errors.
- Validate group conflicts and standalone/terminator behavior.
- Validate delegation to subcommands and unparsed-input handling.

Conventions
- Test method names follow CamelCase per project convention.
- Tests use the public API (command, invoke, Cardinal, Option, Flag).
"""

from __future__ import annotations

import unittest
from unittest import TestCase

from argonaut import command, invoke, Cardinal, Option, Flag
from argonaut.faults import (
    UnknownModifierError,
    DuplicateModifierError,
    InlineParamRequiredError,
    TooManyInlineParamsError,
    UncastableParamError,
    InvalidChoiceError,
    ConflictingGroupError,
    StandaloneOnlyError,
    UnknownCommandError,
    TooManyPositionalsError,
)


class TestCommandParsing(TestCase):
    """Behavioral tests for Command parsing and faults."""

    def testUnknownModifierRaises(self):
        @command
        def tool(
                name=Cardinal(),
                /,
                threads=Option("--threads", type=int),
        ):
            pass

        with self.assertRaises(UnknownModifierError):
            invoke(tool, "file.txt --threds=4")  # misspelled --threads

    def testDuplicateFlagRaises(self):
        @command
        def tool(*, verbose=Flag("--verbose")):
            pass

        with self.assertRaises(DuplicateModifierError):
            invoke(tool, "--verbose --verbose")

    def testInlineRequiredOptionWithoutParamRaises(self):
        @command
        def tool(token=Option("--token", inline=True, nargs=1)):
            pass

        # inline-only but provided as spaced param → inline required fault
        with self.assertRaises(InlineParamRequiredError):
            invoke(tool, "--token abc")

    def testTooManyInlineParamsRaises(self):
        @command
        def tool(one=Option("--one", nargs=1)):
            pass

        # one expected, but provided two inline params (split on ',' by parser)
        with self.assertRaises(TooManyInlineParamsError):
            invoke(tool, "--one=a,b")

    def testConverterUncastableParamError(self):
        @command
        def tool(threads=Option("--threads", type=int)):
            pass

        with self.assertRaises(UncastableParamError):
            invoke(tool, "--threads=r")

    def testInvalidChoiceErrorForOption(self):
        @command
        def tool(mode=Option("--mode", choices={"fast", "safe"})):
            pass

        with self.assertRaises(InvalidChoiceError):
            invoke(tool, "--mode=slow")

    def testInvalidChoiceErrorForPositional(self):
        @command
        def tool(file=Cardinal(choices={"a.txt", "b.txt"}), /):
            pass

        with self.assertRaises(InvalidChoiceError):
            invoke(tool, "c.txt")

    def testConflictingGroupError(self):
        # two options in different groups that conflict
        @command(conflicts=[("outputs", "streams")])
        def tool(
                out=Option("--out", group="outputs"),
                stdout=Option("--stdout", group="streams"),
        ):
            pass

        with self.assertRaises(ConflictingGroupError):
            invoke(tool, "--out=path --stdout=on")

    def testStandaloneOnlyErrorForHelperWithOthers(self):
        @command
        def tool(
                out=Option("--out"),
                *,
                help=Flag("--help", helper=True),  # helper implies standalone+terminator+nowait
        ):
            pass

        with self.assertRaises(StandaloneOnlyError):
            invoke(tool, "--help --out=path")

    def testUnknownSubcommandRaises(self):
        # parent with a subcommand; invoking an unknown subcommand should raise
        @command
        def parent():
            pass

        @parent.command
        def run():
            pass

        with self.assertRaises(UnknownCommandError):
            invoke(parent, "rn")  # misspelled 'run'

    def testTooManyPositionalsError(self):
        @command
        def tool(*, flag=Flag("--flag")):
            pass

        with self.assertRaises(TooManyPositionalsError):
            invoke(tool, "--flag extra")


if __name__ == "__main__":
    unittest.main()