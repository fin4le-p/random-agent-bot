import os
import json
import random
from typing import List, Dict, Any

AGENT_FILE = os.getenv("AGENT_FILE", "agents.json")

ROLE_CONTROLLER = 4  # コントローラー

class Agent:
    def __init__(self, id: str, name_ja: str, role: int, enabled: bool = True):
        self.id = id
        self.name_ja = name_ja
        self.role = role
        self.enabled = enabled

def _load_agents_raw() -> Dict[str, Any]:
    """agents.json を毎回読み込む（ファイル編集を即反映させる）。"""
    with open(AGENT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data

def _load_agents() -> List[Agent]:
    data = _load_agents_raw()
    agents: List[Agent] = []
    for item in data.get("agents", []):
        if item.get("enabled", True):
            agents.append(
                Agent(
                    id=item.get("id", ""),
                    name_ja=item.get("name_ja", ""),
                    role=int(item.get("role", 0)),
                    enabled=item.get("enabled", True),
                )
            )
    return agents

# ===== 既存モード =====

def get_default_agents() -> List[str]:
    """
    デフォルトモード：
    - ロール 1〜4 からそれぞれ1人ずつ
    - さらに全体から1人
    合計5人、重複なしでランダム。
    """
    agents = _load_agents()
    if not agents:
        return []

    result: List[str] = []
    used_ids = set()

    # ロールごと
    for role in range(1, 5):
        candidates = [a for a in agents if a.role == role and a.id not in used_ids]
        if candidates:
            picked = random.choice(candidates)
            result.append(picked.name_ja)
            used_ids.add(picked.id)

    # 全体から1人
    remaining = [a for a in agents if a.id not in used_ids]
    if remaining:
        picked = random.choice(remaining)
        result.append(picked.name_ja)
        used_ids.add(picked.id)

    random.shuffle(result)
    return result[:5]

def get_chaos_agents() -> List[str]:
    """カオスモード：ロール無視で全体から 5 人ランダム。"""
    agents = _load_agents()
    if not agents:
        return []
    if len(agents) <= 5:
        random.shuffle(agents)
        return [a.name_ja for a in agents]
    return [a.name_ja for a in random.sample(agents, 5)]

def get_hirano_agents() -> List[str]:
    """
    平野流モード：
    - コントローラー(ROLE_CONTROLLER) を少なくとも 1 人
    - 残りはその他から 4 人
    合計5人。
    """
    agents = _load_agents()
    if not agents:
        return []

    controllers = [a for a in agents if a.role == ROLE_CONTROLLER]
    others = [a for a in agents if a.role != ROLE_CONTROLLER]

    result: List[str] = []
    used_ids = set()

    # コントローラー 1人
    if controllers:
        ctrl = random.choice(controllers)
        result.append(ctrl.name_ja)
        used_ids.add(ctrl.id)

    # 残り枠数
    remaining_slots = 5 - len(result)
    if remaining_slots <= 0:
        random.shuffle(result)
        return result[:5]

    # その他から残りを選ぶ
    candidates = [a for a in agents if a.id not in used_ids]
    if len(candidates) <= remaining_slots:
        result.extend(a.name_ja for a in candidates)
    else:
        result.extend(a.name_ja for a in random.sample(candidates, remaining_slots))

    random.shuffle(result)
    return result[:5]

def get_ban_agents(count: int = 2) -> List[str]:
    """
    ピック禁止祭（BAN ルーレット）用。
    有効なエージェントから count 人分 BAN を返す。
    """
    agents = _load_agents()
    if not agents:
        return []

    count = max(1, min(count, len(agents)))
    banned = random.sample(agents, count)
    return [a.name_ja for a in banned]
