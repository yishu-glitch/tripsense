"""User-simulation agent: replay scripted turns and optional LLM expansion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .prompts import USERSIM_BEHAVIOR_SPECS, prompt_usersim_behavior, prompt_usersim_open


class LlmUserSim(Protocol):
    def next_utterance(self, case: dict[str, Any], turn_index: int, plan_summary: dict[str, Any]) -> str:
        ...


@dataclass(slots=True)
class UserSimTurn:
    turn: int
    user: str
    tags: list[str]
    modification_type: str = "local"


class ScriptUserSimAgent:
    """Replay `dialogue` user turns from an eval case (no LLM required)."""

    def turns_for_case(self, case: dict[str, Any]) -> list[UserSimTurn]:
        dialogue = case.get("dialogue") or []
        ops = list(case.get("ops_sequence") or [])
        out: list[UserSimTurn] = []
        for i, item in enumerate(dialogue):
            user = str(item.get("user") or "").strip()
            if not user:
                continue
            tags: list[str] = []
            if i > 0:
                if ops:
                    tags = [ops[min(i - 1, len(ops) - 1)]]
                else:
                    tags = list(case.get("tags") or [])
            out.append(
                UserSimTurn(
                    turn=int(item.get("turn") or i + 1),
                    user=user,
                    tags=tags,
                    modification_type=str(item.get("modification_type") or "local"),
                )
            )
        return out


class StubLlmUserSim:
    """Placeholder for LLM-based open-ended user simulation."""

    system_prompt = staticmethod(prompt_usersim_open)
    behavior_prompt = staticmethod(prompt_usersim_behavior)

    def next_utterance(self, case: dict[str, Any], turn_index: int, plan_summary: dict[str, Any]) -> str:
        tags = list(case.get("tags") or [])
        tag = tags[min(turn_index, len(tags) - 1)] if tags else "shorten"
        city = case.get("city_zh") or case.get("city") or "这座城市"
        # Spec strings kept for prompt assembly when a real LLM transport is wired.
        _ = (USERSIM_BEHAVIOR_SPECS.get(tag), plan_summary)
        templates = {
            "add_stop": f"再加一个适合现在主题的点吧，还是在{city}范围内。",
            "shorten": "时间有点紧，帮我压缩一下，别排太满。",
            "replace": "把最后一个换成更符合主题的地方。",
            "state_change": "有点累了，而且可能下雨，节奏放慢一点。",
        }
        return templates.get(tag, "按刚才说的再调整一下。")