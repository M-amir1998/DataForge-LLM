"""Repository secret scan using DataForge's own credential detectors.

Scans the working tree (tracked + unignored files), staged changes, and optionally the
full Git history. Findings are reported as ``path:line type`` with the value redacted;
the exit code is 1 when anything is found. A line can be allowlisted with the marker
``dataforge: allow-secret`` (use sparingly; prefer assembling test values from fragments).

Usage:
    python scripts/scan_secrets.py                # working tree
    python scripts/scan_secrets.py --staged       # staged changes (pre-commit)
    python scripts/scan_secrets.py --history      # working tree + every commit
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from dataforge.config.models import ALL_SECRET_TYPES
from dataforge.privacy.secrets import build_secret_scanner

ALLOW_MARKER = "dataforge: allow-secret"
SKIP_SUFFIXES = (".parquet", ".png", ".jpg", ".gz", ".zst", ".zip", ".whl", ".lock")
MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    where: str
    line: int
    kind: str


def _git(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required")
    return subprocess.run(
        [git, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
    ).stdout


def _scan_text(where: str, text: str, scanner_kinds: tuple[str, ...]) -> Iterator[Finding]:
    scanner = build_secret_scanner(scanner_kinds, min_entropy=3.5, min_length=12)
    lines = text.splitlines()
    for span in scanner.scan(text):
        line_number = text.count("\n", 0, span.start) + 1
        if ALLOW_MARKER in lines[line_number - 1]:
            continue
        yield Finding(where, line_number, span.kind)


def scan_working_tree() -> Iterator[Finding]:
    files = _git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    for name in files:
        path = Path(name)
        if name.endswith(SKIP_SUFFIXES) or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        yield from _scan_text(name, text, ALL_SECRET_TYPES)


def _scan_patch(label: str, patch: str) -> Iterator[Finding]:
    """Scan only added lines of a unified diff."""
    current = "?"
    added: list[str] = []
    origins: list[tuple[str, int]] = []
    line_number = 0
    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            current = raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("@@"):
            header = raw.split("+", 1)[1].split(" ", 1)[0]
            line_number = int(header.split(",")[0]) - 1
        elif raw.startswith("+") and not raw.startswith("+++"):
            line_number += 1
            if not current.endswith(SKIP_SUFFIXES):
                added.append(raw[1:])
                origins.append((current, line_number))
        elif not raw.startswith("-"):
            line_number += 1
    text = "\n".join(added)
    for finding in _scan_text(label, text, ALL_SECRET_TYPES):
        path, line = origins[finding.line - 1]
        yield Finding(f"{label}:{path}", line, finding.kind)


def scan_staged() -> Iterator[Finding]:
    yield from _scan_patch("staged", _git("diff", "--cached", "--unified=0", "--no-color"))


def scan_history() -> Iterator[Finding]:
    for commit in _git("rev-list", "--all").split():
        yield from _scan_patch(commit[:12], _git("show", commit, "--unified=0", "--no-color", "--format="))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staged", action="store_true", help="scan staged changes only")
    parser.add_argument("--history", action="store_true", help="also scan every commit reachable from any ref")
    parser.add_argument("--report", type=Path, help="write findings as JSON")
    args = parser.parse_args(argv)

    if args.staged:
        findings = list(scan_staged())
        scope = "staged changes"
    else:
        findings = list(scan_working_tree())
        scope = "working tree"
        if args.history:
            findings += list(scan_history())
            scope += " + history"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps({"scope": scope, "findings": [asdict(f) for f in findings]}, indent=2), encoding="utf-8"
        )
    for finding in findings:
        print(f"{finding.where}:{finding.line}: possible {finding.kind} (value redacted)")
    print(f"secret scan ({scope}): {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
