"""Synthetic source files in several languages, optionally carrying planted secrets."""

from __future__ import annotations

from typing import Final

from dataforge.synthetic.text import TextGenerator

# fmt: off
_NOUNS: Final = (
    "order", "invoice", "sensor", "account", "ticket", "shipment", "sample", "session", "batch",
    "record", "metric", "segment", "profile", "booking", "reading", "parcel", "route", "job",
)
# fmt: on
# fmt: off
_VERBS: Final = (
    "load", "parse", "merge", "score", "filter", "rank", "build", "resolve", "validate", "group",
    "normalize", "summarize", "dispatch", "estimate", "collect", "render",
)
# fmt: on
_FIELDS: Final = ("priority", "weight", "total", "count", "score", "size", "age", "latency")

_PY_SNIPPETS: Final = (
    (
        'def {fn}({var}s):\n    """Return the {var} with the largest {field}."""\n    best = None\n'
        "    for {var} in {var}s:\n        if best is None or {var}.{field} > best.{field}:\n"
        "            best = {var}\n    return best\n"
    ),
    (
        'def {fn}(path):\n    """Load {var} records from a JSON lines file."""\n    records = []\n'
        '    with open(path, encoding="utf-8") as handle:\n        for line in handle:\n'
        "            if line.strip():\n                records.append(json.loads(line))\n    return records\n"
    ),
    (
        'def {fn}({var}, limit={const}):\n    """Clamp {var} into the closed range [0, limit]."""\n'
        "    return max(0, min({var}, limit))\n"
    ),
    (
        "def {fn}(values):\n    total = 0\n    for index, value in enumerate(values):\n"
        "        total += value * (index + {const})\n    return total\n"
    ),
    (
        '@dataclass(frozen=True)\nclass {cls}:\n    {field}: int\n    label: str = "{var}"\n\n'
        "    def scaled(self, factor: int = {const}) -> int:\n        return self.{field} * factor\n"
    ),
    (
        "def {fn}(items, key):\n    groups = {{}}\n    for item in items:\n"
        "        groups.setdefault(item[key], []).append(item)\n    return groups\n"
    ),
    (
        "def {fn}(attempts={const}):\n    def decorator(func):\n        def wrapper(*args, **kwargs):\n"
        "            for attempt in range(attempts):\n                try:\n"
        "                    return func(*args, **kwargs)\n                except OSError:\n"
        "                    if attempt == attempts - 1:\n                        raise\n"
        "        return wrapper\n    return decorator\n"
    ),
    (
        'def {fn}(text):\n    """Split text into non-empty, stripped lines."""\n'
        "    return [line.strip() for line in text.splitlines() if line.strip()]\n"
    ),
)

_JS_SNIPPETS: Final = (
    "export function {fn}({var}s) {{\n  return {var}s.filter(({var}) => {var}.{field} > {const});\n}}\n",
    (
        "export async function {fn}(url) {{\n  const response = await fetch(url);\n"
        "  if (!response.ok) {{\n    throw new Error(`request failed: ${{response.status}}`);\n  }}\n"
        "  return response.json();\n}}\n"
    ),
    "export const {fn} = (items) =>\n  items.reduce((acc, item) => acc + item.{field}, {const});\n",
    (
        "export class {cls} {{\n  constructor({field}) {{\n    this.{field} = {field};\n  }}\n\n"
        "  isLarge() {{\n    return this.{field} > {const};\n  }}\n}}\n"
    ),
)

_GO_SNIPPETS: Final = (
    (
        "func {fn}(values []int) int {{\n\ttotal := 0\n\tfor _, v := range values {{\n"
        "\t\tif v > {const} {{\n\t\t\ttotal += v\n\t\t}}\n\t}}\n\treturn total\n}}\n"
    ),
    (
        "type {cls} struct {{\n\tName string\n\t{Field} int\n}}\n\n"
        "func (x {cls}) Heavy() bool {{\n\treturn x.{Field} > {const}\n}}\n"
    ),
    (
        "func {fn}(path string) ([]byte, error) {{\n\tdata, err := os.ReadFile(path)\n"
        '\tif err != nil {{\n\t\treturn nil, fmt.Errorf("read %s: %w", path, err)\n\t}}\n\treturn data, nil\n}}\n'
    ),
)

_RUST_SNIPPETS: Final = (
    "pub fn {fn}(values: &[i64]) -> i64 {{\n    values.iter().filter(|v| **v > {const}).sum()\n}}\n",
    (
        "#[derive(Debug, Clone)]\npub struct {cls} {{\n    pub {field}: u32,\n}}\n\n"
        "impl {cls} {{\n    pub fn doubled(&self) -> u32 {{\n        self.{field} * 2\n    }}\n}}\n"
    ),
    (
        "pub fn {fn}(text: &str) -> Vec<String> {{\n    text.lines()\n        .map(str::trim)\n"
        "        .filter(|line| !line.is_empty())\n        .map(String::from)\n        .collect()\n}}\n"
    ),
)

