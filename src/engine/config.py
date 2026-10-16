"""YAML configuration loading for source profiles and design defaults."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

CONFIG_DIR: Path = Path(__file__).resolve().parents[2] / "config"
SOURCES_FILE: Path = CONFIG_DIR / "water_source_profiles.yaml"
DEFAULTS_FILE: Path = CONFIG_DIR / "design_defaults.yaml"


def _read_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``base`` recursively updated with ``override``."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(
    overrides: Optional[Dict[str, Any]] = None,
    sources_file: Path = SOURCES_FILE,
    defaults_file: Path = DEFAULTS_FILE,
) -> Dict[str, Any]:
    """Load the full model configuration.

    The returned dict holds the design defaults plus the source library under
    ``library`` (sources, dose_response, leed_condenser_limits, silica alert).
    ``overrides`` is deep-merged on top, so callers can tweak any input.
    """
    cfg = _read_yaml(defaults_file)
    cfg["library"] = _read_yaml(sources_file)
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg


def source_keys(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Map source key → display label."""
    return {k: v["label"] for k, v in cfg["library"]["sources"].items()}
