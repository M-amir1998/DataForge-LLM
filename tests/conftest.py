from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, settings

from dataforge.telemetry.logging import configure_logging
from tests.support import build_project, write_pipeline

# "ci" is derandomized so Jenkins runs are reproducible; "dev" explores fresh examples.
settings.register_profile(
    "ci", max_examples=150, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow]
)
settings.register_profile("dev", max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

configure_logging("WARNING")


@pytest.fixture(scope="session")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway copy of the repository layout with a generated smoke corpus."""
    return build_project(tmp_path_factory.mktemp("project"))


@pytest.fixture
def pipeline_config(project: Path, tmp_path: Path) -> Callable[..., Path]:
    """Factory writing smoke-derived pipeline configs with per-test storage."""

    def factory(**overrides: Any) -> Path:
        return write_pipeline(project, tmp_path, overrides)

    return factory
