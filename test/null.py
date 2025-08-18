"""
Tests for the null singleton.

This module verifies semantic guarantees of the `nulltype` sentinel:
- Singleton identity (single instance per interpreter process).
- Falsy semantics and string/representation behavior.
- Rich rendering integration.
- Copying, deep copying, pickling, and thread safety properties.
- Finality (type cannot be subclassed).
"""
import copy
import pickle
import unittest
from threading import Thread, Lock
from unittest import TestCase

from rich.console import Console
from rich.text import Text

from argonaut.null import *


class NullTest(TestCase):
    """
    Test suite for the `nulltype` singleton.

    This suite asserts that:
    - nulltype() always returns the same instance (singleton).
    - The exported `null` object equals that instance.
    - The sentinel is falsy but not equal to other falsy values.
    - Rich integration and string representation are stable and predictable.
    - Copy/deepcopy/pickle round-trips preserve identity.
    - Concurrent construction attempts are safe and return the same instance.
    - The type is final and cannot be subclassed.
    """

    def setUp(self) -> None:
        """
        Prepare a fresh reference to the singleton and its type for each test.
        """
        self.null: nulltype = nulltype()
        self.nulltype: type[nulltype] = nulltype

    def testSingleton(self) -> None:
        """
        The constructor returns the same object reference on every call.
        """
        # Identity must be preserved across repeated constructions.
        self.assertIs(self.null, self.nulltype())

    def testModuleSingleton(self) -> None:
        """
        The exported `null` matches the constructed singleton instance.
        """
        # The module-level singleton should be identical to any constructed instance.
        self.assertIs(null, self.null)
        self.assertIs(null, self.nulltype())

    def testIsInstance(self) -> None:
        """
        The constructed object is an instance of `nulltype`.
        """
        self.assertIsInstance(self.null, self.nulltype)

    def testHashAndSetUniqueness(self) -> None:
        """
        Hash is stable and set semantics deduplicate the singleton.
        """
        # Sets should treat multiple references as a single element.
        set = {self.null, self.nulltype()}
        self.assertEqual(len(set), 1)
        self.assertEqual(hash(self.null), hash(self.nulltype()))

    def testRich(self) -> None:
        """
        __rich__() returns a dim-styled Text 'null' (stable Rich integration).
        """
        self.assertEqual(self.null.__rich__(), Text("null", style="dim"))

    def testRichConsolePrint(self) -> None:
        """
        Console.print(...) renders 'null' without ANSI when color is disabled.
        """
        # Capture console output with color disabled for deterministic comparison.
        console = Console(color_system=None, force_terminal=False)
        with console.capture() as capture:
            console.print(self.null)
        self.assertEqual(capture.get().strip(), "null")

    def testRepr(self) -> None:
        """
        __repr__() is the literal string 'null'.
        """
        self.assertEqual(self.null.__repr__(), "null")

    def testStrEqualsRepr(self) -> None:
        """
        str(...) equals repr(...) for the sentinel (user-facing stability).
        """
        self.assertEqual(str(self.null), "null")

    def testFalsely(self) -> None:
        """
        The sentinel is falsy (bool(null) is False).
        """
        self.assertFalse(bool(self.null))

    def testNotEqualToNoneOrFalse(self) -> None:
        """
        Falsy does not imply equality with other falsy values (None/False).
        """
        # Guard against accidental equality implementations.
        self.assertNotEqual(self.null, None)
        self.assertNotEqual(self.null, False)  # noqa: E712

    def testCopyDeepcopyPreserveSingleton(self) -> None:
        """
        copy() and deepcopy() preserve the identity of the singleton.
        """
        # Shallow and deep copies should not produce new instances.
        self.assertIs(copy.copy(self.null), self.null)
        self.assertIs(copy.deepcopy(self.null), self.null)

    def testPickleRoundTrip(self) -> None:
        """
        Pickle round-trips preserve the identity of the singleton.
        """
        # Serialization and deserialization should return the same instance.
        data: bytes = pickle.dumps(self.null)
        restored: nulltype = pickle.loads(data)
        self.assertIs(restored, self.null)

    def testThreadSafetySingleton(self) -> None:
        """
        Concurrent constructions return the same instance (thread-safe singleton).
        """
        results: list[nulltype] = []
        lock: Lock = Lock()

        def worker():
            # Each thread attempts to construct the singleton.
            instance = self.nulltype()
            with lock:
                results.append(instance)

        threads: list[Thread] = [Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # All threads should have obtained the exact same instance.
        self.assertTrue(results)
        for instance in results:
            self.assertIs(instance, self.null)

    def testFinalClass(self) -> None:
        """
        The class is final: attempts to subclass must fail with TypeError.
        """
        with self.assertRaises(TypeError):
            type("nulltype", (self.nulltype,), {})


if __name__ == '__main__':
    # Allow running this test module directly: `python -m pytest` or `python null.py`.
    unittest.main()
