# Argonaut

Argonaut is a friendly, typed toolkit for building modern command‑line interfaces. It lets you declare commands with decorators or compact iterables, parse argv with minimal boilerplate, and present rich, polished help and diagnostics.

Highlights
- Zero‑friction API: define commands with decorators or a tiny iterable schema.
- Subcommands where they belong: attach subcommands to the parent right next to the code that uses them.
- First‑class argument types: positionals (Cardinal), options with values (Option), and boolean flags (Flag).
- Smart UX: helper switches like `-h/--help` and `-v/--version` are auto‑injected.
- Beautiful output: rich, styled help and readable warnings/errors in shell mode.
- Fully typed: ships with `.pyi` stubs and a curated public API for great IDE support.

Installation
```bash
pip install argonaut
```
Quick start (decorators)
```python
from argonaut import command, Cardinal, Option, Flag, invoke

@command(name="cli", descr="Demo CLI")
def cli(
    file: str = Cardinal("FILE", descr="Input file path"),
    /,
    *,
    mode: str = Option("--mode", choices=("fast", "safe"), default="fast", descr="Execution mode"),
    verbose: bool = Flag("-v", "--verbose", descr="Enable verbose logging"),
) -> None:
    print(f"file={file!r} mode={mode!r} verbose={verbose}")

if __name__ == "__main__":
    invoke(cli)  # parses sys.argv[1:] and calls cli(...)
```
- Show help
```bash
python app.py --help
```
- Run
```bash
python app.py data.csv --mode safe -v
```
Subcommands

```python
from argonaut import command, Cardinal, Option, Flag, invoke

@command(name="tool", descr="Multi-command tool")
def tool() -> None:
    pass  # root command (subcommands do the real work)

@tool.command(name="build", descr="Build an artifact")
def build(
    target: str = Cardinal("TARGET", descr="What to build"),
    /,
    *,
    debug: bool = Flag("--debug", descr="Enable debug output"),
) -> None:
    print(f"Building {target!r}; debug={debug}")

@tool.command(name="deploy", descr="Deploy an artifact")
def deploy(
    env: str = Option("--env", choices=("dev", "staging", "prod"), default="dev", descr="Target environment"),
) -> None:
    print(f"Deploying to {env!r}")

if __name__ == "__main__":
    invoke(tool)
```

- The help for the root shows a commands table:
  - build — Build an artifact
  - deploy — Deploy an artifact

Iterable style (schema returns a namespace)

```python
from argonaut import command, Cardinal, Option, Flag, invoke

cli = command([
    Cardinal("FILE", descr="Input file"),
    Option("--mode", choices=("fast", "safe"), default="fast", descr="Execution mode"),
    Flag("--verbose", "-v", descr="Enable verbose output"),
])

ns = invoke(cli, "data.csv --mode=fast -v")
assert ns["FILE"] == "data.csv"
assert ns["--mode"] == "fast"
assert ns["--verbose"] is True
```

Ergonomics in practice
- Decorator vs iterable
  - Use decorators when you have a handler function to run.
  - Use iterables when you want a quick schema that returns a parsed namespace.
- Attach subcommands via `@parent.command(name="...")` near the parent code for clarity.
- One style per command is recommended, but you can mix styles across different commands.

Deep dive: arguments
- Cardinal: positional values with `nargs` support:
  - `None` → single value; `"?"` → optional single; `"*"`/`"+"` → list; `int>=1` → exact list size.
- Option: named option that accepts values; supports `choices`, `default`, `explicit=True` for attached values (`--opt=value`).
- Flag: named switch without values (boolean).
- Each spec has `descr` for help text and optional `group` for grouped display.

Help, styling, and UX
- Help is auto‑generated and includes:
  - usage line (derived when not provided),
  - description,
  - positional arguments,
  - options and flags,
  - commands table (subcommands with descriptions),
  - notes/epilog (if provided).
- Shell mode can render fancy, colorful help and diagnostics. You can control:
  - `fancy` (panel layout), `colorful` (colored text), and `styles` (named style map).
- Helper switches:
  - `-h/--help` prints help,
  - `-v/--version` prints version.
  They’re added automatically if you haven’t defined them.

Invoking commands

```python
from argonaut import invoke

# Default: parse sys.argv[1:]
invoke(cmd)

# String: split with shlex
invoke(cmd, "--mode safe -v")

# Iterable[str]: tokens are used as-is
invoke(cmd, ["--mode", "safe", "-v"])
```

Subcommand discovery (optional)
- You can dynamically attach orphan commands from modules when organizing large CLIs into packages.

Typed and IDE‑friendly
- Ships with `.pyi` stubs and a curated `__all__` so editors can surface exactly the right things.
- The public API is stable and intentionally small.

Frequently asked questions
- Can I combine decorator and iterable for the same command?
  - Prefer one style per command for clarity. Mixing across different commands is fine.
- What happens when a command has no handler?
  - `invoke(...)` returns a `dict[str, Any]` namespace.
- How do I make an option require attached values?
  - Use `explicit=True` on the Option (`--name=value`).

Example: nested subcommands with different styles
```python
from argonaut import command, Cardinal, Option, Flag, invoke

@command(name="root", descr="Root command")
def root() -> None:
    pass

# Decorator-based child with handler
@root.command(name="sum", descr="Sum integers")
def sumcmd(
    nums: list[int] = Cardinal("N", nargs="+", type=int, descr="Integers to sum"),
    /,
    *,
    verbose: bool = Flag("-v", "--verbose", descr="Verbose output"),
) -> None:
    total = sum(nums)
    if verbose:
        print(f"Summing {nums} = {total}")
    else:
        print(total)

# Iterable-based child (returns namespace)
listcmd = root.command([
    Option("--limit", type=int, default=10, descr="Max items to list"),
    Flag("--json", descr="Emit JSON"),
])

if __name__ == "__main__":
    invoke(root)
```

Project status
- Early alpha. The API aims to remain intuitive while continuing to evolve with user feedback.

License
- MIT

Thanks for trying Argonaut! If you have questions or ideas, please open an issue or start a discussion—happy to help.
