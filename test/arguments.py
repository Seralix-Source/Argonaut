# python
"""
Arguments module behavioral tests (no explicit None, no spec-from-spec).

Scope
- Validate public specs (Cardinal, Option, Flag): construction, normalization, flags.
- Validate decorator/factory helpers (cardinal/option/flag): single-assignment and Supports* hooks.
- Validate generated __call__ forwarding across arities ('?', '*', '+', fixed-N, zero-arity).
- Validate metadata constraints (group pluralization, descr defaults to None but explicit None rejected,
  names validation, choices rules).

Conventions
- Test method names follow CamelCase per project convention.
- Never pass explicit None for any parameter; omit instead.
- Never build a factory or a spec from another spec (no field copying from instances).
"""

from __future__ import annotations

import unittest
import warnings
from unittest import TestCase

from argonaut import Cardinal, Option, Flag, cardinal, option, flag


class TestCardinal(TestCase):
    """Behavioral tests for Cardinal (positional) specifications."""

    def testCardinalGroupPluralDefault(self):
        c = Cardinal()
        self.assertEqual(c.group, "cardinals")

    def testCardinalGroupExplicitNonEmpty(self):
        c = Cardinal("FILE", group="operands")
        self.assertEqual(c.group, "operands")

    def testCardinalGroupEmptyRejected(self):
        with self.assertRaises(ValueError):
            Cardinal("FILE", group="  ")

    def testCardinalDescrDefaultsToNone(self):
        c = Cardinal("FILE")
        self.assertIsNone(c.descr)

    def testCardinalDescrExplicitNoneRejected(self):
        with self.assertRaises(TypeError):
            Cardinal("FILE", descr=None)

    def testCardinalMetavarMustBeNonEmpty(self):
        with self.assertRaises(ValueError):
            Cardinal("")

    def testCardinalGreedyForbidsExplicitMetavar(self):
        with self.assertRaises(TypeError):
            Cardinal("FILES", type=str, nargs=Ellipsis)

    def testCardinalGreedyViaLiteralDotDotDot(self):
        received = []

        @cardinal(type=str, nargs="...")
        def onFiles(*files):
            received.append(files)

        onFiles("a", "b", "c")
        self.assertEqual(received[-1], ("a", "b", "c"))

    def testCardinalOptionalSingleWithDefault(self):
        received = []

        @cardinal("X", type=str, nargs="?", default="DEF")
        def onX(x):
            received.append(x)

        onX("VAL")
        self.assertEqual(received[-1], "VAL")
        onX()
        self.assertEqual(received[-1], "DEF")

    def testCardinalVariadicPlusForwarding(self):
        received = []

        @cardinal("HEAD", type=str, nargs="+")
        def onHeadAndTail(head, *tail):
            received.append((head, tail))

        onHeadAndTail("h", "t1", "t2")
        self.assertEqual(received[-1], ("h", ("t1", "t2")))

    def testCardinalFixedNForwarding(self):
        received = []

        @cardinal(type=str, nargs=3)
        def onTriple(p0, p1, p2):
            received.append((p0, p1, p2))

        onTriple("a", "b", "c")
        self.assertEqual(received[-1], ("a", "b", "c"))

    def testCardinalDecoratorSingleAssignmentGuard(self):
        dec = cardinal("FILE")

        @dec
        def first():
            pass

        with self.assertRaises(TypeError):
            @dec
            def second():
                pass


