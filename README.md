# Awn

**Awn (عَوْن)** — *A versatile and intelligent aide who assists, supports, and carries out a wide range of tasks with competence, adaptability, and initiative.*

---

## Installation

```bash
pip install -e .
```

## Usage

### Interactive mode

```bash
awn
```

```
مرحباً! I'm Awn (عَوْن) — your versatile aide.
Type a command or 'help' to see what I can do. Type 'quit' to exit.

awn> help
Available commands:

  help         List available commands or get help on a specific command.  (aliases: ?, h)
  echo         Repeat text back to you.
  time         Show the current date and time.  (aliases: date, now)
  calc         Evaluate an arithmetic expression.  (aliases: calculate, math, =)
  text         Manipulate text (upper, lower, title, reverse, len, words).
```

### One-shot mode

```bash
awn calc 2 + 3 * 4        # arithmetic
awn echo Hello, world!    # echo
awn time                  # current date and time
awn text upper hello      # text transforms
```

## Built-in skills

| Command | Aliases         | Description |
|---------|-----------------|-------------|
| `help`  | `?`, `h`        | List commands or get help on a specific one |
| `echo`  |                 | Repeat text back |
| `time`  | `date`, `now`   | Show current date/time |
| `calc`  | `calculate`, `math`, `=` | Evaluate arithmetic expressions |
| `text`  |                 | Transform text: `upper`, `lower`, `title`, `reverse`, `len`, `words` |

## Running tests

```bash
pip install pytest
pytest
```
