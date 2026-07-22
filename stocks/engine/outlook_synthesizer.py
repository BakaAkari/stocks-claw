"""Constrained OpenAI-compatible outlook synthesizer with cache.

Produces validated structured outlooks via an OpenAI-compatible endpoint,
with retry-on-temperature-error, fenced-JSON extraction, atomic file cache,
and graceful degradation to sanitized-unavailable on any failure.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from stocks.engine.outlook_evidence import evidence_hash
from stocks.engine.outlook_validation import (
    sanitize_unavailable_outlook,
    validate_structured_outlook,
)

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "structured_outlook_prompt.txt"

# ── Cache TTL ───────────────────────────────────────────────────────────────
NEAR_TERM_TTL_SECONDS = 86400  # 24 hours
OUTLOOK_SCHEMA_VERSION = 2
OUTLOOK_VALIDATOR_VERSION = 2


class OutlookCache:
    """Atomic JSON cache for outlook results, keyed by *(session, evidence_hash)*.

    Each file is written atomically via a ``.tmp`` suffix then replaced.
    The cache envelope stores ``_cached_at``, ``session``, ``evidence_hash``,
    and ``outlook`` — the outlook dict itself does NOT carry ``_evidence_hash``.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session: str, evidence_hash_str: str) -> Path:
        return self.root / f"{session}__{evidence_hash_str}.json"

    def load(
        self,
        session: str,
        evidence_hash: str | None = None,
        contract_hash: str | None = None,
    ) -> dict | None:
        """Load a cached outlook.

        Parameters
        ----------
        session : str
            Session identifier.
        evidence_hash : str | None
            When provided, performs an exact file lookup by hash — fast path.
            When *None* (default), scans all files for *session* and returns
            the first non-expired cache entry (backward-compatible scan).

        Returns
        -------
        dict | None
            The cached outlook dict (without ``_evidence_hash``), or *None*.
        """
        # ── Exact hash lookup ──────────────────────────────────────────
        if evidence_hash is not None:
            path = self._path(session, evidence_hash)
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
            cached_at = data.get("_cached_at", 0)
            if time.time() - cached_at > NEAR_TERM_TTL_SECONDS:
                return None
            if contract_hash is not None and data.get("contract_hash") != contract_hash:
                return None
            return data.get("outlook")

        # ── Scan (backward-compatible) ─────────────────────────────────
        if not self.root.exists():
            return None
        prefix = f"{session}__"
        for fpath in self.root.iterdir():
            if not fpath.name.startswith(prefix) or fpath.suffix != ".json":
                continue
            try:
                data = json.loads(fpath.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            cached_at = data.get("_cached_at", 0)
            if time.time() - cached_at > NEAR_TERM_TTL_SECONDS:
                continue
            if contract_hash is not None and data.get("contract_hash") != contract_hash:
                continue
            return data.get("outlook")
        return None

    def save(
        self,
        session: str,
        evidence_hash_str: str,
        contract_hash: str | dict,
        outlook: dict | None = None,
    ) -> None:
        """Atomically write *outlook* to the cache.

        Writes to a ``.tmp`` file first, then ``os.replace`` to the final
        path, and cleans up any leftover ``.tmp`` in a ``finally`` block.
        """
        if outlook is None:
            outlook = contract_hash if isinstance(contract_hash, dict) else {}
            contract_hash = "legacy"
        data: dict[str, Any] = {
            "_cached_at": time.time(),
            "session": session,
            "evidence_hash": evidence_hash_str,
            "contract_hash": str(contract_hash),
            "outlook": outlook,
        }
        path = self._path(session, evidence_hash_str)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), "utf-8")
        try:
            os.replace(str(tmp), str(path))
        finally:
            if tmp.exists():
                tmp.unlink()


