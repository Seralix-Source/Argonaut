# Argonaut · Empathic CLI framework (v1.0.0)

Argonaut is a lightweight framework for building **humane CLIs** with clear, empiric-learning error reports. It is the **cuna (cradle) of the Seralix CLI**: a place to stabilize ideas, conventions, and fault reporting before they are absorbed into Seralix itself.

* short, lowercase messages
* soft punctuation
* position-first explanations
* colorful panels via [Rich]

> [!NOTE]
> Argonaut will be **deprecated** when Seralix CLI reaches its own **v1.0.0**. 
> Until then, it is the official reference implementation of the Seralix CLI style.
> (The repository will be removed after the 1.0.0 release.)

---

## Install

```bash
pip install git+https://github.com/seralix-source/argonaut.git
```

**Requires:** Python ≥ 3.12 (built and tested on 3.13.5)

---

## Quick start

Define specs with decorators, wire them into your command **as parameter defaults**, then call `invoke(main)`.

```python
# app.py
# expected to be imported as `from argonaut import *`
from argonaut.commands import command, invoke
from argonaut.arguments import cardinal, option, flag

@cardinal("FILE", type=str, descr="input file")
def in_file(path: str): ...

@option("-t", "--threads", type=int, nargs="?", metavar="N", descr="worker threads")
def threads_opt(n: int | None): ...

@flag("-v", "--verbose", descr="chatty mode")
def verbose_flag(): ...

@command(name="main", descr="demo tool", shell=True, fancy=True, colorful=True, deferred=True)
def main(
        file=in_file,          # cardinal 
        /,                     # separator for positional-only
        threads=threads_opt,   # option
        *,                     # separator for keyword-only
        verbose=verbose_flag,  # flag
):
    print(f"file={file!r} threads={threads!r} verbose={verbose!r}")

if __name__ == "__main__":
    # Note see Command.include(source[, *, propagate]) for more ways of create delegated commands.
    invoke(main)
```

Run it:

```bash
python app.py --help
python app.py --threads=4 --verbose data.txt
```

---

## Arguments API

**Specs**

* `Cardinal[T]` — positional value
* `Option[T]`   — named value (with aliases, inline or spaced forms)
* `Flag`        — presence only

**Decorators**

* `@cardinal(...)`
* `@option(...)`
* `@flag(...)`

These return spec objects you bind as **defaults** in your command signature.

---

## Help & version renderers

Compact help and version views, optimized for **narrow terminals**. Styling keys can be overridden from `__main__`:

```python
__prog__ = "APP"
__styles__ = {
  "prog-name": "bold #E6E6F0",
  "code": "bold #00E5FF",
  "error-title": "bold #FF4DA6",
  "warning-title": "bold #FFC2E0",
  "error-message": "#C8C8D0",
  "hint-arrow": "#9CE19C dim",
  "hint": "italic #9CE19C",
}
```

---

## Faults: humane and machine-friendly

Every fault has:

* `code` — numeric fault code (rendered as `APP-<code>` using `__prog__`)
* `title` — short lowercase name
* `message` — position-first explanation
* `hint` — one actionable next step
* `docs` — optional pointer

**Plain**

```
APP-11124 invalid choice
cardinal from 3rd position is not a valid choice
→ use one of: add | remove | update
```

**Fancy**

```
╭─ [ APP — 11124 | invalid choice ] ───────────────╮
│ cardinal from 3rd position is not a valid choice │
│ → use one of: add | remove | update              │
╰──────────────────────────────────────────────────╯ 
```

Warnings are soft and non-terminative; errors may be grouped into a final **bad exit** panel.

---

## Status

**Stable 1.0.0.**
Earlier tags were alpha and will be removed.

Argonaut is the **cuna** of Seralix CLI. It will remain available until Seralix CLI reaches its own 1.0.0, at which point Argonaut will be **deprecated** in favor of the official Seralix compiler toolchain.

---

## License

[MIT](https://github.com/seralix-source/argonaut/blob/main/license) © [影皇嶺臣 (Eiko Reishin)](https://github.com/eiko-reishin)

[Rich](https://github.com/textualize/rich)
