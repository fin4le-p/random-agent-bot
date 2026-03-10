import json
import logging
import os
import random
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

AGENT_FILE = os.getenv("AGENT_FILE", "agents.json")
ROLE_CONTROLLER = 4


@dataclass(slots=True)
class Agent:
    id: str
    name_ja: str
    role: int
    enabled: bool = True


def _load_agents_raw() -> dict[str, Any]:
    with open(AGENT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_agents_file() -> list[str]:
    warnings: list[str] = []

    try:
        data = _load_agents_raw()
    except FileNotFoundError:
        return [f"{AGENT_FILE} が見つかりません。"]
    except json.JSONDecodeError as exc:
        return [f"{AGENT_FILE} のJSONが壊れています: {exc}"]
    except Exception as exc:
        return [f"{AGENT_FILE} の読み込みに失敗しました: {exc}"]

    raw_agents = data.get("agents", [])
    if not isinstance(raw_agents, list):
        return [f"{AGENT_FILE} の agents が配列ではありません。"]

    enabled_controllers = 0

    for index, item in enumerate(raw_agents, start=1):
        if not isinstance(item, dict):
            warnings.append(f"{index}件目がオブジェクトではありません。")
            continue

        name_ja = str(item.get("name_ja", "")).strip()
        agent_id = str(item.get("id", "")).strip()
        role = item.get("role", 0)
        enabled = bool(item.get("enabled", True))

        if not name_ja:
            warnings.append(f"{index}件目の name_ja が空です。")
        if not agent_id:
            warnings.append(f"{index}件目の id が空です。")

        try:
            role_int = int(role)
        except Exception:
            warnings.append(f"{index}件目の role が数値に変換できません: {role}")
            continue

        if role_int not in {1, 2, 3, 4}:
            warnings.append(f"{index}件目の role が範囲外です: {role_int}")

        if enabled and role_int == ROLE_CONTROLLER:
            enabled_controllers += 1

    if enabled_controllers == 0:
        warnings.append("enabled な controller(role=4) が 1 件もありません。平野流モードは成立しません。")

    return warnings


def _load_agents() -> list[Agent]:
    try:
        data = _load_agents_raw()
    except Exception:
        logger.exception("Failed to load %s", AGENT_FILE)
        return []

    agents: list[Agent] = []
    for item in data.get("agents", []):
        try:
            if not item.get("enabled", True):
                continue

            agents.append(
                Agent(
                    id=str(item.get("id", "")).strip(),
                    name_ja=str(item.get("name_ja", "")).strip(),
                    role=int(item.get("role", 0)),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        except Exception:
            logger.exception("Invalid agent row: %s", item)

    return [agent for agent in agents if agent.id and agent.name_ja]


def get_default_agents() -> list[str]:
    agents = _load_agents()
    if not agents:
        return []

    result: list[str] = []
    used_ids: set[str] = set()

    for role in range(1, 5):
        candidates = [a for a in agents if a.role == role and a.id not in used_ids]
        if candidates:
            picked = random.choice(candidates)
            result.append(picked.name_ja)
            used_ids.add(picked.id)

    remaining = [a for a in agents if a.id not in used_ids]
    if remaining:
        picked = random.choice(remaining)
        result.append(picked.name_ja)
        used_ids.add(picked.id)

    random.shuffle(result)
    return result[:5]


def get_chaos_agents() -> list[str]:
    agents = _load_agents()
    if not agents:
        return []

    if len(agents) <= 5:
        random.shuffle(agents)
        return [a.name_ja for a in agents]

    return [a.name_ja for a in random.sample(agents, 5)]


def get_hirano_agents() -> list[str]:
    agents = _load_agents()
    if not agents:
        return []

    controllers = [a for a in agents if a.role == ROLE_CONTROLLER]
    if not controllers:
        logger.warning("No enabled controller found. Hirano mode cannot satisfy the guarantee.")
        return []

    result: list[str] = []
    used_ids: set[str] = set()

    ctrl = random.choice(controllers)
    result.append(ctrl.name_ja)
    used_ids.add(ctrl.id)

    remaining_slots = 5 - len(result)
    candidates = [a for a in agents if a.id not in used_ids]

    if len(candidates) <= remaining_slots:
        result.extend(a.name_ja for a in candidates)
    else:
        result.extend(a.name_ja for a in random.sample(candidates, remaining_slots))

    random.shuffle(result)
    return result[:5]


def get_ban_agents(count: int = 2) -> list[str]:
    agents = _load_agents()
    if not agents:
        return []

    count = max(1, min(count, len(agents)))
    banned = random.sample(agents, count)
    return [a.name_ja for a in banned]