class TestOption(TestCase):
    """Behavioral tests for Option (named, value-bearing) specifications."""

    def testOptionRequiresAtLeastOneName(self):
        with self.assertRaises(TypeError):
            Option()

    def testOptionGroupPluralDefault(self):
        o = Option("--opt")
        self.assertEqual(o.group, "options")

    def testOptionDescrDefaultsToNone(self):
        o = Option("--opt")
        self.assertIsNone(o.descr)

    def testOptionDescrExplicitNoneRejected(self):
        with self.assertRaises(TypeError):
            Option("--opt", descr=None)

    def testOptionNamesRejectUnderscore(self):
        with self.assertRaises(ValueError):
            Option("--bad_name")

    def testOptionDuplicateNamesRejected(self):
        with self.assertRaises(ValueError):
            Option("--dup", "--dup")

    def testOptionNamesAllowI18N(self):
        o = Option("--名-前")
        self.assertIn("--名-前", o.names)

    def testOptionHelperWiring(self):
        o = Option("--help", helper=True)
        self.assertTrue(o.helper)
        self.assertTrue(o.standalone)
        self.assertTrue(o.terminator)
        self.assertTrue(o.nowait)

    def testOptionHelperCannotBeHidden(self):
        with self.assertRaises(TypeError):
            Option("--help", helper=True, hidden=True)

    def testOptionHelperDeprecatedWarns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Option("--help", helper=True, deprecated=True)
            self.assertTrue(any("deprecated" in str(w.message).lower() for w in caught))

    def testOptionInlineFlagExposed(self):
        o = Option("--opt", inline=True)
        self.assertTrue(o.inline)

    def testOptionVariadicStarForwarding(self):
        received = []

        @option("--tag", nargs="*")
        def onTag(*tags):
            received.append(tags)

        onTag("t1", "t2", "t3")
        self.assertEqual(received[-1], ("t1", "t2", "t3"))

    def testOptionFixedNForwarding(self):
        received = []

        @option("--point", nargs=3)
        def onPoint(x, y, z):
            received.append((x, y, z))

        onPoint("1", "2", "3")
        self.assertEqual(received[-1], ("1", "2", "3"))

    def testOptionDecoratorSingleAssignmentGuard(self):
        dec = option("--only-once")

        @dec
        def first(_value=None):
            pass

        with self.assertRaises(TypeError):
            @dec
            def second(_value=None):
                pass

    def testOptionChoicesDuplicatesRejectedForGenericIterable(self):
        # duplicates in a generic iterable (e.g., list) are rejected
        with self.assertRaises(ValueError):
            Option("--mode", choices=["fast", "safe", "fast"])

    def testOptionChoicesFrozenForSetAndRange(self):
        o2 = Option("--level", choices={1, 2, 3})
        self.assertIsInstance(o2.choices, frozenset)
        self.assertEqual(o2.choices, frozenset({1, 2, 3}))

        o3 = Option("--range", choices=range(3))
        self.assertIsInstance(o3.choices, frozenset)
        self.assertEqual(o3.choices, frozenset({0, 1, 2}))


class TestFlag(TestCase):
    """Behavioral tests for Flag (presence-only) specifications."""

    def testFlagNamesValidation(self):
        with self.assertRaises(ValueError):
            Flag("--bad_name")

    def testFlagGroupPluralDefault(self):
        f = Flag("--verbose")
        self.assertEqual(f.group, "flags")

    def testFlagDescrDefaultsToNone(self):
        f = Flag("--verbose")
        self.assertIsNone(f.descr)

    def testFlagDescrExplicitNoneRejected(self):
        with self.assertRaises(TypeError):
            Flag("--verbose", descr=None)

    def testFlagHelperWiringAndConstraints(self):
        f = Flag("--help", helper=True)
        self.assertTrue(f.helper and f.standalone and f.terminator and f.nowait)
        with self.assertRaises(TypeError):
            Flag("--help", helper=True, hidden=True)

    def testFlagForwardingZeroArity(self):
        called = []

        @flag("--verbose")
        def onVerbose():
            called.append(True)

        onVerbose()
        self.assertTrue(called and called[-1] is True)

    def testFlagDecoratorSingleAssignmentGuard(self):
        dec = flag("--flag")

        @dec
        def first():
            pass

        with self.assertRaises(TypeError):
            @dec
            def second():
                pass


if __name__ == "__main__":
    unittest.main()