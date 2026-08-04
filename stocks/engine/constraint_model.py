"""M4 constraint model: irreversibility, segregated pools, hard caps.

Extends the legacy four-bucket min/max ratio constraints with three
semantics that real portfolio decisions depend on:

1. **Irreversibility** (``position_restrictions.<ref>.no_buyback``) — a
   position that cannot be bought back once sold makes every sell
   suggestion carry an irreversibility warning, and soft take-profit
   suggestions on it are suppressed.
2. **Segregated pools** (``pools`` + ``position_pool``/``account_pool``) —
   positions belong to named pools; isolated pools never fund another
   pool's purchases, and ratio checks run per pool.
3. **Hard caps** (``hard_caps``) — a category cap with
   ``on_breach: must_reduce`` produces a mandatory reduce candidate naming
   the cap, even when no technical signal fires.

Everything here is data-driven: the schema lives in code, the values are
user-confirmed financial memory loaded from ``.local/portfolio_constraints.json``
(with the repo's ``stocks/config/portfolio_constraints.json`` as example
fallback).  Validation fails closed: unknown keys, bad types, or
references to undefined pools raise :class:`ConstraintConfigError`.

Schema (JSON, all M4 keys optional; legacy bucket keys preserved)::

    {
      "权益": {"min": 0.25, "max": 0.65},          // legacy soft buckets
      "固收": {"min": 0.15, "max": 0.5},
      "pools": {                                    // M4: named pools
        "domestic": {"label": "国内池", "currency": "CNY"},
        "overseas": {"label": "海外封闭池", "currency": "USD",
                     "isolated": true}
      },
      "position_pool": {"<position_id>": "overseas"},
      "account_pool": {"<account_id>": "overseas"},  // fallback mapping
      "bucket_limits": {                            // optional per-pool ratios
        "domestic": {"权益": {"min": 0.25, "max": 0.65}},
        "overseas": {"权益": {"min": 0.0, "max": 1.0}}
      },
      "hard_caps": [
        {"pool": "domestic", "category": "nasdaq100", "max": 0.12,
         "on_breach": "must_reduce",
         "reason": "限购无法买回，超上限必须减"}
      ],
      "position_restrictions": {
        "<position_id or instrument_key>": {
          "no_buyback": true,
          "restriction_note": "平台每日限购极低额度，卖出后事实不可买回"
        }
      }
    }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Top-level keys allowed besides legacy bucket rules (bucket rules are
# dicts with numeric min/max whose key names are user-defined labels).
_M4_TOP_LEVEL_KEYS = {
    "pools",
    "position_pool",
    "account_pool",
    "bucket_limits",
    "hard_caps",
    "position_restrictions",
}

_POOL_KEYS = {"label", "currency", "isolated"}
_RESTRICTION_KEYS = {"no_buyback", "restriction_note"}
_HARD_CAP_KEYS = {"pool", "category", "max", "on_breach", "reason"}
_BUCKET_RULE_KEYS = {"min", "max"}
_ON_BREACH_VALUES = {"must_reduce"}

# Pool assigned to positions with no explicit mapping.
DEFAULT_POOL = "domestic"


class ConstraintConfigError(ValueError):
    """Raised when the constraints config is malformed (fail closed)."""


def iter_bucket_rules(constraints: dict):
    """Yield ``(bucket_name, rule)`` for legacy bucket-ratio rules only.

    M4 extended keys (pools/hard_caps/position_restrictions/…) are NOT
    bucket rules; consumers that predate M4 must use this helper instead
    of iterating ``constraints.items()`` raw, or they will crash on
    non-rule values (lists, pool maps) or treat them as phantom buckets.
    """
    for name, rule in (constraints or {}).items():
        if name in _M4_TOP_LEVEL_KEYS or str(name).startswith("_"):
            continue
        if not isinstance(rule, dict):
            continue
        if "min" not in rule and "max" not in rule:
            continue
        yield name, rule


def _err(msg: str) -> ConstraintConfigError:
    return ConstraintConfigError(f"portfolio_constraints: {msg}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_bucket_rule(rule: Any, *, where: str) -> dict:
    if not isinstance(rule, dict):
        raise _err(f"{where} must be an object with min/max")
    unknown = set(rule) - _BUCKET_RULE_KEYS
    if unknown:
        raise _err(f"{where} has unknown keys: {sorted(unknown)}")
    for key in ("min", "max"):
        value = rule.get(key)
        if value is not None and not _is_number(value):
            raise _err(f"{where}.{key} must be a number or null")
    min_v, max_v = rule.get("min"), rule.get("max")
    if min_v is not None and max_v is not None and min_v > max_v:
        raise _err(f"{where}: min ({min_v}) > max ({max_v})")
    return {"min": min_v, "max": max_v}


def validate_constraints(data: Any) -> dict:
    """Validate and normalize the constraints config. Fail closed.

    Returns a normalized dict with guaranteed M4 keys:
    ``pools`` / ``position_pool`` / ``account_pool`` / ``bucket_limits`` /
    ``hard_caps`` / ``position_restrictions`` plus legacy bucket rules
    under their original top-level names.
    """
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise _err("top level must be an object")

    normalized: dict[str, Any] = {}

    # Legacy bucket rules (top-level dicts with min/max).  Keys starting
    # with "_" are documentation/comment keys and ignored by design.
    for key, value in data.items():
        if key in _M4_TOP_LEVEL_KEYS or key.startswith("_"):
            continue
        normalized[key] = _validate_bucket_rule(value, where=f'bucket "{key}"')

    pools = data.get("pools") or {}
    if not isinstance(pools, dict):
        raise _err("pools must be an object")
    pool_defs: dict[str, dict] = {}
    for name, spec in pools.items():
        if not isinstance(name, str) or not name.strip():
            raise _err("pool names must be non-empty strings")
        if not isinstance(spec, dict):
            raise _err(f'pools."{name}" must be an object')
        unknown = set(spec) - _POOL_KEYS
        if unknown:
            raise _err(f'pools."{name}" has unknown keys: {sorted(unknown)}')
        pool_defs[name] = {
            "label": str(spec.get("label") or name),
            "currency": str(spec.get("currency") or "CNY").upper(),
            "isolated": bool(spec.get("isolated", False)),
        }
    if pool_defs and DEFAULT_POOL not in pool_defs:
        raise _err(f'pools must define the default pool "{DEFAULT_POOL}"')
    normalized["pools"] = pool_defs

    for map_key in ("position_pool", "account_pool"):
        mapping = data.get(map_key) or {}
        if not isinstance(mapping, dict):
            raise _err(f"{map_key} must be an object")
        for ref, pool in mapping.items():
            if pool_defs and pool not in pool_defs:
                raise _err(f'{map_key}."{ref}" references undefined pool "{pool}"')
        normalized[map_key] = {str(k): str(v) for k, v in mapping.items()}

    bucket_limits = data.get("bucket_limits") or {}
    if not isinstance(bucket_limits, dict):
        raise _err("bucket_limits must be an object")
    limits: dict[str, dict] = {}
    for pool, rules in bucket_limits.items():
        if pool_defs and pool not in pool_defs:
            raise _err(f'bucket_limits."{pool}" references undefined pool')
        if not isinstance(rules, dict):
            raise _err(f'bucket_limits."{pool}" must be an object')
        limits[pool] = {
            name: _validate_bucket_rule(rule, where=f'bucket_limits."{pool}"."{name}"')
            for name, rule in rules.items()
        }
    normalized["bucket_limits"] = limits

    hard_caps = data.get("hard_caps") or []
    if not isinstance(hard_caps, list):
        raise _err("hard_caps must be an array")
    caps: list[dict] = []
    for index, cap in enumerate(hard_caps):
        where = f"hard_caps[{index}]"
        if not isinstance(cap, dict):
            raise _err(f"{where} must be an object")
        unknown = set(cap) - _HARD_CAP_KEYS
        if unknown:
            raise _err(f"{where} has unknown keys: {sorted(unknown)}")
        category = cap.get("category")
        if not isinstance(category, str) or not category.strip():
            raise _err(f'{where}.category must be a non-empty string')
        max_v = cap.get("max")
        if not _is_number(max_v) or not (0 < max_v <= 1):
            raise _err(f"{where}.max must be a number in (0, 1]")
        on_breach = cap.get("on_breach", "must_reduce")
        if on_breach not in _ON_BREACH_VALUES:
            raise _err(f"{where}.on_breach must be one of {sorted(_ON_BREACH_VALUES)}")
        pool = cap.get("pool")
        if pool is not None and pool_defs and pool not in pool_defs:
            raise _err(f'{where}.pool references undefined pool "{pool}"')
        caps.append({
            "pool": str(pool) if pool else None,
            "category": category.strip(),
            "max": float(max_v),
            "on_breach": on_breach,
            "reason": str(cap.get("reason") or f"{category} 超出硬上限"),
        })
    normalized["hard_caps"] = caps

    restrictions = data.get("position_restrictions") or {}
    if not isinstance(restrictions, dict):
        raise _err("position_restrictions must be an object")
    restr: dict[str, dict] = {}
    for ref, spec in restrictions.items():
        if not isinstance(spec, dict):
            raise _err(f'position_restrictions."{ref}" must be an object')
        unknown = set(spec) - _RESTRICTION_KEYS
        if unknown:
            raise _err(f'position_restrictions."{ref}" has unknown keys: {sorted(unknown)}')
        if "no_buyback" in spec and not isinstance(spec["no_buyback"], bool):
            raise _err(f'position_restrictions."{ref}".no_buyback must be bool')
        restr[str(ref)] = {
            "no_buyback": bool(spec.get("no_buyback", False)),
            "restriction_note": str(spec.get("restriction_note") or ""),
        }
    normalized["position_restrictions"] = restr

    return normalized


@dataclass(frozen=True)
class ConstraintModel:
    """Runtime view over a validated constraints config."""

    buckets: dict = field(default_factory=dict)          # legacy top-level rules
    pools: dict = field(default_factory=dict)
    position_pool: dict = field(default_factory=dict)
    account_pool: dict = field(default_factory=dict)
    bucket_limits: dict = field(default_factory=dict)
    hard_caps: tuple = ()
    position_restrictions: dict = field(default_factory=dict)

    @classmethod
    def from_config(cls, data: Optional[dict]) -> "ConstraintModel":
        normalized = validate_constraints(data or {})
        buckets = {
            k: v for k, v in normalized.items() if k not in _M4_TOP_LEVEL_KEYS
        }
        return cls(
            buckets=buckets,
            pools=normalized["pools"],
            position_pool=normalized["position_pool"],
            account_pool=normalized["account_pool"],
            bucket_limits=normalized["bucket_limits"],
            hard_caps=tuple(normalized["hard_caps"]),
            position_restrictions=normalized["position_restrictions"],
        )

    @property
    def has_pools(self) -> bool:
        return bool(self.pools)

    def pool_of(self, position_id: str, account_id: str = "") -> str:
        """Resolve the pool a position belongs to (position > account > default)."""
        if not self.pools:
            return DEFAULT_POOL
        pool = self.position_pool.get(position_id)
        if pool:
            return pool
        pool = self.account_pool.get(account_id)
        if pool:
            return pool
        return DEFAULT_POOL

    def is_isolated(self, pool: str) -> bool:
        return bool((self.pools.get(pool) or {}).get("isolated"))

    def pool_label(self, pool: str) -> str:
        return str((self.pools.get(pool) or {}).get("label") or pool)

    def bucket_rules_for(self, pool: str) -> dict:
        """Per-pool bucket limits when defined, else legacy global buckets."""
        if self.bucket_limits and pool in self.bucket_limits:
            return self.bucket_limits[pool]
        return self.buckets

    def restriction_for(self, position_id: str, instrument_key: str = "") -> dict:
        """Look up position restrictions by position_id or instrument_key."""
        spec = self.position_restrictions.get(position_id)
        if spec is None and instrument_key:
            spec = self.position_restrictions.get(instrument_key)
        return spec or {"no_buyback": False, "restriction_note": ""}

    def category_matches(self, category: str, tags: list[str], buckets: list[str]) -> bool:
        """A cap category matches a position via exposure tag or bucket name."""
        return category in tags or category in buckets
