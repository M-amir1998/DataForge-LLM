"""Validate built distributions before they are archived or published.

Checks that the wheel ships the typed package and nothing else (no tests, data, configs,
or secrets), that the sdist contains what is needed to rebuild and test, that metadata
declares the license, and that the wheel installs into a clean environment and runs.

Usage: python scripts/check_dist.py dist
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import NoReturn

FORBIDDEN_IN_WHEEL = ("tests/", "data/", "configs/", ".dataforge", ".env", ".parquet", "__pycache__")
FORBIDDEN_ANYWHERE = (".env", ".pem", "id_rsa", ".dataforge/", "data/sample", "data/smoke", ".venv/")
REQUIRED_IN_WHEEL = ("dataforge/__init__.py", "dataforge/py.typed", "dataforge/cli/app.py")
REQUIRED_IN_SDIST = ("pyproject.toml", "uv.lock", "LICENSE", "src/dataforge/__init__.py", "tests/conftest.py")


def fail(message: str) -> NoReturn:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def check_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
    for required in REQUIRED_IN_WHEEL:
        if required not in names:
            fail(f"{wheel.name} is missing {required}")
    for name in names:
        if any(token in name for token in FORBIDDEN_IN_WHEEL):
            fail(f"{wheel.name} contains forbidden path {name}")
    if "License-Expression: Apache-2.0" not in metadata:
        fail("wheel metadata does not declare License-Expression: Apache-2.0")
    if "Requires-Python: >=3.11" not in metadata:
        fail("wheel metadata does not declare Requires-Python >=3.11")
    print(f"ok: {wheel.name} ({len(names)} files)")


def check_sdist(sdist: Path) -> None:
    with tarfile.open(sdist) as archive:
        names = [m.name.split("/", 1)[1] for m in archive.getmembers() if "/" in m.name]
    for required in REQUIRED_IN_SDIST:
        if required not in names:
            fail(f"{sdist.name} is missing {required}")
    for name in names:
        if any(token in name for token in FORBIDDEN_ANYWHERE):
            fail(f"{sdist.name} contains forbidden path {name}")
    print(f"ok: {sdist.name} ({len(names)} files)")


def check_install(wheel: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        fail("uv is required for the install check")
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        subprocess.run([uv, "venv", "--quiet", str(venv)], check=True)
        python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([uv, "pip", "install", "--quiet", "--python", str(python), str(wheel)], check=True)
        result = subprocess.run(
            [str(python), "-m", "dataforge", "--version"], capture_output=True, text=True, check=True
        )
        print(f"ok: clean install runs ({result.stdout.strip()})")


def main(argv: list[str]) -> int:
    dist = Path(argv[1] if len(argv) > 1 else "dist")
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        fail(f"expected exactly one wheel and one sdist in {dist}, found {len(wheels)} and {len(sdists)}")
    check_wheel(wheels[0])
    check_sdist(sdists[0])
    check_install(wheels[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
