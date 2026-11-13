"""``dataforge doctor``: environment readiness checks."""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import psutil
import typer

from dataforge.cli.common import console, print_json, simple_table
from dataforge.storage.budget import GIB, disk_free_bytes

_REQUIRED = (
    "duckdb",
    "polars",
    "pyarrow",
    "numpy",
    "pydantic",
    "xxhash",
    "regex",
    "typer",
    "rich",
    "psutil",
)
_OPTIONAL = {"datasets": "hf", "tokenizers": "tokenizers", "py3langid": "langid", "s3fs": "s3"}
_TOOLS = {
    "git": "lineage (commit SHA in manifests)",
    "uv": "dependency management and CI",
    "docker": "optional container builds / Jenkins agent",
    "java": "only needed to host a Jenkins controller or agent locally",
    "gitleaks": "optional secret scanning before pushes",
}


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str
    detail: str


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def collect_checks(path: Path, min_free_gb: float = 5.0, min_ram_gb: float = 4.0) -> list[Check]:
    checks: list[Check] = []
    python_ok = sys.version_info >= (3, 11)
    checks.append(Check("python", "ok" if python_ok else "fail", platform.python_version()))
    checks.append(Check("platform", "ok", f"{platform.system()} {platform.release()} ({platform.machine()})"))
    memory = psutil.virtual_memory()
    checks.append(
        Check(
            "ram",
            "ok" if memory.available / GIB >= min_ram_gb else "warn",
            f"{memory.total / GIB:.1f} GiB total, {memory.available / GIB:.1f} GiB available",
        )
    )
    free = disk_free_bytes(path)
    checks.append(
        Check(
            "disk",
            "ok" if free / GIB >= min_free_gb else "warn",
            f"{free / GIB:.1f} GiB free at {path.name or path}",
        )
    )
    checks.append(Check("cpu", "ok", f"{psutil.cpu_count(logical=True)} logical cores"))
    for package in _REQUIRED:
        version = _version(package)
        checks.append(Check(package, "ok" if version else "fail", version or "missing (required)"))
    for package, extra in _OPTIONAL.items():
        version = _version(package)
        checks.append(
            Check(
                package,
                "ok" if version else "info",
                version or f"not installed (optional extra '{extra}')",
            )
        )
    for tool, purpose in _TOOLS.items():
        found = shutil.which(tool)
        checks.append(
            Check(
                f"tool:{tool}",
                "ok" if found else "info",
                "found" if found else f"not found - {purpose}",
            )
        )
    return checks


def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    path: Annotated[Path, typer.Option(help="Directory whose disk is checked.")] = Path(),
) -> None:
    """Check Python, RAM, disk, core libraries, optional extras, and CI tooling."""
    checks = collect_checks(path)
    if as_json:
        print_json(
            [
                check.__dict__
                if hasattr(check, "__dict__")
                else {"name": check.name, "status": check.status, "detail": check.detail}
                for check in checks
            ]
        )
    else:
        console.print(
            simple_table(
                "DataForge doctor",
                ["Check", "Status", "Detail"],
                [[c.name, c.status, c.detail] for c in checks],
                justify="lcl",
            )
        )
        console.print("No GPU is required: every stage runs on CPU.")
    if any(check.status == "fail" for check in checks):
        raise typer.Exit(1)
