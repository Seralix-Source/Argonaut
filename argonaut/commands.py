"""
command tree, parsing, and dispatch (public API with localized internals).

what this module provides
- Command: a dynamic, factory-built command node that can:
  • define positional arguments (cardinals) and named arguments (options/flags),
  • participate in a tree of subcommands (parent/children),
  • parse a prompt into a namespace, validate/conflict-check, and dispatch callbacks,
  • collect warnings/exceptions and finalize with a single grouped exit.

- helpers:
  • command(...): decorator/factory to create commands (top-level or children).
  • invoke(...): convenience entry point to run any command-like object.
  • include(...): discover and mount orphan/template commands via module-globs.

design highlights
- dynamic classes: commands are factory-built with a metaclass (_CommandType) that:
  • normalizes the public name (kebab-case) and exposes read-only views of fields,
  • injects a __call__ that mirrors the original callback signature (_call),
  • forbids subclassing of such dynamic instances (stable public surface).
- friendly diagnostics: messages are clear, lowercase, and position-aware:
  • uses _ordinal() to say “at the third position” or “at the first sub-position”,
  • adds contextual hints so beginners can learn by empiric feedback.
- faults: warnings/exceptions flow through trigger(), supporting:
  • immediate or deferred delivery (deferred=True accumulates faults),
  • aggregation into CommandExit (ExceptionGroup) on finalize(),
  • in shell-mode, optional help display before raising.

parsing model (overview)
- modifiers vs cardinals:
  • tokens that start with '-' are treated as modifiers (unless a greedy cardinal takes the rest),
  • otherwise tokens become positional (cardinals), respecting declared order.
- conversion & choices:
  • each argument.type is applied to collected values, with CommandWarnings forwarded,
  • unexpected converter errors are wrapped as UncastableParamError,
  • values are validated against choices; invalids use InvalidChoiceError.
- policy checks:
  • deprecation: DeprecatedArgumentWarning,
  • duplicates: DuplicateModifierError,
  • group conflicts: ConflictingGroupError,
  • standalone-only: StandaloneOnlyError,
  • inline requirements/excess: InlineParamRequiredError, TooManyInlineParamsError,
  • arity issues: MissingParamError, AtLeastOneParamRequiredError, NotEnoughParamsError.
- delegation:
  • before parsing positionals, a bare token may represent a subcommand (children),
  • when delegation fails, UnparsedInputError explains what remains and how to proceed.

notes
- this module cooperates with:
  • argonaut.arguments for spec classes and decorators,
  • argonaut.faults for fault types and delivery,
  • argonaut.internals for StorageGuard, Unset, view, rename, etc.
- public API is Command, command, invoke; other names are internal.
"""
import difflib
import importlib
import inspect
import itertools
import os
import re
import shlex
import sys
import textwrap
from collections import ChainMap, defaultdict, deque
from collections.abc import Iterable
from inspect import Parameter
from warnings import catch_warnings

from rich.console import Console

from .arguments import Cardinal, Option, Flag, flag
from .faults import *
from .internals import *


Skipped = type('Skipped', (Exception,), {})


def _call(callback):
    """
    build a bound __call__ method that mirrors a callback’s signature and forwards to it.

    intent
    - given a “callback descriptor” whose parameters are already extracted
      (callback.parameters: list[inspect.Parameter]), synthesize a __call__ with:
        • the same parameter names and layout (positional-only '/', keyword-only '*'),
        • a first receiver argument named 'self' (or '__self__' when the original
          function’s first parameter is literally 'self', to avoid shadowing),
        • forwarding logic that invokes the stored backing callback: self.-callback(...).

    signature synthesis
    - preserves positional-only and keyword-only partitions by inserting '/' and '*'
      once, at the first occurrence of a POSITIONAL_OR_KEYWORD and KEYWORD_ONLY
      parameter respectively (Python’s callable syntax).
    - the generated function is renamed to "__call__" via @rename for cleaner traces.

    defaults
    - __defaults__: tuple of defaults for all non-keyword-only parameters, in order.
      note: this expects each default object to expose `.default` (your argument
      resolvables wrap the concrete default there). If that contract changes, adjust
      extraction accordingly.
    - __kwdefaults__: mapping for keyword-only parameters; here initialized with
      keys for each kw-only name and a sentinel value False (so the call-site can
      detect explicit omission cheaply).

    result
    - returns the synthesized function object (callable). The metaclass installs it
      on the dynamic class when factory=True so instances become directly callable.
    """
    signature = [self := "__self__" if callback.parameters and callback.parameters[0].name == "self" else "self"]
    arguments = []
    slashed = False
    starred = False

    for parameter in callback.parameters:
        if parameter.kind is Parameter.POSITIONAL_OR_KEYWORD and not slashed:
            signature.append("/")
            slashed = True
        if parameter.kind is Parameter.KEYWORD_ONLY and not starred:
            signature.append("*")
            starred = True
        signature.append(parameter.name)
        arguments.append(f"{parameter.name}={parameter.name}" if parameter.kind is Parameter.KEYWORD_ONLY else parameter.name)

    if len(signature) > 1 and signature[1] == "/":
        signature.pop(1)

    exec(textwrap.dedent(f"""
        @rename("__call__")
        def __call__({", ".join(signature)}):
            return object.__getattribute__({self}, "-callback")({", ".join(arguments)})
    """), globals(), namespace := locals())

    namespace["__call__"].__doc__ = textwrap.dedent(f"""\
            Dynamically generated invoker that mirrors the command callback signature.

            signature
            - __call__({", ".join(signature)})

            behavior
            - forwards all received arguments to the stored callback.
            - preserves positional-only ("/") and keyword-only ("*") partitions so the
              runtime calling convention matches the original callback.

            defaults
            - __defaults__ are seeded from the callback's positional/positional-only defaults
              (in order), enabling calls without explicitly passing those arguments.
            - __kwdefaults__ maps keyword-only parameter names to a False sentinel to
              detect omission at call time when needed by the runtime.
        """)

    namespace["__call__"].__defaults__ = tuple(
        parameter.default.default for parameter in callback.parameters if parameter.kind is not Parameter.KEYWORD_ONLY
    )

    namespace["__call__"].__kwdefaults__ = dict.fromkeys(
        (parameter.name for parameter in callback.parameters if parameter.kind is Parameter.KEYWORD_ONLY), False
    )

    return namespace["__call__"]


class _CommandType(type):
    """
    internal metaclass that builds the dynamic “command” classes.

    responsibilities
    - optionally install an arity-aware __call__ when building factory instances
      (when options['factory'] is truthy and a callback is provided).
    - normalize the public type name to kebab-case and expose it as __qualname__;
      mark __module__ as Unset for limbo types built during construction.
    - project declared __fields__ into read-only properties using view(...).
    - attach friendly representations:
      • __repr__: compact one-liner using current metadata snapshot
      • __rich_repr__: yields (key, value) pairs for pretty/rich renderers
    - forbid subclassing of dynamic/factory-built classes by installing a raising
      __init_subclass__ when options['factory'] is truthy.

    notes
    - this metaclass does not validate field values; upstream helpers perform
      normalization and validation before class materialization.
    """

    __fields__ = ()

    def __new__(metacls, name, bases, namespace, /, **options):
        """
        construct a concrete command type with optional factory semantics.

        parameters
        - name: original type name; will be normalized to kebab-case.
        - bases: base classes (usually a single concrete base).
        - namespace: class body; may include __fields__ to project via view(...).
        - options:
          • factory: bool (default False) — when true, inject __call__ and block
            subclassing on the resulting dynamic class.
          • callback: callable|Unset — when factory is true and a callback is
            present, an arity-aware __call__ is synthesized via _call(callback).

        behavior
        - installs __call__ for factory builds when a callback descriptor is present.
        - rewrites the public name to kebab-case and sets __qualname__ accordingly.
        - overlays read-only property views for each declared field in __fields__.
        - attaches __repr__ and __rich_repr__ helpers.
        - for factory builds, installs a raising __init_subclass__ to prevent
          further inheritance from the dynamic class.
        """
        if options.get("factory", False) and options["callback"] is not Unset:
            namespace |= {"__call__": _call(options["callback"])}

        cls = super().__new__(
            metacls,
            name := re.sub(r"(?<!^)(?=[A-Z])", r"-", name.strip("_")).lower(),
            bases,
            namespace | {
                "__module__": Unset,
                "__qualname__": name
            } | {
                name: view(name) for name in namespace.get("__fields__", ())
            }
        )

        @rename("__repr__")
        def __repr__(self):
            """
            debug-oriented single-line representation.

            shape
            - <typename>(key=value, ...) where keys come from __fields__ and values
              reflect the current metadata snapshot.

            notes
            - pairs are sourced via __rich_repr__ to keep plain and rich output in sync.
            """
            return f"{name}({", ".join("%s=%r" % pair for pair in self.__rich_repr__())})"

        cls.__repr__ = __repr__

        @rename("__rich_repr__")
        def __rich_repr__(self):
            """
            rich/pretty representation producer.

            behavior
            - yield (key, value) pairs for each declared field in __fields__. This
              enables rich/pretty printers to render a compact, readable view.

            notes
            - values are read from the internal backing storage to avoid extra wrapping.
            """
            for field in type(self).__fields__:
                yield field, object.__getattribute__(self, "-" + field)

        cls.__rich_repr__ = __rich_repr__

        if options.get("factory", False):  # dynamic, generated class instance
            @rename("__init_subclass__")
            def __init_subclass__(cls, **options):
                """
                forbid subclassing of dynamic/factory-built command types.

                rationale
                - dynamic classes represent finalized shapes produced at construction
                  time; inheriting from them would break invariants and widen surface
                  without benefit.

                errors
                - always raises TypeError when subclassing is attempted.
                """
                raise TypeError(f"type {name!r} is not an acceptable base type")

            cls.__init_subclass__ = classmethod(__init_subclass__)

        return cls


