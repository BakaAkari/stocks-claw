"""
Hypothesis Tracker — 研究目标/论点笔记本

跨天观点追踪。存储、更新、自动核对投资论点是否被后续数据证实或证伪。
每次生成 ScheduledAnalysisRun 时，检查相关 hypothesis 是否需要更新证据。
纯规则匹配，不依赖 LLM。
"""

from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from stocks.logging_utils import get_logger

logger = get_logger("hypothesis_tracker")

STATUSES = {"open", "confirmed", "invalidated", "stale"}


@dataclass
class Hypothesis:
    """一条投资论点。"""
    id: str
    statement: str                          # 论点陈述
    created_at: str
    updated_at: str
    status: str                             # open / confirmed / invalidated / stale
    evidence_links: list[str] = field(default_factory=list)  # run_id 列表
    tags: list[str] = field(default_factory=list)             # gold, ai, rates, a-share...
    resolution_note: str = ""


class HypothesisStore:
    """论点持久化存储。"""

    def __init__(self, store_dir: Path | None = None):
        if store_dir is None:
            store_dir = Path(__file__).resolve().parents[2] / ".local" / "hypotheses"
        self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "index.json"

    @property
    def _lock_path(self) -> Path:
        return self._dir / "index.json.lock"

    def _load_index(self) -> dict[str, dict]:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_index(self, index: dict) -> None:
        self._index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False))

    def _locked_update(self, mutator):
        """读-改-写整体在 fcntl 排他锁内完成，防并发丢更新。

        修复前 create/update_status/add_evidence 各自 _load→改→_save，
        两个进程并发时后写覆盖先写，evidence_links 丢失。
        锁模式与 risk_state.RiskStateStore 一致（fcntl.flock LOCK_EX）。
        """
        self._lock_path.touch(exist_ok=True)
        with open(self._lock_path, "r+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                index = self._load_index()
                result = mutator(index)
                self._save_index(index)
                return result
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def create(self, statement: str, *, tags: list[str] | None = None) -> Hypothesis:
        h = Hypothesis(
            id=uuid4().hex[:12],
            statement=statement,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            status="open",
            tags=tags or [],
        )
        def _mutate(index):
            index[h.id] = {
                "statement": h.statement, "created_at": h.created_at,
                "updated_at": h.updated_at, "status": h.status,
                "evidence_links": h.evidence_links, "tags": h.tags,
                "resolution_note": h.resolution_note,
            }
            return h
        return self._locked_update(_mutate)

    def list_all(self, status: str | None = None) -> list[Hypothesis]:
        index = self._load_index()
        result = []
        for hid, d in index.items():
            h = Hypothesis(
                id=hid, statement=d.get("statement", ""),
                created_at=d.get("created_at", ""), updated_at=d.get("updated_at", ""),
                status=d.get("status", "open"), evidence_links=d.get("evidence_links", []),
                tags=d.get("tags", []), resolution_note=d.get("resolution_note", ""),
            )
            if status and h.status != status:
                continue
            result.append(h)
        return sorted(result, key=lambda h: h.created_at, reverse=True)

    def update_status(self, hypothesis_id: str, new_status: str, note: str = "") -> Hypothesis | None:
        if new_status not in STATUSES:
            raise ValueError(f"Invalid status: {new_status}")
        def _mutate(index):
            if hypothesis_id not in index:
                return None
            d = index[hypothesis_id]
            d["status"] = new_status
            d["updated_at"] = datetime.now(timezone.utc).isoformat()
            if note:
                d["resolution_note"] = note
            return Hypothesis(
                id=hypothesis_id, statement=d["statement"],
                created_at=d["created_at"], updated_at=d["updated_at"],
                status=d["status"], evidence_links=d.get("evidence_links", []),
                tags=d.get("tags", []), resolution_note=d.get("resolution_note", ""),
            )
        return self._locked_update(_mutate)

    def add_evidence(self, hypothesis_id: str, run_id: str) -> None:
        def _mutate(index):
            if hypothesis_id not in index:
                return
            links = index[hypothesis_id].setdefault("evidence_links", [])
            if run_id not in links:
                links.append(run_id)
                index[hypothesis_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._locked_update(_mutate)


def auto_check_hypotheses(
    store: HypothesisStore,
    run_id: str,
    action_cards: list[dict],
) -> list[dict]:
    """根据当前 run 的 action_cards 自动检查相关论点。

    匹配规则：
    - 论点 tags 命中 action_card 中有对应标签的持仓 → 添加 evidence_link
    - 暂不做自动状态变更（需要人工判断）

    Returns: 本次关联到证据的论点列表。
    """
    open_hypotheses = store.list_all(status="open")
    if not open_hypotheses:
        return []

    # 从 action_cards 提取所有相关标签
    run_tags: set[str] = set()
    for card in action_cards:
        # 从 action 文本和 signal 中提取关键词
        text = f"{card.get('signal', '')} {card.get('action', '')}".lower()
        tag_map = {
            "gold": "黄金", "mining": "黄金",
            "tech": "科技", "ai": "AI", "semiconductor": "半导体",
            "rates": "利率", "bond": "债券", "fixed_income": "固收",
            "a_share": "A股", "us_equity": "美股",
            "energy": "能源", "oil": "能源",
            "defense": "军工", "aerospace": "军工",
            "dividend": "红利", "high_dividend": "红利",
        }
        for tag, keyword in tag_map.items():
            if keyword in text or tag in text:
                run_tags.add(tag)

    matched = []
    for h in open_hypotheses:
        if any(t in run_tags for t in h.tags):
            store.add_evidence(h.id, run_id)
            matched.append({
                "hypothesis_id": h.id,
                "statement": h.statement,
                "matched_tags": [t for t in h.tags if t in run_tags],
            })

    return matched


def format_hypothesis_report(hypotheses: list[Hypothesis]) -> str:
    """生成论点追踪报告。"""
    lines = ["## 研究论点追踪", ""]
    by_status: dict[str, list[Hypothesis]] = {}
    for h in hypotheses:
        by_status.setdefault(h.status, []).append(h)

    for status in ["open", "confirmed", "invalidated", "stale"]:
        items = by_status.get(status, [])
        if not items:
            continue
        emoji = {"open": "🔬", "confirmed": "✅", "invalidated": "❌", "stale": "⏳"}
        lines.append(f"### {emoji.get(status, '')} {status} ({len(items)})")
        for h in items:
            lines.append(f"- [{h.id[:8]}] {h.statement}")
            if h.evidence_links:
                lines.append(f"  - 证据: {len(h.evidence_links)} 次相关分析")
            if h.resolution_note:
                lines.append(f"  - 备注: {h.resolution_note}")
        lines.append("")
    return "\n".join(lines)