_JAVA_SNIPPETS: Final = (
    (
        "    public static int {fn}(int[] values) {{\n        int total = 0;\n"
        "        for (int value : values) {{\n            if (value > {const}) {{\n"
        "                total += value;\n            }}\n        }}\n        return total;\n    }}\n"
    ),
    (
        "    public static String {fn}(String input) {{\n        if (input == null) {{\n"
        '            return "";\n        }}\n        return input.trim().toLowerCase();\n    }}\n'
    ),
)

EXTENSIONS: Final = {
    "python": ".py",
    "javascript": ".js",
    "go": ".go",
    "rust": ".rs",
    "java": ".java",
}


class CodeGenerator:
    def __init__(self, text: TextGenerator) -> None:
        self.text = text

    def _names(self) -> dict[str, str]:
        pick = self.text.pick
        verb, noun, field = pick(_VERBS), pick(_NOUNS), pick(_FIELDS)
        return {
            "fn": f"{verb}_{noun}s",
            "var": noun,
            "field": field,
            "Field": field.capitalize(),
            "cls": f"{noun.capitalize()}{pick(('Record', 'Summary', 'Window', 'Event', 'Stats'))}",
            "const": str(self.text.rng.randint(2, 500)),
        }

    def _render(self, snippets: tuple[str, ...], count: int, camel: bool = False) -> list[str]:
        chosen = self.text.rng.sample(range(len(snippets)), k=min(count, len(snippets)))
        blocks = []
        for index in chosen:
            names = self._names()
            if camel:
                head, _, tail = names["fn"].partition("_")
                names["fn"] = head + "".join(part.capitalize() for part in tail.split("_"))
            blocks.append(snippets[index].format(**names))
        return blocks

    def secret_line(self, language: str, kind: str) -> str:
        value = self.text.fake_secret(kind)
        names = {
            "aws_access_key_id": "AWS_ACCESS_KEY_ID",
            "aws_secret_access_key": "AWS_SECRET_ACCESS_KEY",
            "github_token": "GITHUB_TOKEN",
            "slack_token": "SLACK_BOT_TOKEN",
            "password": "DB_PASSWORD",
        }
        if kind == "private_key":
            if language == "python":
                return f'SIGNING_KEY = """{value}"""\n'
            return f"const signingKey = `{value}`;\n"
        name = names[kind]
        if language == "python":
            return f'{name} = "{value}"\n'
        if language == "javascript":
            return f'const {name} = "{value}";\n'
        if language == "go":
            return f'const {name.title().replace("_", "")} = "{value}"\n'
        if language == "rust":
            return f'pub const {name}: &str = "{value}";\n'
        return f'    private static final String {name} = "{value}";\n'

    def source_file(self, language: str, *, secret: str | None = None, embed: str | None = None) -> str:
        """Render a plausible source file; optionally plant a secret or embed foreign code."""
        rng = self.text.rng
        header_comment = self.text.sentence(
            "Utilities for {topic} maintained by {org}.",
            None,
            self.text.pick(_NOUNS) + " processing",
        )
        if language == "python":
            parts = [f'"""{header_comment}"""\n\nimport json\nfrom dataclasses import dataclass\n\n']
            if secret:
                parts.append(self.secret_line(language, secret) + "\n")
            parts.extend(block + "\n\n" for block in self._render(_PY_SNIPPETS, rng.randint(3, 5)))
            if embed:
                parts.append(embed + "\n\n")
            parts.append('if __name__ == "__main__":\n    print("ready")\n')
            return "".join(parts).rstrip() + "\n"
        if language == "javascript":
            parts = [f"// {header_comment}\n\n"]
            if secret:
                parts.append(self.secret_line(language, secret) + "\n")
            parts.extend(block + "\n" for block in self._render(_JS_SNIPPETS, rng.randint(2, 4), camel=True))
            return "".join(parts).rstrip() + "\n"
        if language == "go":
            parts = [f'// {header_comment}\npackage main\n\nimport (\n\t"fmt"\n\t"os"\n)\n\n']
            if secret:
                parts.append(self.secret_line(language, secret) + "\n")
            parts.extend(block + "\n" for block in self._render(_GO_SNIPPETS, rng.randint(2, 3), camel=True))
            return "".join(parts).rstrip() + "\n"
        if language == "rust":
            parts = [f"//! {header_comment}\n\n"]
            if secret:
                parts.append(self.secret_line(language, secret) + "\n")
            parts.extend(block + "\n" for block in self._render(_RUST_SNIPPETS, rng.randint(2, 3)))
            return "".join(parts).rstrip() + "\n"
        names = self._names()
        parts = [f"// {header_comment}\npublic final class {names['cls']} {{\n\n"]
        if secret:
            parts.append(self.secret_line(language, secret) + "\n")
        parts.extend(block + "\n" for block in self._render(_JAVA_SNIPPETS, 2, camel=True))
        parts.append("}\n")
        return "".join(parts)
