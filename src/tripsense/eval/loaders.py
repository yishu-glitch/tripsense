"""Load Eval-200 dialogue cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_eval_path() -> Path:
    return repo_root() / "data" / "eval" / "dialogue_eval_200.jsonl"


def load_eval_cases(
    path: Path | None = None,
    *,
    ids: set[str] | None = None,
    limit: int | None = None,
    cities: set[str] | None = None,
    scenes: set[str] | None = None,
    difficulties: set[str] | None = None,
) -> list[dict[str, Any]]:
    path = path or default_eval_path()
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            if ids is not None and case["id"] not in ids:
                continue
            if cities is not None and case.get("city") not in cities:
                continue
            if scenes is not None and case.get("style_scene") not in scenes:
                continue
            if difficulties is not None and case.get("difficulty") not in difficulties:
                continue
            cases.append(case)
            if limit is not None and len(cases) >= limit:
                break
    return cases


def iter_first_user_turns(cases: list[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], str]]:
    for case in cases:
        dialogue = case.get("dialogue") or []
        if not dialogue:
            continue
        yield case, str(dialogue[0].get("user") or "")
