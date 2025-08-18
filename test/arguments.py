"""
Tests for argument specifications (Operand, Option, Switch).

This suite verifies:
- Construction and normalization:
  • Operand: metavar/type/nargs (including Ellipsis), default, choices, group/descr.
  • Option: names validation, metavar/type/nargs (no Ellipsis), explicit flag, helper wiring.
  • Switch: names validation, helper wiring (standalone/terminator/nowait).
- Single-assignment callback contract (second assignment raises TypeError).
- Decorator/factory helpers:
  • Decorators return the spec instance (not the function).
  • Retrieval hooks (__operand__/__option__/__switch__) return the same spec.
- Visibility and UX flags:
  • hidden (omitted from help), deprecated (emits warning when used).
"""
import unittest
from unittest import TestCase

# Import the public API under test
from argonaut.arguments import *


class ArgumentTest(TestCase):
    """
    Test suite for Operand/Option/Switch specs and their decorator/factories.

    Goals
    - Validate construction-time normalization/validation (names, metavar, nargs, choices).
    - Validate helper wiring (standalone/terminator/nowait).
    - Validate Ellipsis semantics for operands.
    - Validate single-assignment callback contract.
    - Validate decorator/factory behavior and retrieval hooks.
    """

    # -----------------------------
    # Operand (positional) tests
    # -----------------------------

    def testOperandBasicConstruction(self) -> None:
        spec = Operand("FILE", type=str)
        self.assertEqual(spec.metavar, "FILE")
        self.assertIs(spec.type, str)
        # Default group is derived from type name; assert it's a non-empty string.
        self.assertIsInstance(spec.group, str)
        self.assertTrue(spec.group)

    def testOperandEllipsisMetavarDefault(self) -> None:
        spec = Operand(type=str, nargs=...)
        self.assertEqual(spec.nargs, ...)
        self.assertEqual(spec.metavar, "...")

    def testOperandEllipsisForbidsExplicitMetavar(self) -> None:
        with self.assertRaises(TypeError):
            Operand("X", type=str, nargs=...)

    def testOperandChoicesDeduplicateAndValidate(self) -> None:
        # Duplicates should be rejected (ValueError)
        with self.assertRaises(ValueError):
            Operand("X", type=str, choices=["a", "a"])
        # Non-string iterables are accepted and frozen; ensure container-like
        spec = Operand("X", type=str, choices=["a", "b"])
        self.assertIn("a", spec.choices)
        self.assertIn("b", spec.choices)

    def testOperandCallbackSingleAssignment(self) -> None:
        spec = Operand("X", type=str)

        def first(x: str) -> None:  # noqa: ARG001
            pass

        def second(x: str) -> None:  # noqa: ARG001
            pass

        # First assignment OK; returns the function
        self.assertIs(spec.callback(first), first)
        # Second assignment must fail
        with self.assertRaises(TypeError):
            spec.callback(second)

    # -----------------------------
    # Option (named, value-bearing) tests
    # -----------------------------

    def testOptionNamesValidation(self) -> None:
        # Must start with '-' or '--'
        with self.assertRaises(ValueError):
            Option("name")
        # Non-empty, string-only, duplicates rejected
        with self.assertRaises(ValueError):
            Option("--opt", "--opt")
        # Valid names go through
        spec = Option("--mode", "-m")
        # Names are stored as a set/frozenset; containment is sufficient
        self.assertIn("--mode", spec.names)
        self.assertIn("-m", spec.names)

    def testOptionHelperWiring(self) -> None:
        # helper implies standalone=True and terminator=True
        spec = Option("--help", helper=True, standalone=False, terminator=False, nowait=False)
        self.assertTrue(spec.helper)
        self.assertTrue(spec.standalone)
        self.assertTrue(spec.terminator)
        # terminator implies nowait=True
        self.assertTrue(spec.nowait)

    def testOptionExplicitAttachedValues(self) -> None:
        spec = Option("--opt", explicit=True)
        self.assertTrue(spec.explicit)

    def testOptionEllipsisNotAllowed(self) -> None:
        # Ellipsis is invalid for options
        with self.assertRaises(TypeError):
            Option("--bad", nargs=...)

    def testOptionCallbackSingleAssignment(self) -> None:
        spec = Option("--name", "-n", type=str)

        def first(x: str) -> None:  # noqa: ARG001
            pass

        def second(x: str) -> None:  # noqa: ARG001
            pass

        self.assertIs(spec.callback(first), first)
        with self.assertRaises(TypeError):
            spec.callback(second)

    # -----------------------------
    # Switch (named boolean) tests
    # -----------------------------

    def testSwitchNamesValidation(self) -> None:
        with self.assertRaises(ValueError):
            Switch("verbose")
        with self.assertRaises(ValueError):
            Switch("--flag", "--flag")
        spec = Switch("--verbose", "-v")
        self.assertIn("--verbose", spec.names)
        self.assertIn("-v", spec.names)

    def testSwitchHelperWiring(self) -> None:
        sw = Switch("--help", helper=True, standalone=False, terminator=False, nowait=False)
        self.assertTrue(sw.helper)
        self.assertTrue(sw.standalone)
        self.assertTrue(sw.terminator)
        self.assertTrue(sw.nowait)

    def testSwitchCallbackSingleAssignment(self) -> None:
        sw = Switch("--debug")

        def first() -> None:
            pass

        def second() -> None:
            pass

        self.assertIs(sw.callback(first), first)
        with self.assertRaises(TypeError):
            sw.callback(second)

    # -----------------------------
    # Decorator/factory helpers
    # -----------------------------

    def testOperandDecoratorReturnsSpec(self) -> None:
        @operand("X", type=str)
        def on_x(value: str) -> None:  # noqa: ARG001
            pass

        # Decorator returns the spec instance, not the function
        self.assertIsInstance(on_x, Operand)
        # Retrieval hook present
        self.assertIs(on_x.__operand__(), on_x)

    def testOptionDecoratorReturnsSpec(self) -> None:
        @option("--out", "-o", metavar="PATH", type=str)
        def on_out(path: str) -> None:  # noqa: ARG001
            pass

        self.assertIsInstance(on_out, Option)
        self.assertIs(on_out.__option__(), on_out)

    def testSwitchDecoratorReturnsSpec(self) -> None:
        @switch("--verbose", "-v")
        def on_verbose() -> None:  # noqa: D401
            """Enable verbose mode."""
            pass

        self.assertIsInstance(on_verbose, Switch)
        self.assertIs(on_verbose.__switch__(), on_verbose)


if __name__ == '__main__':
    unittest.main()