def _ordinal(number, *, at=False, sub=False, frm=False):
    """
    return a human-friendly ordinal for number.

    parameters
    - number: positive integer (>= 1)
    - at: when true, prefixes with location context:
          "at the <ordinal> position"
    - sub: when true and at is also true, inserts "sub" before "position":
           "at the <ordinal> sub-position"
           (useful for nested contexts such as subcommands)

    behavior
    - 1..10 use word forms ("first", "second", ... "tenth")
    - numbers > 10 use numeric ordinals with suffix ("11th", "21st", ...)
    - when at is false, returns just the ordinal core ("first", "11th", ...)

    examples
    - _ordinal(1) -> "first"
    - _ordinal(11) -> "11th"
    - _ordinal(3, at=True) -> "at the third position"
    - _ordinal(2, at=True, sub=True) -> "at the second sub-position"
    """
    if number < 1:
        raise ValueError("ordinal expects a positive integer")

    words = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")

    if number <= 10:
        core = words[number - 1]
    else:
        # correct suffix for teens (11, 12, 13) and general case
        if 10 <= (number % 100) <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
        core = f"{number}{suffix}"

    return f"{"from" if frm else "at"} the {core} {"sub-" * sub}position" if at or frm else core


def _process_source(cls, source, groups):
    """
    normalize and validate a command “source” into callback/spec maps, updating groups.

    accepted forms
    - callable: a function whose parameters and their defaults encode specs:
      • cardinals: positional-only params (before '/') whose defaults resolve to Cardinal
      • options: positional-or-keyword params whose defaults resolve to Option
      • flags: keyword-only params (after '*') whose defaults resolve to Flag
    - iterable: ordered specs in the sequence [cardinals..., options..., flags...]
      (any section may be empty, but the order must be preserved)

    grouping
    - groups: a mutable mapping keyed by group name (str) whose values are tuple-like
      containers of specs. This collector is updated in place as specs are discovered.
      The exact container semantics may evolve; treat it as an append-only sequence.
      Both callable and iterable forms append each spec to groups[spec.group].

    outputs
    - dict with:
      • callback: the original callable (or Unset when iterable form)
      • cardinals: mapping[position|name, Cardinal]
      • modifiers: mapping[name, Option|Flag] keyed by each declared name

    ordering rules and constraints
    - in iterable form:
      • once an option/flag is seen, no more cardinals may follow
      • no Option can follow a Flag
    - greedy cardinal (nargs is Ellipsis) must be last among cardinals
    - “hidden”/“deprecated” cardinals cannot be followed by visible/non-deprecated ones
    - duplicate option/flag names are rejected
    """
    typename = cls.__name__

    cardinals = {}
    modifiers = {}

    def _resolve_argument(x, ctx):
        """
        resolve a Supports*-like default/object into a concrete spec.

        ctx
        - int (iterable form): 0-based index used for diagnostics/ordering
        - inspect.Parameter (callable form): parameter object (name/kind used)

        behavior
        - exactly one Supports* method must be present and callable:
          __cardinal__ | __option__ | __flag__
        - returns the concrete Cardinal/Option/Flag object or raises TypeError.
        """
        if sum((
                hasattr(x, "__cardinal__") and callable(x.__cardinal__),
                hasattr(x, "__option__") and callable(x.__option__),
                hasattr(x, "__flag__") and callable(x.__flag__),
        )) != 1:
            if isinstance(ctx, int):
                raise TypeError(f"{typename} object {_ordinal(ctx, at=True)} must be argument-resolvable")
            raise TypeError(f"{typename} callback parameter {ctx.name!r} default must be argument-resolvable")

        if hasattr(x, "__cardinal__"):
            cardinal = x.__cardinal__()
            if not isinstance(cardinal, Cardinal):
                raise TypeError("__cardinal__() non-cardinal returned")
            return cardinal
        if hasattr(x, "__option__"):
            option = x.__option__()
            if not isinstance(option, Option):
                raise TypeError("__option__() non-option returned")
            return option
        if hasattr(x, "__flag__"):
            flag = x.__flag__()
            if not isinstance(flag, Flag):
                raise TypeError("__flag__() non-flag returned")
            return flag
        raise RuntimeError("unreachable")

    def _resolve_cardinal(x, ctx):
        """
        register a cardinal and enforce cardinal-only constraints.

        constraints
        - callable form: param must be positional-only
        - must not appear after an option/flag
        - greedy cardinal must be last among cardinals
        - once a “hidden”/“deprecated” cardinal appears, no visible/active may follow
        """
        nonlocal active, greedy, hidden, deprecated
        if isinstance(ctx, Parameter) and ctx.kind is not Parameter.POSITIONAL_ONLY:
            raise TypeError(f"{typename} callback parameter {ctx.name!r} must be positional-only")
        if active != "cardinal":
            raise TypeError(f"{typename} cardinal {_ordinal(ctx, at=True)} cannot follow an option or a flag")
        if greedy:
            if isinstance(greedy, Parameter):
                raise TypeError(f"{typename} greedy cardinal at parameter {greedy.name!r} must be the last one")  # type: ignore[attr-defined]
            raise TypeError(f"{typename} greedy cardinal {_ordinal(greedy, at=True)} must be the last one")
        greedy = ctx if x.nargs is Ellipsis else None

        hidden |= x.hidden
        if hidden and not x.hidden:
            if isinstance(ctx, int):
                raise TypeError(f"{typename} hidden cardinal {_ordinal(ctx, at=True)} cannot follow a hidden one")
            raise TypeError(f"{typename} hidden cardinal at parameter {ctx.name!r} cannot follow a hidden one")

        deprecated |= x.deprecated
        if deprecated and not x.deprecated:
            if isinstance(ctx, int):
                raise TypeError(f"{typename} deprecated cardinal {_ordinal(ctx, at=True)} cannot follow a deprecated one")
            raise TypeError(f"{typename} deprecated cardinal at parameter {ctx.name!r} cannot follow a deprecated one")

        cardinals[getattr(ctx, "name", ctx)] = x

    def _resolve_modifier(x, ctx):
        """
        register an option/flag and enforce modifier ordering/kind constraints.

        constraints
        - callable form:
          • Option: must be POSITIONAL_OR_KEYWORD (standard definition)
          • Flag: must be KEYWORD_ONLY
        - iterable form:
          • once a Flag is seen, no Option may follow
        - names across Option/Flag must be unique; duplicate aliases rejected
        """
        nonlocal active
        if isinstance(ctx, Parameter):
            if isinstance(x, Option) and ctx.kind is not Parameter.POSITIONAL_OR_KEYWORD:
                raise TypeError(f"{typename} callback parameter {ctx.name!r} must follow standard declaration")
            if isinstance(x, Flag) and ctx.kind is not Parameter.KEYWORD_ONLY:
                raise TypeError(f"{typename} callback parameter {ctx.name!r} must be keyword-only")
        if isinstance(x, Option) and active == "flag":
            raise TypeError(f"{typename} option {_ordinal(ctx, at=True)} cannot be following a flag")

        active = "option" if isinstance(x, Option) else "flag"
        for name in x.names:
            if modifiers.setdefault(name, x) is not x:
                raise TypeError(f"{typename} {active} with name {name!r} already exists")

    active = "cardinal"
    greedy = None
    hidden = False
    deprecated = False

    if callable(source):
        # callable form — decode parameters and classify defaults
        try:
            source.parameters = list(inspect.signature(source).parameters.values())
        except ValueError:
            raise TypeError(f"{typename} callback must support signature inspection")
        except AttributeError:
            raise TypeError(f"{typename} must allow external attribute definition")
        for parameter in source.parameters:
            if parameter.default is Parameter.empty:
                raise TypeError(f"{typename} callback parameter {parameter.name!r} must have a default")
            if isinstance(argument := _resolve_argument(parameter.default, parameter), Cardinal):
                _resolve_cardinal(argument, parameter)
            else:
                _resolve_modifier(argument, parameter)
            groups[argument.group] += (argument,)
    elif isinstance(source, Iterable):
        # iterable form — enforce [cardinals..., options..., flags...] boundaries
        for index, object in enumerate(source):
            if isinstance(argument := _resolve_argument(object, index), Cardinal):
                _resolve_cardinal(argument, index)  # type: ignore[arg-type]
            else:
                _resolve_modifier(argument, index)
            groups[argument.group] += (argument,)
    else:
        raise TypeError(f"{typename} first argument must be callable or an iterable of argument-resolvable")

    return dict(
        callback=source if callable(source) else Unset,
        cardinals=cardinals,
        modifiers=modifiers,
    )


