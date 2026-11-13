"""Plug-in point for learned quality classifiers.

v0.1 ships no model. A model is any object with a ``predict`` method returning, for each
text, the probability that it is high quality; it is loaded from a ``module:factory``
entrypoint. Typical CPU-friendly choices are fastText or linear models over hashed n-grams;
GPU models can run as a separate scoring job whose scores are joined back by document id.

Security note: the entrypoint executes code. Only reference modules you control.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from dataforge.config.models import LearnedQualityModelConfig
from dataforge.errors import ConfigError


@runtime_checkable
class LearnedQualityModel(Protocol):
    def predict(self, texts: Sequence[str]) -> Sequence[float]: ...


def load_model(config: LearnedQualityModelConfig) -> LearnedQualityModel:
    module_name, _, factory_name = config.entrypoint.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(f"cannot import learned quality model module '{module_name}'") from exc
    factory = getattr(module, factory_name, None)
    if factory is None or not callable(factory):
        raise ConfigError(f"'{config.entrypoint}' is not a callable factory")
    model = factory()
    if not isinstance(model, LearnedQualityModel):
        raise ConfigError(f"'{config.entrypoint}' did not return an object with predict(texts)")
    return model
