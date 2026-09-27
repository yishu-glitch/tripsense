from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_project_env(path: Path | None = None) -> set[str]:
    """Load simple KEY=VALUE entries without replacing process-level settings."""
    env_path = path or project_root() / ".env"
    if not env_path.exists():
        return set()

    loaded: set[str] = set()
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value
        loaded.add(key)
    return loaded
