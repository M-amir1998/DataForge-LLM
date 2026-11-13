"""Synthetic evaluation sets used to demonstrate contamination detection.

These items are generated here (not copied from GSM8K, HumanEval, MBPP, or any other
benchmark) so the repository can ship them under CC0-1.0. The committed JSONL files under
``benchmarks/contamination/custom/`` are produced by :func:`write_demo_benchmarks`, and a
test asserts they stay identical to this generator.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

BENCHMARK_SEED: Final = 2026

# fmt: off
_NAMES: Final = (
    "Maya", "Omar", "Lucia", "Kenji", "Amara", "Tobias", "Priya", "Mateo", "Ingrid", "Yusuf",
    "Chloe", "Ravi", "Nadia", "Felix", "Zara", "Diego", "Hana", "Elias", "Sofia", "Kwame",
)
# fmt: on


@dataclass(frozen=True, slots=True)
class MathTemplate:
    text: str
    answer: Callable[[int, int, int, int], int]


_MATH_TEMPLATES: Final = (
    MathTemplate(
        "A neighbourhood bakery bakes {a} loaves of rye bread every morning and {b} loaves "
        "every afternoon. Each loaf sells for {c} dollars. If {name} sells every loaf for {d} "
        "days in a row, how many dollars does the bakery collect from rye bread?",
        lambda a, b, c, d: (a + b) * c * d,
    ),
    MathTemplate(
        "{name} reads {a} pages of a mystery novel on each weekday and {b} pages on each "
        "weekend day. Counting every day of the week, how many pages does {name} read during "
        "{d} complete weeks of steady reading?",
        lambda a, b, _c, d: (5 * a + 2 * b) * d,
    ),
    MathTemplate(
        "A community garden has {a} raised beds. {name} plants {b} tomato seedlings in each bed, "
        "and every seedling later yields {c} tomatoes. After {d} tomatoes are given to the local "
        "food bank, how many tomatoes remain for the gardeners?",
        lambda a, b, c, d: a * b * c - d,
    ),
    MathTemplate(
        "A cyclist named {name} rides {a} kilometres to work and the same distance back home on "
        "each of {b} working days. On the weekend {name} adds one long ride of {c} kilometres. "
        "How many kilometres does {name} cycle in that whole week?",
        lambda a, b, c, _d: 2 * a * b + c,
    ),
    MathTemplate(
        "A school library receives {a} boxes of donated books, and each box holds {b} books. The "
        "librarian {name} keeps {c} books from every box for the reference shelf and sends the "
        "rest to classrooms. How many books go to the classrooms in total?",
        lambda a, b, c, _d: a * (b - c),
    ),
    MathTemplate(
        "{name} saves {a} dollars every week from a part-time job and spends {b} dollars of it "
        "on bus tickets. After {d} weeks, {name} also receives a birthday gift of {c} dollars. "
        "How many dollars has {name} saved at the end of that period?",
        lambda a, b, c, d: (a - b) * d + c,
    ),
    MathTemplate(
        "A theatre has {a} rows with {b} seats in each row. For the Saturday matinee, {c} seats "
        "are reserved for students and every other seat is sold at full price. If {name} counts "
        "the full-price seats, how many does the count reach?",
        lambda a, b, c, _d: a * b - c,
    ),
    MathTemplate(
        "An orchard owner named {name} picks {a} crates of apples on Monday, twice as many crates "
        "on Tuesday, and {b} crates on Wednesday. Each crate holds {c} apples. How many apples "
        "are picked across those three days of harvest work?",
        lambda a, b, c, _d: (a + 2 * a + b) * c,
    ),
    MathTemplate(
        "A recycling centre collects {a} kilograms of glass on each of {b} weekdays and {c} "
        "kilograms on Saturday. {name} ships out {d} kilograms before the week closes. How many "
        "kilograms of glass are still stored at the centre?",
        lambda a, b, c, d: a * b + c - d,
    ),
    MathTemplate(
        "During a charity walk, {name} gets pledges of {a} dollars per kilometre from {b} friends "
        "and a flat donation of {c} dollars from a local shop. If {name} completes {d} kilometres, "
        "how many dollars are raised for the charity in total?",
        lambda a, b, c, d: a * b * d + c,
    ),
)


@dataclass(frozen=True, slots=True)
class CodeTemplate:
    name: str
    prompt: str
    solution: str


_CODE_TEMPLATES: Final = (
    CodeTemplate(
        "sum_even_squares",
        'def {fn}(values):\n    """Return the sum of the squares of the even integers in values."""\n',
        "    return sum(item * item for item in values if item % 2 == 0)\n",
    ),
    CodeTemplate(
        "count_vowel_words",
        'def {fn}(sentence):\n    """Count the words in sentence that start with a vowel."""\n',
        "    return sum(1 for word in sentence.split() if word[:1].lower() in 'aeiou')\n",
    ),
    CodeTemplate(
        "running_maximum",
        'def {fn}(numbers):\n    """Return a list whose i-th element is the maximum of numbers[: i + 1]."""\n',
        "    result, best = [], None\n    for value in numbers:\n"
        "        best = value if best is None or value > best else best\n"
        "        result.append(best)\n    return result\n",
    ),
    CodeTemplate(
        "merge_intervals",
        'def {fn}(intervals):\n    """Merge overlapping closed intervals given as (start, end) pairs."""\n',
        "    merged = []\n    for start, end in sorted(intervals):\n"
        "        if merged and start <= merged[-1][1]:\n"
        "            merged[-1] = (merged[-1][0], max(merged[-1][1], end))\n"
        "        else:\n            merged.append((start, end))\n    return merged\n",
    ),
    CodeTemplate(
        "is_balanced",
        'def {fn}(text):\n    """Return True when every bracket in text is closed in the right order."""\n',
        "    pairs, stack = {')': '(', ']': '[', '}': '{'}, []\n    for char in text:\n"
        "        if char in '([{':\n            stack.append(char)\n"
        "        elif char in pairs and (not stack or stack.pop() != pairs[char]):\n"
        "            return False\n    return not stack\n",
    ),
    CodeTemplate(
        "chunk_list",
        'def {fn}(items, size):\n    """Split items into consecutive chunks of at most size elements."""\n',
        "    return [items[index : index + size] for index in range(0, len(items), size)]\n",
    ),
    CodeTemplate(
        "word_frequencies",
        'def {fn}(text):\n    """Return a dict mapping each lowercase word in text to its count."""\n',
        "    counts = {}\n    for word in text.lower().split():\n"
        "        counts[word] = counts.get(word, 0) + 1\n    return counts\n",
    ),
    CodeTemplate(
        "rotate_matrix",
        'def {fn}(matrix):\n    """Rotate a square matrix ninety degrees clockwise and return it."""\n',
        "    return [list(row) for row in zip(*matrix[::-1])]\n",
    ),
    CodeTemplate(
        "second_largest",
        'def {fn}(numbers):\n    """Return the second largest distinct value, or None if there is none."""\n',
        "    distinct = sorted(set(numbers))\n    return distinct[-2] if len(distinct) > 1 else None\n",
    ),
    CodeTemplate(
        "flatten_once",
        'def {fn}(nested):\n    """Flatten a list of lists by exactly one level."""\n',
        "    return [element for group in nested for element in group]\n",
    ),
)


def demo_math_items(count: int = 40, seed: int = BENCHMARK_SEED) -> list[dict[str, str]]:
    rng = random.Random(seed)
    items: list[dict[str, str]] = []
    for index in range(count):
        template = _MATH_TEMPLATES[index % len(_MATH_TEMPLATES)]
        a, b, c = rng.randint(12, 48), rng.randint(3, 11), rng.randint(2, 9)
        d = rng.randint(2, 9)
        if "given to the local" in template.text or "ships out" in template.text:
            d = rng.randint(5, 40)
        name = _NAMES[rng.randrange(len(_NAMES))]
        question = template.text.format(a=a, b=b, c=c, d=d, name=name)
        items.append(
            {
                "id": f"demo-math-{index:03d}",
                "question": question,
                "answer": str(template.answer(a, b, c, d)),
            }
        )
    return items


def demo_code_items(count: int = 20, seed: int = BENCHMARK_SEED) -> list[dict[str, str]]:
    rng = random.Random(seed + 1)
    prefixes = ("compute", "find", "build", "make", "get")
    items: list[dict[str, str]] = []
    for index in range(count):
        template = _CODE_TEMPLATES[index % len(_CODE_TEMPLATES)]
        fn = f"{prefixes[rng.randrange(len(prefixes))]}_{template.name}"
        items.append(
            {
                "id": f"demo-code-{index:03d}",
                "prompt": template.prompt.format(fn=fn),
                "canonical_solution": template.solution,
            }
        )
    return items


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_demo_benchmarks(directory: Path) -> list[Path]:
    """Write ``demo_math.jsonl`` and ``demo_code.jsonl`` into ``directory``."""
    math_path = directory / "demo_math.jsonl"
    code_path = directory / "demo_code.jsonl"
    _write_jsonl(math_path, demo_math_items())
    _write_jsonl(code_path, demo_code_items())
    return [math_path, code_path]