def _attach_child(child, parent):
    """
    attach this command as a child of the given parent.

    behavior
    - when parent is Unset, no-op (deferred mounting).
    - otherwise, inserts child into parent's children mapping under child.name.
    - if a child with the same name already exists, raises TypeError.

    parameters
    - child: Command to attach as a child.
    - parent: Command | Unset
      the prospective parent; Unset means “no parent yet”.

    errors
    - TypeError when a sibling with the same name already exists.
    """
    if parent is Unset:
        return
    if object.__getattribute__(parent, "-children").setdefault(child.name, child) is not child:
        raise TypeError(f"{type(child).__name__} parent already has a child named {child.name!r}")


def _process_info(cls, metadata):
    """
    normalize and validate high-level command metadata.

    parameters
    - cls: type used to prefix diagnostics.
    - metadata: dict holding string-or-Unset values for:
      name, descr, usage.

    behavior
    - for each listed field:
      • require str | Unset; otherwise TypeError.
      • when str, dedent and strip; empty after trim raises ValueError.
      • write the normalized value back into metadata, converting Unset via nullify.

    returns
    - None (metadata is updated in place).
    """
    for field, object in map(lambda x: (x, metadata[x]), (
        "name",
        "descr",
        "usage",
    )):
        if not isinstance(object, str | Unset):
            raise TypeError(f"{cls.__name__} {field} must be a string")
        elif isinstance(object, str) and not (object := textwrap.dedent(object).strip()):
            raise ValueError(f"{cls.__name__} {field} must be a non-empty string")
        metadata[field] = nullify(object)


def _process_conflicts(cls, metadata):
    """
    normalize and validate conflicting option/flag groups.

    input shape (metadata["conflicts"])
    - iterable of iterables of strings, where each inner iterable names a set of
      groups that are mutually exclusive.
      example:
        [
          ("output", "stdout"),
          {"verbose", "quiet"},
        ]

    validation
    - outer value must be iterable; otherwise TypeError.
    - each inner value must be iterable and convertible to a set; otherwise TypeError.
    - each inner set must contain at least two group names; otherwise ValueError.
    - each group name must be a non-empty string after trim; otherwise TypeError/ValueError.

    normalization
    - builds a mapping: group_name -> frozenset of other group names that conflict
      with it. The relation is symmetric by construction.
      example:
        {
          "output": frozenset({"stdout"}),
          "stdout": frozenset({"output"}),
          "verbose": frozenset({"quiet"}),
          "quiet": frozenset({"verbose"}),
        }

    result
    - writes the normalized mapping back under the "conflicts" key in the provided
      metadata dict (in-place update). Returns None.

    notes
    - group names must correspond to the grouping keys assigned to specs during
      source processing. Unknown names are not resolved here; this step only
      validates shapes and prepares the adjacency map.
    """
    message = f"{cls.__name__} conflicts must be an iterable of iterables of strings"
    conflicts = defaultdict(frozenset)

    if not isinstance(metadata["conflicts"], Iterable):
        raise TypeError(message)

    for groups in metadata["conflicts"]:
        try:
            groups = set(groups)
        except TypeError:
            raise TypeError(message) from None
        if len(groups) < 2:
            raise ValueError("each conflicting group must have at least two elements")
        for group in groups:
            if not isinstance(group, str):
                raise TypeError(message)
            if not (group := group.strip()):
                raise ValueError(f"{cls.__name__} conflicting groups must be a non-empty strings")
            conflicts[group] |= groups - {group}

    metadata["conflicts"] = conflicts


# Orphan/template mounting state (internal, not part of public API)
#
# _standby
# - purpose: order-preserving buffer of staged “clones” created from a template command
#   when no parent is available yet. These clones are mounted later by include(),
#   after the template itself is mounted.
# - lifecycle:
#   • when cloning a Command with no parent, the clone is appended to _standby[template].
#   • include() drains _standby[template] (if any) immediately after mounting the template,
#     attaching each staged clone as a fresh child.
#
# _parents
# - purpose: tracks the template a staged clone originates from, allowing include() to:
#   • normalize a top-level object to its template (template = _parents.get(obj, obj)),
#   • coalesce and drain the correct _standby bucket,
#   • forget tracking once the object is mounted (entry removed).
# - notes: this bookkeeping enables deterministic “template then its clones” mounting order.
#   It is module-local, mutable, and not thread-safe by design (single-threaded CLI setup).
_standby = defaultdict(list)
_parents = {}


