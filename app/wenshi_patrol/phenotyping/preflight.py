"""No-motion preflight for the Wenshi phenotype observation task."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import load_phenotyping_config, validate_phenotyping_config


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    formal_ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    hardware_probe_called: bool = False


def phenotyping_preflight(
    config: dict[str, Any],
    runtime_root: Path,
    hardware_probe: Callable[[], Any] | None = None,
) -> PreflightReport:
    """Validate phenotype configuration before any hardware client is created.

    The runtime directory is checked for writability only. Hardware probing is
    deliberately optional and is called only after all static gates pass.
    """
    errors = list(validate_phenotyping_config(config))
    runtime = Path(runtime_root).expanduser()
    if runtime.exists() and not runtime.is_dir():
        errors.append(f"运行目录不是目录: {runtime}")
    elif runtime.exists() and not _writable(runtime):
        errors.append(f"运行目录不可写: {runtime}")
    phenotype = config.get("phenotyping", {}) if isinstance(config, dict) else {}
    enabled = bool(phenotype.get("enabled", False)) if isinstance(phenotype, dict) else False
    loaded = load_phenotyping_config(config) if isinstance(config, dict) else None
    formal_ready = bool(loaded and loaded.formal_ready)
    hardware_called = False
    if not errors and enabled and hardware_probe is not None:
        hardware_called = True
        try:
            result = hardware_probe()
            if result is False:
                errors.append("表型硬件预检失败")
        except Exception as exc:
            errors.append(f"表型硬件预检失败: {exc}")
    return PreflightReport(
        ok=not errors,
        formal_ready=formal_ready,
        errors=tuple(errors),
        warnings=(),
        hardware_probe_called=hardware_called,
    )


def _writable(path: Path) -> bool:
    return path.is_dir() and __import__("os").access(path, __import__("os").W_OK)