class OutlookSynthesizer:
    """Constrained OpenAI-compatible outlook synthesizer.

    Parameters
    ----------
    config : dict
        Engine configuration (``config["llm"]["outlook"]`` is consumed).
    transport : Callable | None
        Optional injected transport for testing.  Signature::

            transport(request: dict) -> dict

        Returns an OpenAI Chat Completions response dict.
    """

    def __init__(
        self,
        config: dict,
        *,
        transport: Callable[[dict], dict] | None = None,
    ) -> None:
        outlook_cfg = config.get("llm", {}).get("outlook", {})
        self.enabled: bool = outlook_cfg.get("enabled", True)
        self.model: str = outlook_cfg.get("model", "deepseek-v4-pro")
        self.api_key_env: str = outlook_cfg.get("api_key_env", "OPENAI_COMPATIBLE_API_KEY")
        self.base_url_env: str = outlook_cfg.get("base_url_env", "OPENAI_COMPATIBLE_BASE_URL")
        self.fallback_base_url: str = outlook_cfg.get(
            "fallback_base_url", "http://100.121.167.1:8317/v1"
        )
        self.timeout: int = outlook_cfg.get("timeout_seconds", 120)
        self.temperature: float = outlook_cfg.get("temperature", 0.2)
        self.max_tokens: int = outlook_cfg.get("max_tokens", 3000)
        cache_dir_setting: str = outlook_cfg.get("cache_dir", ".local/outlook_cache")
        cache_path = Path(cache_dir_setting)
        if not cache_path.is_absolute():
            cache_path = Path(__file__).resolve().parents[2] / cache_dir_setting
        self.cache = OutlookCache(cache_path)
        self._transport = transport

        # Load prompt text
        self._prompt_text = (
            _PROMPT_PATH.read_text("utf-8") if _PROMPT_PATH.exists() else ""
        )
        self._cache_contract_hash = self._compute_cache_contract_hash()

        # Resolve API key and base URL
        self._api_key: str | None = None
        self._base_url: str | None = None
        self._resolve_credentials(config)

    def _compute_cache_contract_hash(self) -> str:
        payload = {
            "prompt": self._prompt_text,
            "schema_version": OUTLOOK_SCHEMA_VERSION,
            "validator_version": OUTLOOK_VALIDATOR_VERSION,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ── Public API ──────────────────────────────────────────────────────────

    def generate(self, evidence: dict, *, now: str) -> dict:
        """Generate a structured outlook for the given *evidence*.

        Returns a validated outlook (``status == "ok"``) or a sanitized
        unavailable dict on any failure.
        """
        # ── Disabled / missing key → unavailable ──────────────────────────
        if not self.enabled or not self._api_key:
            return sanitize_unavailable_outlook(
                ["outlook synthesizer disabled or not configured"],
                generated_at=now,
            )

        # ── Check cache (exact hash lookup) ──────────────────────────────
        e_hash = evidence_hash(evidence)
        session = evidence.get("session", "unknown")
        cached = self.cache.load(session, e_hash, self._cache_contract_hash)
        if cached is not None:
            errors = validate_structured_outlook(cached, evidence)
            if not errors:
                logger.info("Outlook cache hit for session=%s", session)
                return cached
            logger.warning("Ignoring invalid cached outlook for %s: %s", session, errors)

        # ── Build request ────────────────────────────────────────────────
        request = self._build_request(evidence)

        # ── Call transport ───────────────────────────────────────────────
        response = self._call_transport(request)

        # ── Parse ────────────────────────────────────────────────────────
        outlook = self._parse_response(response, now)
        if outlook is None and self._is_truncated_response(response):
            logger.info("Retrying truncated outlook with larger token budget")
            request["max_tokens"] = max(self.max_tokens, 8000)
            response = self._call_transport(request)
            outlook = self._parse_response(response, now)
        if outlook is None and self._should_retry_temperature(response):
            logger.info("Retrying with temperature=1")
            request["temperature"] = 1.0
            response = self._call_transport(request)
            outlook = self._parse_response(response, now)

        # ── Validate (with system fields forced before check) ────────────
        if outlook is not None:
            # Force system-controlled fields; model output for these is
            # ignored to prevent placeholder values from failing validation.
            outlook = dict(outlook)
            outlook["status"] = "ok"
            outlook["generated_at"] = now

            errors = validate_structured_outlook(outlook, evidence)
            if errors:
                logger.warning("Outlook validation failed: %s", errors)
                retry_request = self._build_validation_retry_request(evidence, errors)
                retry_response = self._call_transport(retry_request)
                retry_outlook = self._parse_response(retry_response, now)
                if retry_outlook is not None:
                    retry_outlook = dict(retry_outlook)
                    retry_outlook["status"] = "ok"
                    retry_outlook["generated_at"] = now
                    retry_errors = validate_structured_outlook(retry_outlook, evidence)
                    if not retry_errors:
                        outlook = retry_outlook
                        errors = []
                    else:
                        logger.warning("Outlook validation retry failed: %s", retry_errors)
                        errors = retry_errors
                if errors:
                    outlook = sanitize_unavailable_outlook(errors, generated_at=now)
            if not errors:
                outlook["status"] = "ok"
                outlook["generated_at"] = now
                self.cache.save(session, e_hash, self._cache_contract_hash, outlook)
        else:
            outlook = sanitize_unavailable_outlook(
                ["LLM returned no valid JSON"],
                generated_at=now,
            )

        return outlook

    # ── Internal helpers ────────────────────────────────────────────────────

    def _resolve_credentials(self, config: dict) -> None:
        """Resolve API key and base URL from env vars or secret env file."""
        key = os.environ.get(self.api_key_env, "").strip()
        url = os.environ.get(self.base_url_env, "").strip()

        if not key or not url:
            secret_env_file = config.get("paths", {}).get("secret_env_file")
            if secret_env_file:
                env_path = Path(secret_env_file)
                if env_path.exists():
                    for line in env_path.read_text("utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("\"'")
                        if k == self.api_key_env and not key:
                            key = v
                        if k == self.base_url_env and not url:
                            url = v

        self._api_key = key or None
        base = (url or self.fallback_base_url).rstrip("/")
        self._base_url = f"{base}/chat/completions"

    def _build_request(self, evidence: dict) -> dict:
        """Build the OpenAI-compatible request dict."""
        messages = [
            {"role": "system", "content": self._prompt_text},
            {
                "role": "user",
                "content": json.dumps(evidence, ensure_ascii=False, default=str),
            },
        ]
        return {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }

    def _build_validation_retry_request(self, evidence: dict, errors: list[str]) -> dict:
        request = self._build_request(evidence)
        feedback = {
            "VALIDATION_ERRORS": errors[:8],
            "instruction": "修正这些错误后重新输出完整 JSON；仍只使用原证据，不增加新事实。",
        }
        request["messages"] = list(request["messages"]) + [
            {"role": "user", "content": json.dumps(feedback, ensure_ascii=False)},
        ]
        return request

    def _call_transport(self, request: dict) -> dict | None:
        """Call the transport (injected callable or real HTTP).

        Real HTTP path specifically handles ``urllib.error.HTTPError`` by
        reading and parsing the response body, so ``_should_retry_temperature``
        can detect temperature errors from production API responses.
        """
        if self._transport is not None:
            try:
                return self._transport(request)
            except Exception as exc:
                logger.warning("Transport call failed: %s", exc)
                return None

        # Real HTTP call via urllib
        try:
            data = json.dumps(request, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self._base_url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as http_err:
            # Read response body so retry logic can inspect the error
            try:
                body = http_err.read().decode("utf-8")
                error_data = json.loads(body)
                logger.warning(
                    "LLM API returned HTTP %s: %s",
                    http_err.code,
                    error_data.get("error", {}).get("message", body),
                )
                return error_data
            except (json.JSONDecodeError, OSError):
                logger.warning(
                    "LLM API returned HTTP %s with unparseable body",
                    http_err.code,
                )
                return None
        except Exception as exc:
            logger.warning("LLM API call failed: %s", exc)
            return None

    @staticmethod
    def _is_truncated_response(response: dict | None) -> bool:
        if not isinstance(response, dict):
            return False
        choices = response.get("choices") or []
        return bool(choices and choices[0].get("finish_reason") == "length")

    @staticmethod
    def _parse_response(
        response: dict | None, now: str  # noqa: ARG004
    ) -> dict | None:
        """Parse JSON from an OpenAI-compatible chat completion response."""
        if response is None:
            return None
        try:
            choices = response.get("choices", [])
            if not choices:
                return None
            message = choices[0].get("message", {})
            content = (message.get("content") or "").strip()
            if not content:
                content = (message.get("reasoning_content") or "").strip()
            if not content:
                return None
            # Some compatible/reasoning models wrap JSON in a code fence or
            # prefix it with a short explanatory sentence despite response_format.
            if content.startswith("```"):
                content = OutlookSynthesizer._extract_fenced_json(content)
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                start = content.find("{")
                end = content.rfind("}")
                if start < 0 or end <= start:
                    raise
                return json.loads(content[start : end + 1])
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            logger.warning("Failed to parse LLM response: %s", exc)
            return None

    @staticmethod
    def _extract_fenced_json(text: str) -> str:
        """Extract JSON from within a `````json`` fenced code block."""
        lines = text.splitlines()
        start = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("```"):
                if start == -1:
                    start = i
                else:
                    return "\n".join(lines[start + 1 : i])
        return "\n".join(lines[start + 1 :]) if start >= 0 else text

    @staticmethod
    def _should_retry_temperature(response: dict | None) -> bool:
        """Check whether the API response explicitly requires temperature=1."""
        if response is None:
            return False
        error = response.get("error", {})
        if isinstance(error, dict):
            msg = (error.get("message") or "").lower()
            if "temperature" in msg and (
                "=1" in msg or "must be 1" in msg or "only support" in msg or "must equal 1" in msg
            ):
                return True
        return False