class Command(StorageGuard, metaclass=_CommandType):
    """
    parsed-command node with optional callback and a tree of subcommands.

    responsibilities
    - represent a single command (or subcommand) in a CLI tree.
    - hold argument specifications (cardinals/modifiers), help metadata, and
      runtime flags (fancy/shell/colorful/deferred).
    - parse an input prompt into a namespace, apply conversions/choices, enforce
      conflicts and standalone rules, and dispatch argument/command callbacks.
    - collect faults (warnings/exceptions) and finalize as a single exit point.

    structure
    - name/descr/usage: presentation metadata used by help/UX.
    - groups: Mapping[str, tuple[Cardinal|Option|Flag, ...]]
      specs grouped by help section (e.g., "options", "flags").
    - cardinals: Mapping[str, Cardinal]
      positional arguments keyed by their parameter names.
    - modifiers: Mapping[str, Option|Flag]
      options/flags keyed by every declared alias (e.g., "--opt", "-o").
    - conflicts: Mapping[str, Set[str]]
      mutually-exclusive group adjacency.
    - parent/children: tree topology for subcommands; root() and rootpath()
      provide convenient navigation helpers.

    runtime flags
    - fancy: enable UI embellishments (consumer-defined).
    - shell: favor shell-like UX (e.g., show help on error).
    - colorful: colorize auto-generated help when available.
    - deferred: accumulate faults during parse and report them together.

    common flows
    - include(): discover and attach orphan/template commands via module-glob.
    - command(): define a child command via decorator or factory style.
    - trigger(): route faults immediately or defer them based on settings.
    - __invoke__(): normalize a prompt and parse+dispatch.
    - __parse__(): core parsing loop; see its docstring for details.
    - __finalize__(): drain warnings and raise a grouped CommandExit on errors.

    notes
    - implicit help: a helper flag (-h/--help) is injected when absent and wired
      to __show_help(); helper implies standalone→terminator→nowait.
    - read-only views: public attributes are exposed via the metaclass using view().
    - dynamic behavior: most instances are factory-built dynamic types; subclassing
      such instances is forbidden by the metaclass.
    """
    __fields__ = (
        "name",
        "descr",
        "usage",
        "groups",
        "cardinals",
        "modifiers",
        "conflicts",
        "parent",
        "children",
        "fancy",
        "shell",
        "colorful",
        "deferred",
    )

    @property
    def root(self):
        """
        return the top-most ancestor command (the tree root).

        behavior
        - climb the parent chain until no parent is present.
        - returns self when the command has no parent.

        examples
        - child.root is parent if parent has no parent.
        - root.root is root.
        """
        command, parent = self, self.parent
        # walk up until a node without a parent is found
        while parent:
            command, parent = parent, parent.parent
        return command

    @property
    def rootpath(self):
        """
        return the path from the root command to this command (inclusive).

        shape
        - tuple[Command, ...] ordered from outermost (root) to innermost (self).

        behavior
        - follow parent links toward the root while pre-pending each ancestor.

        examples
        - for root: (root,)
        - for nested: (root, sub, leaf)
        """
        rootpath = deque([command := self])
        # prepend ancestors while climbing to the root
        while command.parent:
            rootpath.appendleft(command := command.parent)
        return tuple(rootpath)

    @property
    def source(self):
        """
        return the underlying callback or a materialized sequence of argument specs.

        behavior
        - call-based command (has a synthesized __call__):
          returns the stored callback object (the function to invoke).
        - spec-based command (defined from an iterable of specs):
          returns a tuple of unique argument specs in declaration/help order:
            • first all cardinals (positional) from self.cardinals.values()
            • then all modifiers (named options/flags) from self.modifiers.values()
          duplicates (same spec object reachable via multiple keys) are emitted once.

        notes
        - returns a concrete value in both cases (never a generator) to avoid
          consumer surprises.
        - iteration order follows insertion order of the underlying dicts.
        """
        if callable(self):
            return object.__getattribute__(self, "-callback")
        items = []
        seen = set()
        for argument in itertools.chain(self.cardinals.values(), self.modifiers.values()):  # type: ignore[arg-type]
            if argument in seen:
                continue
            items.append(argument)
            seen.add(argument)
        return tuple(items)

    def __new__(
            cls,
            source,
            /,
            parent=Unset,
            name=Unset,
            descr=Unset,
            usage=Unset,
            conflicts=Unset,  # Unset to be casted from the template
            *,
            fancy=Unset,
            shell=Unset,
            colorful=Unset,  # this colors the help auto output
            deferred=Unset,
    ):
        """
        build a command node from a callback/spec sequence or clone from a template.

        parameters
        - source: Command | Callable[..., Any] | Iterable[Supports*]
          • Command  → clone the template (preserving metadata unless overridden).
          • Callable → treat function parameters (and defaults) as argument specs.
          • Iterable → ordered sequence of argument specs (cardinals, then options, then flags).
        - parent: Command | Unset
          parent command to attach under. Unset means “no parent yet” (template/orphan).
        - name: str | Unset
          command name. Defaults to function.__name__ for callables, or argv[0] basename.
        - descr: str | Unset
          dedented short description. Defaults to function docstring for callables.
        - usage: str | Unset
          optional usage override (printed by help).
        - conflicts: Iterable[Iterable[str]] | Unset
          mutually-exclusive groups, as an iterable of group-name collections.
          Example: [("output", "stdout"), {"verbose", "quiet"}].
        - fancy: bool (kw-only)
          when true, enable “fancy” output/formatting (consumer-defined).
        - shell: bool (kw-only)
          when true, suppress raising from faults in some contexts and favor
          shell-style UX (e.g., print help on failure).
        - colorful: bool (kw-only)
          when true, colorize auto-generated help output.
        - deferred: bool (kw-only)
          when true, accumulate faults and report them at once during finalize().

        behavior
        - validates parent type and asserts parent has no cardinals (only leaf nodes may carry cardinals).
        - clone path (source is Command):
          • converts the template’s conflicts mapping back to a list-of-iterables form,
            applies overrides, and returns the clone.
          • when parent is Unset, the clone is staged (standby) until include() mounts it.
        - build path (callable/iterable):
          • resolves source into groups/cardinals/modifiers via _process_source().
          • normalizes name/descr/usage and conflicts; boolean flags are stabilized.
          • materializes a dynamic type (factory=True), injects an implicit --help if missing,
            wires callback/fallback/faults, and attaches to the parent if provided.

        notes
        - implicit --help: if absent, a helper flag (-h/--help) is injected and wired to __show_help.
        - colorful/fancy are stored as simple booleans; consumers decide rendering effects.
        - orphan/template lifecycle: see _standby/_parents for staging and mounting order rules.

        errors
        - TypeError if parent is not a Command/Unset.
        - ValueError if parent already has cardinals (non-leaf).
        """
        if not isinstance(parent, Command | Unset):
            raise TypeError(f"{cls.__name__} parent must be a command")
        elif getattr(parent, "cardinals", {}):
            raise ValueError(f"{cls.__name__} parent must not have cardinals")

        if isinstance(source, Command):
            temp = []
            for group, groups in source.conflicts.items():
                temp.append((group, *groups))

            clone = type(source).__base__(
                source.source,
                parent := nullify(parent, source.parent or Unset),
                nullify(name, source.name),
                nullify(descr, source.descr or Unset),
                nullify(usage, source.usage or Unset),
                nullify(conflicts, temp),
                fancy=nullify(fancy, source.fancy or Unset),
                shell=nullify(shell, source.shell or Unset),
                colorful=nullify(colorful, source.colorful or Unset),
                deferred=nullify(deferred, source.deferred or Unset),
            )

            if not parent:
                _standby[template := _parents.get(source, source)].append(clone)
                _parents[clone] = template
            return clone

        metadata = {
            "name": nullify(name, getattr(source, "__name__", os.path.basename(sys.argv[0]))),
            "descr": nullify(descr, inspect.getdoc(source) if callable(source) else None) or Unset,
            "usage": usage,
            "groups": defaultdict(tuple),
            "conflicts": nullify(conflicts, ()),
            "parent": nullify(parent),
            "children": {},
            "fancy": bool(fancy),
            "shell": bool(shell),
            "colorful": bool(colorful),
            "deferred": bool(deferred),
        }
        metadata |= _process_source(cls, source, metadata["groups"])
        _process_info(cls, metadata)
        _process_conflicts(cls, metadata)
        with super().__new__(type(cls)(cls.__name__, (cls,), {}, factory=True, **metadata)) as self:
            if not metadata["modifiers"].keys() & {"-h", "--help"}:
                metadata["modifiers"] |= dict.fromkeys({"-h", "--help"}, flag(
                    "-h", "--help",
                    descr="show this help message and exit",
                    helper=True
                )(self.__show_help))
            setattr(self, "-callback", metadata.pop("callback"))
            setattr(self, "-fallback", Unset)
            for field in cls.__fields__:
                setattr(self, "-" + field, metadata[field])
            setattr(self, "-faults", [])
            _attach_child(self, parent)
        return self

    def __show_help(self):
        stderr = bool(object.__getattribute__(self, "-faults"))
        console = Console(stderr=stderr, style="red" * stderr * self.colorful)

    def fallback(self, fallback, /):
        """
        register a fallback handler for command faults.

        purpose
        - when dispatch/processing raises a command-fault immediately, or when
          faults are deferred and accumulated, this handler is invoked to handle
          them in a centralized way.

        parameters
        - fallback: callable
          a callable that accepts either:
            • a single fault object (exception/warning) when raised immediately, or
            • a non-empty list of fault objects when faults are deferred/aggregated.
          the handler’s return value is ignored; this hook is for side-effects
          such as logging, formatting, or converting faults to exit codes.

        constraints
        - must be callable; attempting to set a non-callable raises TypeError.
        - can be set only once per command; subsequent attempts raise TypeError.

        returns
        - the same callable (enables decorator-style usage).

        examples
        - decorator style:
            @cmd.fallback
            def on_fault(faults):
                # faults is either a single fault or a list of faults
                ...

        - direct registration:
            def on_fault(faults): ...
            cmd.fallback(on_fault)
        """
        if not callable(fallback):
            raise TypeError(f"{type(self).__name__} fallback must be callable")
        if object.__getattribute__(self, "-fallback") is not Unset:
            raise TypeError(f"{type(self).__name__} fallback already set")
        object.__setattr__(self, "-fallback", fallback)
        return fallback

    def command(self, source=Unset, /, *args, **kwargs):
        """
        convenience helper to define a child command under this command.

        usage
        - decorator mode:
            @parent.command(name="child")
            def child(...): ...
          Returns the created child Command and attaches it to 'parent'.

        - factory mode:
            parent.command(callback_or_specs, name="child", ...)
          Mirrors the top-level command(...) API, but automatically sets parent=self.

        parameters
        - source: Unset | Command | Callable | Iterable[Supports*]
          Unset enables decorator mode; otherwise forwarded to command(...).
        - *args, **kwargs: forwarded to command(...), with 'self' injected as parent.

        returns
        - in decorator mode: a function that accepts the callback and returns a child Command
        - in factory mode: the created child Command
        """
        return command(source, self, *args, **kwargs)  # type: ignore[arg-type]

    def include(self, source, /):
        """
        discover and mount orphan/template commands from modules matched by a module-glob.

        purpose
        - import one or more modules, find top-level Command objects that have no parent
          (templates and their staged clones), and mount fresh children under this
          command in deterministic order.

        source pattern (module-glob)
        - dot-separated pattern of module names; supports:
          • per-segment: '*', '?', character classes '[...]' and negation '[!...]'
          • whole-segment: '**' for zero or more segments
        - must start with a concrete leading segment; patterns like "*.pkg" are rejected.
        - examples:
          • "pkg.tools.*"
          • "core.**.tests"
          • "utils.[a-z]*"

        behavior
        - expands source via mglob(source) to concrete module names.
        - imports each module; for every top-level Command with no parent:
          • normalizes to its template (if the object is a staged clone),
          • mounts the template,
          • drains and mounts its staged clones in declaration order.
        - mounting creates fresh children (no reparenting of existing instances).

        parameters
        - source: str
          module-glob string (see above).

        errors
        - TypeError when source is not a string.
        - ValueError when source is empty after trim.
        - RuntimeError when a matched module fails to import.

        notes
        - name collisions under this command raise a TypeError during insertion.
        - results are deterministic: template first, then staged clones in order.
        """
        if not isinstance(source, str):
            raise TypeError("include() argument must be a string")
        elif not (source := source.strip()):
            raise ValueError("include() argument must be a non-empty string")

        for module in mglob(source):
            try:
                module = importlib.import_module(module)
            except ImportError:
                raise RuntimeError(f"include() failed to import {module!r}") from None
            for name, object in inspect.getmembers(module):
                if not isinstance(object, Command) or object.parent:
                    continue

                commands = [template := _parents.get(object, object)] + list(_standby.pop(template, ()))
                for command in commands:
                    if command in _parents:
                        del _parents[command]
                    self.command(command)

    def trigger(self, fault, /, **options):
        """
        dispatch a command fault immediately or defer it for later handling.

        behavior
        - merges context-specific options with the provided overrides and decides:
          • deferred mode (self.deferred is truthy):
            resolve the fault with the current options (when it supports __replace__)
            and append the concrete fault object to the internal fault queue.
          • immediate mode with fallback:
            resolve the fault with the current options and invoke the registered
            fallback handler with a single fault object.
          • immediate mode without fallback:
            forward to the global faults.trigger(fault, **options).

        parameters
        - fault: an object implementing the Triggerable protocol (i.e., exposes
          __replace__(**overrides) -> fault and __trigger__() -> None). Plain
          objects are permitted; when they do not support __replace__, options
          are ignored for that object and it is passed through as-is.
        - **options: override/formatting context (merged with instance context).
          This set may evolve; callers should treat it as an opaque mapping.

        deferred draining
        - when faults are deferred, they are stored as resolved fault objects. A
          later drain routine is expected to deliver a list of these faults to
          the fallback handler in one call, preserving the original order.

        guarantees
        - stored deferred entries are already resolved with the options in effect
          at the time of deferral (no late-bound surprises).
        - the fallback, when present, always receives concrete fault objects:
          a single object in immediate mode, a list of objects on drain.
        """
        options |= {"command": self, "shell": self.shell, "fancy": self.fancy, "colorful": self.colorful}
        if self.deferred:
            if hasattr(fault, "__replace__") and callable(fault.__replace__):
                fault = fault.__replace__(**options)
            object.__getattribute__(self, "-faults").append(fault)
        elif (fallback := object.__getattribute__(self, "-fallback")) is not Unset:
            if hasattr(fault, "__replace__") and callable(fault.__replace__):
                fault = fault.__replace__(**options)
            fallback(fault)
        else:
            if self.shell:
                self.modifiers["--help"]()
            trigger(fault, **options)

    def __resolve_token(self, token):
        """
        parse a single token that looks like a modifier (option/flag) and apply friendly policies.

        goals (beginner-friendly)
        - keep messages lowercase and actionable.
        - avoid hard fails when we can recover or teach (e.g., '--opt=').
        - suggest likely options when users mistype (did you mean '...'?).
        - avoid forcing a '--' end-of-options terminator; prefer context.

        token shape
        - '--name' or '--name=value'
        - '-x' (short) or '-x=value'
        - we do not require '--' to disambiguate values; we only treat tokens as
          modifiers when their 'input' part matches a known name.

        empty inline value policy ('--opt=')
        - flags: always an error (flags never accept values).
        - options:
          • if inline-only: error with a clear hint to provide a value.
          • else: handled later by the collector; here we emit a gentle hint to
            either remove '=' and write a spaced value, or provide an inline value.

        '--' terminator
        - supported elsewhere, but not required; we keep this resolver focused on
          known modifier tokens and user-friendly diagnostics.
        """
        match = re.fullmatch(r"(?P<input>--?[^\W\d_](?:-?[^\W_]+)*)(=(?P<param>[^\r\n]*))?", token)

        if not match:
            # malformed modifier-like token (e.g., stray '-', bad unicode boundary, etc.)
            # keep it gentle and point to help with concrete examples.
            self.trigger(MalformedTokenError(
                f"argument %s: invalid format" % _ordinal(self.index, at=True),
                hint="use -name, --name or --name=param"
            ))
            raise Skipped

        input = match["input"]   # the option/flag name part (e.g., '--output')
        param = match["param"]   # the inline value if provided (after '=')

        # unknown modifier name: suggest a close match (top-1) when available; otherwise point to help
        if input not in self.modifiers.keys():
            suggestions = difflib.get_close_matches(input, self.modifiers.keys(), n=3, cutoff=0.85)

            try:
                hint = f"did you mean %r?" % suggestions[0]
            except IndexError:
                hint = "run '%s --help' to see valid options" % " ".join(command.name for command in self.rootpath)

            self.trigger(UnknownModifierError(
                "unknown option or flag %r %s" % (input, _ordinal(self.index, at=True)),
                hint=hint
            ))
            raise Skipped

        # inline '=' present but no value typed (e.g., '--mode=')
        # flags never accept values; options get a friendly hint (actual recovery/consumption happens in the collector).
        if isinstance(param, str) and not param:
            modifier = self.modifiers[input]
            if isinstance(modifier, Option):
                # do not hard-fail here: beginners often type '=' by habit; teach the two correct forms
                self.trigger(EmptyInlineParamWarning(
                    f"option %r %s has an empty inline param" % (input, _ordinal(self.index, at=True)),
                    hint="either remove '=' (use '--%(name)s param') or provide a param (e.g., '--%(name)s=param')" % {"name": input}
                ))
            elif isinstance(modifier, Flag):
                # flags are strict: any '=...' is not allowed
                self.trigger(FlagTakesNoParamError(
                    f"flag %r %s does not take a param" % (input, _ordinal(self.index, at=True)),
                    hint="remove the param (use '%s')" % input
                ))

        return input, param

    def __parsearg(self, argument, input, tokens, *, inline=False):
        """
        parse and convert a single argument (cardinal or modifier), with friendly faults.

        flow
        - arity collection:
          • Ellipsis: consume the rest
          • None/'?': optional single
          • '*','+': variadic
          • int: exact count
          validates and raises helpful faults on missing/extra/insufficient params.
        - conversion:
          • applies argument.type to each collected value
          • forwards CommandWarning via trigger(..., ord=..., sub=...)
          • wraps non-command warnings into ExternalConverterWarning
          • wraps unexpected exceptions into UncastableParamError
          • preserves user CommandException types but enriches context
        - choices:
          • validates against argument.choices (scalar and variadic)
          • emits InvalidChoiceError listing allowed values
        - return:
          • nullifies Unset to argument.default for unified downstream usage
        """
        index = self.index

        peekable = lambda: tokens and not tokens[0].startswith("-")
        if (nargs := argument.nargs) is Ellipsis:
            parsed = []
            while tokens:
                parsed.append(tokens.popleft())
                self.index += 1
        elif not nargs or nargs == "?":
            if not nargs and not peekable():
                if isinstance(argument, Option):
                    if argument.inline:
                        hint = "write it as '%s=param'" % input
                    else:
                        hint = "write it as '%s param' or '%s=param'" % (input, input)
                    self.trigger(MissingParamError(
                        "option %r %s needs a param" % (input, _ordinal(index - 1, at=True)),
                        hint=hint
                    ))
                else:
                    self.trigger(MissingParamError(
                        "positional argument %s needs a param" % _ordinal(index, frm=True),
                        hint="write a param after it"
                    ))
            elif inline and len(tokens) > 1:
                if argument.inline:
                    hint = "use one inline param like '%s=param'" % input
                else:
                    hint = "use one inline param like '%s=param' or use spaced form '%s p1 p2'" % (input, input)
                self.trigger(TooManyInlineParamsError(
                    "option %r %s takes at most one inline param" % (input, _ordinal(index - 1, at=True)),
                    hint=hint
                ))
            parsed = tokens.popleft() if peekable() else Unset
            self.index += 1 * (not inline and parsed is not Unset)
        elif nargs in ("*", "+"):
            parsed = []
            while peekable():
                parsed.append(tokens.popleft())
                self.index += 1 * (not inline)
            if nargs == "+" and not parsed:
                if argument.inline:
                    hint = "pass one or more params like '%s=p1,p2,p3'" % input
                else:
                    hint = "pass one or more params like '%s p1 p2 p3' or '%s=p1,p2,p3'" % (input, input)
                self.trigger(AtLeastOneParamRequiredError(
                    "option %r %s needs at least one param" % (input, _ordinal(index - 1, at=True)),
                    hint=hint
                ))
        else:
            parsed = []
            while peekable() and len(parsed) < nargs:
                parsed.append(tokens.popleft())
                self.index += 1 * (not inline)
            if len(parsed) != nargs:
                if isinstance(argument, Option):
                    if argument.inline:
                        example = "=%s" % ",".join("p%d" % i for i in range(1, int(nargs) + 1))
                        hint = "pass exactly %d param%s like '%s%s'" % (nargs, "" if nargs == 1 else "s", input, example)
                    else:  # This allow both ways, but only one at time (two at time is impossible the parser doesn't detect it)
                        spaced = " ".join("p%d" % i for i in range(1, int(nargs) + 1))
                        inline_example = "=%s" % ",".join("p%d" % i for i in range(1, int(nargs) + 1))
                        hint = "pass exactly %d param%s like '%s %s' or '%s%s'" % (nargs, "" if nargs == 1 else "s", input, spaced, input, inline_example)
                    self.trigger(NotEnoughParamsError(
                        "option %r %s needs %d param%s" % (input, _ordinal(index - 1, at=True), nargs, "" if nargs == 1 else "s"),
                        hint=hint
                    ))
                else:
                    self.trigger(NotEnoughParamsError(
                        "positional argument %s needs %d param%s" % (_ordinal(index, frm=True), nargs, "" if nargs == 1 else "s"),
                        hint="write the remaining param%s after it" % ("" if nargs == 1 else "s")
                    ))

            if inline and tokens:
                if argument.inline:
                    hint = "use only inline params like '%s=p1,p2'" % input
                else:
                    hint = "use inline once like '%s=p1' or use spaced form '%s p1 p2', not both" % (input, input)
                self.trigger(TooManyInlineParamsError(
                    "option %r %s got extra params after inline form" % (input, _ordinal(index - 1, at=True)),
                    hint=hint
                ))

        variadic = isinstance(parsed, list)

        def _ord(pos):
            text = _ordinal(
                pos,
                at=isinstance(argument, Option),
                frm=isinstance(argument, Cardinal),
                sub=inline
            )
            if inline:
                text = f"%s (option %s)" % (text, _ordinal(index - 1, at=True))
            return text

        if variadic:
            offset = 0
            for pos, obj in enumerate(parsed, start=1 if inline else index):
                try:
                    with catch_warnings(record=True) as warnings:
                        parsed[offset] = argument.type(obj)
                        offset += 1
                    for warning in warnings:
                        if isinstance(warning, CommandWarning):
                            self.trigger(warning, ord=_ord(pos), sub=inline)
                        else:
                            if isinstance(argument, Option):
                                message = "conversion of option %r %s: %s" % (input, _ord(pos), getattr(warning, "message", str(warning)))
                                hint = "check the value"
                            else:
                                message = "conversion of positional %s: %s" % (_ord(pos), getattr(warning, "message", str(warning)))
                                hint = "check the value"
                            self.trigger(ExternalConverterWarning(
                                message,
                                hint=hint
                            ), original=warning)
                except CommandException as exception:
                    self.trigger(exception, ord=_ord(pos), sub=inline)
                except Exception as exception:
                    if isinstance(argument, Option):
                        message = "conversion of option %r %s: %s" % (input, _ord(pos), str(exception))
                        hint = "check the value"
                    else:
                        message = "conversion of positional %s: %s" % (_ord(pos), str(exception))
                        hint = "check the value"
                    self.trigger(UncastableParamError(
                        message,
                        hint=hint
                    ), original=exception)

        elif parsed is not Unset:
            pos = index if not inline else 1
            try:
                with catch_warnings(record=True) as warnings:
                    parsed = argument.type(parsed)
                for warning in warnings:
                    if isinstance(warning, CommandWarning):
                        self.trigger(warning, ord=_ord(pos), sub=inline)
                    else:
                        if isinstance(argument, Option):
                            message = "conversion of option %r %s: %s" % (input, _ord(pos), getattr(warning, "message", str(warning)))
                            hint = "check the value"
                        else:
                            message = "conversion of positional %s: %s" % (_ord(pos), getattr(warning, "message", str(warning)))
                            hint = "check the value"
                        self.trigger(ExternalConverterWarning(
                            message,
                            hint=hint
                        ), original=warning)
            except CommandException as exception:
                self.trigger(exception, ord=_ord(pos), sub=inline)
            except Exception as exception:
                if isinstance(argument, Option):
                    message = "conversion of option %r %s: %s" % (input, _ord(pos), str(exception))
                    hint = "check the value"
                else:
                    message = "conversion of positional %s: %s" % (_ord(pos), str(exception))
                    hint = "check the value"
                self.trigger(UncastableParamError(
                    message,
                    hint=hint
                ), original=exception)

        if not argument.choices:
            return nullify(parsed, argument.default)

        if variadic:
            for pos, obj in enumerate(parsed, start=1 if inline else index):
                if obj in argument.choices:
                    continue
                if isinstance(argument, Option):
                    message = "option %r %s has an invalid choice %r" % (input, _ord(pos), obj)
                else:
                    message = "positional %s has an invalid choice %r" % (_ord(pos), obj)
                self.trigger(InvalidChoiceError(
                    message,
                    hint="allowed values are: {%s}" % ", ".join(map(repr, argument.choices))
                ))
        elif parsed is not Unset and parsed not in argument.choices:
            if isinstance(argument, Option):
                message = "option %r %s has an invalid choice %r" % (input, _ord(self.index if not inline else 1), parsed)
            else:
                message = "positional %s has an invalid choice %r" % (_ord(self.index), parsed)
            self.trigger(InvalidChoiceError(
                message,
                hint="allowed values are: {%s}" % ", ".join(map(repr, argument.choices))
            ))

        return nullify(parsed, argument.default)

    def __finalize(self, *, help=True):
        """
        internal: drain collected faults, optionally show help, and raise a grouped exit.

        purpose
        - Deliver all queued warnings immediately.
        - Aggregate collected command exceptions into a CommandExit and trigger it,
          allowing a single failure path for multiple parse/conversion issues.
        - In shell mode, optionally show help before raising when helpful.

        parameters
        - help: bool (keyword-only)
          when True and shell-mode is active, attempt to show the built-in help
          (via the implicit --help handler) before triggering the exit group.

        behavior
        - partitions the internal faults buffer into warnings and exceptions.
        - triggers warnings first (non-blocking).
        - constructs a CommandExit(exceptions) and:
          • if shell and help is True, invokes the help handler,
          • triggers the exit group (raising unless suppressed elsewhere).
        - ValueError raised by CommandExit construction is ignored (treated as
          “no exceptions to report”).

        notes
        - this method does not clear the internal faults list; the caller governs
          lifecycle and subsequent reuse/return paths.
        - help invocation relies on the implicit --help being present (injected
          during command construction when absent).
        """
        exceptions = []
        warnings = []
        for fault in object.__getattribute__(self, "-faults"):
            if isinstance(fault, CommandException):
                exceptions.append(fault)
            else:
                warnings.append(fault)

        for warning in warnings:
            trigger(warning, **{"command": self, "shell": self.shell, "fancy": self.fancy, "colorful": self.colorful})

        try:
            exit = CommandExit(exceptions)
            if self.shell and help:
                self.modifiers["--help"]()
            trigger(exit, **{"command": self, "shell": self.shell, "fancy": self.fancy, "colorful": self.colorful})
        except ValueError:
            pass

    def __parse(self, tokens, index=1, /, *, faults=()):
        """
        internal: parse a command line for this command node and dispatch callbacks.

        parameters
        - tokens: deque[str] | Iterable[str]
          stream of tokens to consume (typically a deque). The method mutates it
          by popping consumed items from the left.
        - index: int (1-based)
          logical position counter for user-facing messages. Increments as tokens
          are consumed so diagnostics can say “at the Nth position”.
        - faults: list[CommandException | CommandWarning]
          external fault buffer (used when delegating to subcommands). The current
          command’s fault storage is rebound to this list so all raised/queued
          faults end up in a single place for finalize().

        side effects
        - self.namespace: dict[str, Any]
          populated with parsed values keyed by:
            • cardinals: their declared names (from the callback signature)
            • options/flags: all declared aliases (each name points to the same value)
        - self.tokens: set to the provided token source and advanced during parsing.
        - self.index: advanced as positional progress indicator for messages.
        - internal faults buffer is extended with generated faults; finalize() drains it.

        flow (high level)
        - while there are tokens:
          • resolve next token as:
            – a modifier (option/flag) when it starts with '-' (unless a greedy
              cardinal is next), or
            – a subcommand when children exist and no arguments were parsed yet, or
            – a positional (cardinal) otherwise.
          • deprecation: emit a DeprecatedArgumentWarning for deprecated args.
          • duplicate: emit DuplicateModifierError when a modifier repeats improperly.
          • collect params according to argument.nargs; convert via argument.type
            and surface warnings/exceptions with contextual, beginner-friendly messages.
          • group conflicts: check against previously-seen groups; emit ConflictingGroupError.
          • standalone: if this argument is standalone=True and others are present
            (or more tokens remain), emit StandaloneOnlyError.
          • nowait: if argument.nowait, invoke the argument callback immediately.
            If argument.terminator, call finalize() and return early.
        - when tokens remain unparsed:
          • emit UnparsedInputError with a tailored hint depending on whether
            delegation to a subcommand was attempted (tried) and whether children exist.

        post-collection
        - invoke callbacks for any remaining arguments that were not called in
          nowait phase (in a stable order based on the spec).
        - for any missing cardinals (no more tokens and some cardinals unfilled),
          emit MissingParamError / AtLeastOneParamRequiredError / NotEnoughParamsError
          using an ordinal based on the missing cardinal index (not the exhausted self.index),
          then seed namespace with defaults.

        finalize and dispatch
        - call finalize() to emit warnings and raise CommandExit when exceptions
          were collected (or print help when shell=True).
        - if this command has no callback (__call__ synthesized is unset), return
          the namespace (with defaults for all modifier aliases); otherwise, build
          positional args/kwargs based on the original callback signature and invoke it.

        returns
        - dict[str, Any] when there is no callback to invoke (leafless/spec-driven commands).
        - None when a callback is present and has been invoked.
        """
        old = object.__getattribute__(self, "-faults")
        old[::] = faults
        faults = old

        self.namespace = {}
        self.tokens = tokens
        self.index = index

        conflicts = defaultdict(frozenset, self.conflicts)  # type: ignore[arg-type]
        groups = set()
        calls = set()

        cardinals = deque(self.cardinals.keys())
        tried = False
        while self.tokens:
            token = self.tokens.popleft().strip()

            if token.startswith("-") and not (cardinals and self.cardinals[cardinals[0]].nargs is Ellipsis):
                try:
                    input, param = self.__resolve_token(token)
                except Skipped:
                    continue
                argument = self.modifiers[input]
                self.index += 1  # count the names as an argument to keep consistency with the parameters if apply
            elif self.children and not self.namespace:
                try:
                    return self.children[token].__parse(self.tokens, self.index + 1, faults=faults)  # NOQA: Owned Attribute
                except KeyError:
                    suggestions = difflib.get_close_matches(token, self.children.keys(), n=5, cutoff=0.85)
                    exception = UnknownCommandError if not self.parent else UnknownSubcommandError
                    cmdtype = "command" if not self.parent else "subcommand"
                    tried = True

                    try:
                        hint = f"did you mean %r?" % suggestions[0]
                    except IndexError:
                        hint = f"run '%s --help' to see available %ss" % (" ".join(command.name for command in self.rootpath), cmdtype)

                    self.trigger(exception(
                        "unknown %s %r %s" % (cmdtype, token, _ordinal(self.index, at=True)),
                        hint=hint
                    ))
                break
            else:
                try:
                    argument = self.cardinals[input := cardinals.popleft()]
                    param = Unset
                except IndexError:
                    cmdtype = "command" if self.parent is self.root else "subcommand"
                    if self.cardinals:
                        hint = "all positional arguments were already parsed"
                    else:
                        hint = f"%s {self.name} does not take positional arguments" % cmdtype
                    self.trigger(TooManyPositionalsError(
                        f"unexpected positional argument %r %s" % (token, _ordinal(self.index, at=True)),
                        hint=hint
                    ))
                    self.index += 1
                    continue
                self.tokens.appendleft(token)

            if argument.deprecated:
                if isinstance(argument, Cardinal):
                    message = "use of positional argument %s is deprecated" % _ordinal(self.index, at=True)
                    hint = "consider removing it or using a supported alternative"
                else:
                    argtype = "option" if isinstance(argument, Option) else "flag"
                    message = "use of %s %r %s is deprecated" % (argtype, input, _ordinal(self.index, at=True))
                    hint = "consider using a supported alternative"
                self.trigger(DeprecatedArgumentWarning(message, hint=hint))

            if input in self.namespace:
                argtype = "option" if isinstance(argument, Option) else "flag"

                if argtype == "flag":
                    hint = "use this flag once only"
                elif isinstance(argument, Option) and (
                    argument.nargs in ("*", "+") or isinstance(argument.nargs, int) and argument.nargs > 1
                ):
                    hint = "pass multiple params after one option like '%s p1 p2'" % input
                else:
                    hint = "use this option once with one param like '%s param'" % input

                self.trigger(DuplicateModifierError(
                    "duplicate %s %r %s" % (argtype, input, _ordinal(self.index, at=True)),
                    hint=hint
                ))

            if isinstance(argument, Cardinal):
                index = self.index
                self.namespace[input] = self.__parsearg(argument, input, self.tokens)
            elif isinstance(argument, Option):
                index = self.index - 1
                if param and os.pathsep in param:
                    splitter = os.pathsep
                else:
                    splitter = ":" if param and ":" in param else ","

                tokens = deque(param.split(splitter)) if param else self.tokens
                if argument.inline and not param:
                    self.trigger(InlineParamRequiredError(  # Even it fails, it can be treated as a soft error
                        "option %r %s needs an inline param" % (input, _ordinal(self.index, at=True)),
                        hint="write it as '%s=param'" % input
                    ))
                self.namespace.update(dict.fromkeys(argument.names, self.__parsearg(argument, input, tokens, inline=bool(param))))
            elif isinstance(argument, Flag):
                index = self.index - 1
                self.namespace.update(dict.fromkeys(argument.names, True))
            else:
                raise RuntimeError("unexpected argument type")

            if conflicts := self.conflicts[group := argument.group] & groups:
                # report using index; at = not a cardinal, frm = cardinal
                pos = _ordinal(
                    index,
                    at=not isinstance(argument, Cardinal),
                    frm=isinstance(argument, Cardinal)
                )
                # build a concise, friendly message
                if isinstance(argument, Option):
                    subject = "option %r %s" % (input, pos)
                elif isinstance(argument, Flag):
                    subject = "flag %r %s" % (input, pos)
                else:
                    subject = "positional argument %s" % pos
                self.trigger(ConflictingGroupError(
                    "%s conflicts with group%s %s" % (
                        subject,
                        "" if len(conflicts) == 1 else "s",
                        ", ".join(sorted(conflicts))
                    ),
                    hint="remove one of the conflicting arguments or use them separately"
                ))
            groups.add(group)

            if getattr(argument, "standalone", False) and (self.namespace.keys() - argument.names or self.tokens):
                subject = ("option %r %s" if isinstance(argument, Option) else "flag %r %s") % (input, _ordinal(index, at=True))
                if argument.helper:
                    message = "%s must be used alone" % subject
                    hint = "run it without other arguments"
                else:
                    message = "%s must be the only argument" % subject
                    hint = "remove other arguments and run it alone"
                self.trigger(StandaloneOnlyError(message, hint=hint))

            if argument.nowait:
                if isinstance(argument, Flag):
                    argument()
                elif not argument.nargs or argument.nargs == "?":
                    argument(self.namespace[input])
                elif argument in ("*", "+", Ellipsis) or isinstance(argument, int):
                    argument(*self.namespace[input])

                if getattr(argument, "terminator", False):
                    self.__finalize(help=input not in self.modifiers["--help"].names)
                    return

                calls.update(getattr(argument, "names", (input,)))

        if self.tokens:  # This means that there's unparsed arguments
            first = self.tokens[0]
            where = _ordinal(self.index, at=True)
            cmdtype = "command" if not self.parent else "subcommand"

            if not self.children:
                # no delegation possible here; leftover input couldn't be parsed
                message = "could not parse the remaining input starting at %r %s" % (first, where)
                hint = "run '%s --help' to see valid arguments for this %s" % (
                    " ".join(command.name for command in self.rootpath), cmdtype
                )
            elif tried:
                # we attempted to delegate but nothing matched; input remains unparsed
                message = "could not parse the remaining input starting at %r %s; no subcommand matched" % (first,
                                                                                                            where)
                hint = "run '%s --help' to see available subcommands" % (
                    " ".join(command.name for command in self.rootpath)
                )
            else:
                # children exist but user provided extra/unknown tokens instead of a subcommand or valid args
                message = "could not parse the remaining input starting at %r %s for this %s" % (first, where, cmdtype)
                hint = "run '%s --help' to see how to use this %s" % (
                    " ".join(command.name for command in self.rootpath), cmdtype
                )

            self.trigger(UnparsedInputError(message, hint=hint))


        for input, argument in ChainMap(self.cardinals, self.modifiers).items():  # type: ignore[arg-type]
            if input not in self.namespace or input in calls:  # No need to redundancy check, calls is updated with all names
                continue
            if isinstance(argument, Flag):
                argument()
            elif not argument.nargs or argument.nargs == "?":
                argument(self.namespace[input])
            elif argument in ("*", "+", Ellipsis) or isinstance(argument, int):
                argument(*self.namespace[input])  # NOQA
            calls.update(getattr(argument, "names", (input,)))

        offset = len(self.cardinals) - len(cardinals)
        while cardinals:
            argument = self.cardinals[input := cardinals.popleft()]  # type: ignore[misc]

            if not (nargs := argument.nargs):
                position = _ordinal(offset + 1)
                self.trigger(MissingParamError(
                    "missing %s positional argument needs a param" % position,
                    hint="write a param after it"
                ))
            elif nargs == "+":
                position = _ordinal(offset + 1)
                self.trigger(AtLeastOneParamRequiredError(
                    "missing %s positional argument needs at least one param" % position,
                    hint="write one or more params after it"
                ))
            elif isinstance(nargs, int):
                position = _ordinal(offset + 1)
                self.trigger(NotEnoughParamsError(
                    "missing %s positional argument needs %d param%s" % (position, nargs, "" if nargs == 1 else "s"),
                    hint="write the remaining param%s after it" % ("" if nargs == 1 else "s")
                ))
            self.namespace[input] = argument.default
            offset += 1

        self.__finalize()


        if not (callback := object.__getattribute__(self, "-callback")):
            namespace = self.namespace
            del self.namespace
            del self.tokens
            del self.index

            for argument in self.modifiers.keys():
                for name in argument.names:  # type: ignore[attr-defined]
                    namespace.setdefault(name, argument.default)  # type: ignore[attr-defined]

            return namespace

        args = ()
        for parameter in filter(lambda x: x.kind is not Parameter.KEYWORD_ONLY, callback.parameters):
            args += self.namespace.get(next(iter(getattr(parameter.default, "names", (parameter.name,)))), parameter.default.default),

        kwargs = {}
        for parameter in filter(lambda x: x.kind is Parameter.KEYWORD_ONLY, callback.parameters):
            kwargs[parameter.name] = self.namespace.get(next(iter(parameter.default.names)), False)


        del self.namespace
        del self.tokens
        del self.index
        callback(*args, **kwargs)

    def __invoke__(self, prompt=Unset, /):
        """
        internal: entry point to run this command with a prompt.

        parameters
        - prompt: Unset | str | Iterable[str]
          • Unset      → use sys.argv[1:]
          • str        → split with shlex.split(prompt)
          • Iterable   → consume as-is after validating all items are strings

        behavior
        - normalizes the prompt into a list of tokens and delegates to __parse(...)
          with a fresh deque so tokens can be consumed from the left.

        errors
        - TypeError when prompt is neither a string nor an iterable of strings.

        returns
        - dict[str, Any] | None
          • dict when the command has no callback (spec-driven; returns namespace)
          • None when a callback is present and has been invoked
        """
        if prompt is Unset:
            tokens = sys.argv[1:]
        elif isinstance(prompt, str):
            tokens = shlex.split(prompt)
        elif isinstance(prompt, Iterable):
            tokens = list(prompt)
            if any(not isinstance(token, str) for token in tokens):
                raise TypeError("__invoke__() argument must be a string or an iterable of strings")
        else:
            raise TypeError("__invoke__() argument must be a string or an iterable of strings")
        return self.__parse(deque(tokens))  # type: ignore[attr-defined]


