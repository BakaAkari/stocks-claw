from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from stocks.errors import FinancialMemoryError
from stocks.logging_utils import log_event

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY_PATH = ROOT / 'data' / 'financial_assets.json'


class FinancialMemoryService:
    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_MEMORY_PATH
        self._cache: dict | None = None
        self._mtime: float = 0.0

    def load(self) -> dict:
        # 热重载：检查文件修改时间
        try:
            current_mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            current_mtime = 0.0
        
        if self._cache is not None and current_mtime == self._mtime:
            return self._cache
        
        if not self.path.exists():
            self._cache = {
                'schema_version': 1,
                'updated_at': None,
                'assets': [],
                'portfolio_constraints': {},
            }
            self._mtime = current_mtime
            return self._cache
        
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            raise FinancialMemoryError(f'读取金融记忆失败: {e}') from e

        if not isinstance(data, dict):
            raise FinancialMemoryError('金融记忆文件格式错误: 顶层必须是对象')

        assets = data.get('assets')
        if assets is None:
            data['assets'] = []
        elif not isinstance(assets, list):
            raise FinancialMemoryError('金融记忆文件格式错误: assets 必须是数组')

        # Ensure portfolio_constraints exists
        if 'portfolio_constraints' not in data:
            data['portfolio_constraints'] = {}

        self._cache = data
        self._mtime = current_mtime
        return self._cache

    def save(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise FinancialMemoryError('保存金融记忆失败: payload 必须是对象')
        payload = dict(payload)
        payload['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        payload.setdefault('schema_version', 1)
        payload.setdefault('assets', [])

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write('\n')
        except Exception as e:
            raise FinancialMemoryError(f'保存金融记忆失败: {e}') from e

        log_event('financial_memory.saved', path=str(self.path), assets=len(payload.get('assets', [])))
        # 保存后更新缓存和mtime，避免下次重复读取
        self._cache = payload
        try:
            self._mtime = self.path.stat().st_mtime
        except Exception:
            self._mtime = 0.0

    def list_assets(self) -> list[dict]:
        payload = self.load()
        return payload.get('assets', [])

    def load_constraints(self) -> dict:
        """读取用户投资组合约束"""
        payload = self.load()
        constraints = payload.get('portfolio_constraints', {})
        if not isinstance(constraints, dict):
            return {}
        return constraints

    def save_constraints(self, constraints: dict) -> None:
        """保存用户投资组合约束（会覆盖原有约束）"""
        if not isinstance(constraints, dict):
            raise FinancialMemoryError('约束必须是字典类型')
        
        payload = self.load()
        payload['portfolio_constraints'] = constraints
        self.save(payload)
        log_event('financial_memory.constraints_saved', 
                  has_ranges=bool(constraints.get('target_bucket_ranges')),
                  locked_count=len(constraints.get('locked_assets', [])))

    def update_constraints(self, updates: dict) -> dict:
        """增量更新约束字段（保留未更新的字段）"""
        if not isinstance(updates, dict):
            raise FinancialMemoryError('更新必须是字典类型')
        
        constraints = self.load_constraints()
        constraints.update(updates)
        constraints['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        constraints['schema_version'] = 1
        self.save_constraints(constraints)
        return constraints
