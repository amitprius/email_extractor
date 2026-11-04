"""Gemini maps ONLY the target columns that exact/fuzzy matching could not resolve.

* Fixed prompt (constants.FIXED_PROMPT) + structured JSON output, temperature 0.
* Tries each model in settings.GEMINI_MODELS in turn; retries with back-off; never needs a human.
* If every attempt fails, the run continues with the columns matched so far.
* Every call's token usage is recorded.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from pydantic import BaseModel

import constants

from .logger import RunLogger, TokenUsage


class Pick(BaseModel):
    target: str
    source: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""


class SheetResult(BaseModel):
    sheet_id: str
    picks: list[Pick]


class AIResponse(BaseModel):
    sheets: list[SheetResult]


def _payload(chunk: list[dict]) -> str:
    return json.dumps({"target_columns": constants.TARGET_COLUMNS, "sheets": chunk}, ensure_ascii=False)


def _call(client: Any, model: str, payload: str):
    from google.genai import types  # imported here so tests can run with a stub client
    return client.models.generate_content(
        model=model, contents=payload,
        config=types.GenerateContentConfig(system_instruction=constants.FIXED_PROMPT, temperature=0,
                                           response_mime_type="application/json", response_schema=AIResponse))


def _is_permanent(err: Exception) -> bool:
    s = str(err).lower()
    return any(k in s for k in ("not found", "404", "permission", "api key", "invalid argument", "unsupported"))


def _ask(client: Any, models: list, chunk: list[dict], tokens: TokenUsage, log: RunLogger) -> Optional[AIResponse]:
    payload = _payload(chunk)
    for model in models:
        for attempt in range(1, constants.AI_RETRIES + 1):
            t0 = time.time()
            try:
                resp = _call(client, model, payload)
                parsed = getattr(resp, "parsed", None)
                result = parsed if isinstance(parsed, AIResponse) else AIResponse.model_validate_json(resp.text)
                rec = tokens.add(model, getattr(resp, "usage_metadata", None), len(chunk), time.time() - t0)
                log.info(f"   AI call #{rec['call']} ({model}, {len(chunk)} sheet(s)): tokens in={rec['prompt_tokens']} "
                         f"out={rec['output_tokens']} thinking={rec['thinking_tokens']} total={rec['total_tokens']} "
                         f"| running total = {tokens.totals['total_tokens']}")
                return result
            except Exception as e:
                log.warn(f"   AI attempt {attempt}/{constants.AI_RETRIES} on {model} failed: {type(e).__name__}: {str(e)[:160]}")
                if _is_permanent(e):
                    break                       # a wrong model name will not fix itself: go to the next model
                time.sleep(min(2 ** attempt, 20))
        log.warn(f"   switching away from model {model}")
    return None


def resolve(units: list[dict], unresolved: dict, assigned: dict, api_key: str, models: list,
            tokens: TokenUsage, log: RunLogger, client: Any = None) -> dict:
    """units: sheets with unresolved targets. Returns {unit_id: {target: {source, score, method, reason}}}."""
    if client is None:
        from google import genai
        client = genai.Client(api_key=api_key)

    found: dict = {}
    n = constants.AI_SHEETS_PER_CALL
    for i in range(0, len(units), n):
        group = units[i:i + n]
        chunk = []
        for u in group:
            used = {a["source"] for a in assigned[u["unit_id"]].values()}
            chunk.append({"sheet_id": u["unit_id"], "unresolved_targets": unresolved[u["unit_id"]],
                          "available_columns": [{"name": c, "samples": u["samples"].get(c, [])[:constants.AI_SAMPLE_VALUES]}
                                                for c in u["columns"] if c not in used]})
        resp = _ask(client, models, chunk, tokens, log)
        if resp is None:
            log.error("   AI matching failed for this batch; continuing without it")
            continue
        by_id = {u["unit_id"]: u for u in group}
        for sr in resp.sheets:
            u = by_id.get(sr.sheet_id)
            if not u:
                continue
            used = {a["source"] for a in assigned[u["unit_id"]].values()}
            for p in sr.picks:
                label = f"{u['file']['filename']} › '{u['sheet']}'"
                if p.target not in unresolved[u["unit_id"]] or not p.source:
                    continue
                if p.source not in u["columns"]:
                    log.warn(f"   AI proposed unknown column '{p.source}' for {p.target} in {label}: rejected")
                elif p.source in used:
                    log.warn(f"   AI reused column '{p.source}' for {p.target} in {label}: rejected")
                elif p.confidence < constants.AI_MIN_CONFIDENCE:
                    log.warn(f"   AI low confidence ({p.confidence:.2f}) for {p.target} ← '{p.source}' in {label}: rejected")
                else:
                    used.add(p.source)
                    found.setdefault(u["unit_id"], {})[p.target] = {"source": p.source, "score": round(p.confidence, 3),
                                                                    "method": "ai", "reason": p.reason}
    return found