def command(source=Unset, /, *args, **kwargs):
    """
    decorator/factory for building Command objects.

    usage
    - as a decorator (no positional 'source'):
        @command(name="tool")
        def run(...): ...
      The decorated callable becomes the command callback. The decorator returns
      a Command instance.

    - as a factory (explicit source is a callable/specs/command):
        cmd = command(callback_or_specs, parent=..., name=..., conflicts=..., ...)
        • callable → parameters/defaults are interpreted as argument specs
        • iterable of specs → [cardinals..., options..., flags...]
        • Command → clone from a template, applying overrides

    parameters
    - source: Unset | Command | Callable | Iterable[Supports*]
      Unset means “decorator mode”; otherwise it is passed to Command.__new__.
    - *args, **kwargs: forwarded to Command(...) (e.g., parent, name, descr, usage,
      conflicts, fancy, shell, colorful, deferred).

    returns
    - in decorator mode: a function that accepts the callback and returns a Command
    - in factory mode: a Command instance
    """
    @rename("command")
    def decorator(source):
        if decorating and not callable(source) and not isinstance(source, Command):
            raise TypeError("@command() must be applied to a callable or a command")
        return Command(source, *args, **kwargs)
    return decorator(source) if not (decorating := source is Unset) else decorator


def invoke(x, prompt=Unset, /):
    """
    convenience entry point to run a command-like object.

    parameters
    - x: Command | Invocable | Callable
      • Command/Invocable → call its __invoke__(prompt) (or __invoke__() when prompt is Unset).
      • Callable          → wrapped into a Command implicitly (fallback; supported but not intended).
    - prompt: Unset | Any
      forwarded to __invoke__. When Unset, the callee decides (typically sys.argv[1:]).

    behavior
    - if x exposes __invoke__, delegate to it directly.
    - otherwise, attempt to wrap x as a command (command(x)) and invoke that.
      This fallback is allowed, but not intended for long-term use.

    errors
    - TypeError when x is neither a command-like object nor a callable suitable for wrapping.

    returns
    - the return value of __invoke__ (dict[str, Any] | None) depending on the command shape.
    """
    if hasattr(x, "__invoke__"):
        return x.__invoke__(prompt) if prompt is not Unset else x.__invoke__()
    try:  # Is supported this fallback, but not intended
        return invoke(command(x), prompt)
    except Exception:
        raise TypeError(f"invoke() first argument must be a callable or a command") from None

__all__ = (
    "Command",
    "command",
    "invoke"
)
