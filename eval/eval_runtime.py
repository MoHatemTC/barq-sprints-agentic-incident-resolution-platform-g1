"""Persistent response cache and budget accounting for the offline eval harness."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import openai


class BudgetExceeded(RuntimeError):
    """No more requests may be dispatched within the configured allowance."""


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class Checkpoint:
    """Keep paid responses independently of thresholds, rubrics and report metadata."""

    def __init__(self, path: Path):
        self.path = path
        self.data = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"version": 1, "generations": {}, "responses": {}, "calls": []}
        )
        if self.data.get("version") != 1 or any(
            key not in self.data for key in ("generations", "responses", "calls")
        ):
            raise ValueError(f"Unsupported checkpoint: {path}; use a different --output")

    def save(self) -> None:
        atomic_json(self.path, self.data)


class BudgetedLLM:
    """Reserve before dispatch; persist even failed/ambiguous requests.

    The USD limit is based on proxy headers when available and configured token
    rates otherwise. It is not a provider-enforced dollar limit if those rates
    differ from proxy billing. SDK retries must be disabled on the supplied client.
    """

    def __init__(self, client, checkpoint: Checkpoint, budget_usd: float, max_calls: int):
        self.client = client
        self.checkpoint = checkpoint
        self.budget_usd = budget_usd
        self.max_calls = max_calls
        self.first_call = len(checkpoint.data["calls"])

    @property
    def accounted_usd(self) -> float:
        return sum(call["accounted_usd"] for call in self.checkpoint.data["calls"])

    def complete(
        self,
        *,
        stage: str,
        turn_id: str,
        model: str,
        messages: list[dict],
        max_tokens: int,
        input_rate: float,
        output_rate: float,
        reasoning_effort: str | None = None,
        json_output: bool = False,
        cache_salt: str = "",
    ) -> dict:
        options = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": max_tokens,
        }
        if reasoning_effort:
            options["reasoning_effort"] = reasoning_effort
        if json_output:
            options["response_format"] = {"type": "json_object"}
        key = fingerprint({"options": options, "cache_salt": cache_salt})
        cached = self.checkpoint.data["responses"].get(key)
        if cached is not None:
            return cached

        # UTF-8 bytes give a deliberately conservative input-token allowance.
        # Include room for message framing and the JSON response-format instruction.
        input_bound = sum(len(m["content"].encode("utf-8")) + 64 for m in messages) + 256
        reservation = (input_bound * input_rate + max_tokens * output_rate) / 1_000_000
        if len(self.checkpoint.data["calls"]) >= self.max_calls:
            raise BudgetExceeded(f"Call limit ({self.max_calls}) reached")
        if self.accounted_usd + reservation > self.budget_usd:
            raise BudgetExceeded(
                f"Next {stage} request would exceed ${self.budget_usd:.2f}: "
                f"${self.accounted_usd:.4f} accounted + ${reservation:.4f} reserved"
            )
        call = {
            "stage": stage,
            "turn_id": turn_id,
            "model": model,
            "accounted_usd": reservation,
            "proxy_cost_usd": None,
            "cost_source": "reservation",
            "status": "in_flight",
            "prompt_tokens": None,
            "completion_tokens": None,
            "input_rate_per_million": input_rate,
            "output_rate_per_million": output_rate,
        }
        self.checkpoint.data["calls"].append(call)
        self.checkpoint.save()
        try:
            raw = self.client.chat.completions.with_raw_response.create(**options)
            header = raw.headers.get("x-litellm-response-cost")
            try:
                cost = float(header)
            except (TypeError, ValueError):
                cost = None
            if cost is not None and math.isfinite(cost) and cost >= 0:
                call.update(proxy_cost_usd=cost, accounted_usd=cost, cost_source="proxy_header")
            response = raw.parse()
            if response.usage is not None:
                # Prefer the LiteLLM header; never count usage.cost twice.
                usage_cost = getattr(response.usage, "cost", None)
                if usage_cost is None:
                    usage_cost = (getattr(response.usage, "model_extra", None) or {}).get("cost")
                try:
                    usage_cost = float(usage_cost)
                except (TypeError, ValueError):
                    usage_cost = None
                if (
                    call["proxy_cost_usd"] is None
                    and usage_cost is not None
                    and math.isfinite(usage_cost)
                    and usage_cost >= 0
                ):
                    call.update(
                        proxy_cost_usd=usage_cost,
                        accounted_usd=usage_cost,
                        cost_source="usage_cost",
                    )
                prompt_tokens = response.usage.prompt_tokens
                completion_tokens = response.usage.completion_tokens
                call.update(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
                if call["proxy_cost_usd"] is None and all(
                    isinstance(n, int) and n >= 0 for n in (prompt_tokens, completion_tokens)
                ):
                    call.update(
                        accounted_usd=(prompt_tokens * input_rate + completion_tokens * output_rate)
                        / 1_000_000,
                        cost_source="token_estimate",
                    )
            choice = response.choices[0]
            result = {
                "content": choice.message.content,
                "finish_reason": choice.finish_reason,
            }
            self.checkpoint.data["responses"][key] = result
            call["status"] = "received"
            self.checkpoint.save()
            return result
        except Exception as exc:
            # Explicit rejected requests are unbilled. For timeouts/5xx retain
            # the reservation: the upstream may have completed and charged.
            if isinstance(exc, openai.APIStatusError) and exc.status_code in (
                400,
                401,
                403,
                404,
                422,
                429,
            ):
                call.update(accounted_usd=0.0, cost_source="rejected")
            call["status"] = "error"
            self.checkpoint.save()
            raise

    def usage_summary(self) -> dict:
        calls = self.checkpoint.data["calls"]
        new_calls = calls[self.first_call :]

        def observed(items):
            return (
                round(sum(c["proxy_cost_usd"] for c in items), 6)
                if all(c["proxy_cost_usd"] is not None for c in items)
                else None
            )

        return {
            "spend_this_run_usd": observed(new_calls),
            "observed_proxy_spend_usd": observed(calls),
            "known_proxy_spend_usd": round(sum(c["proxy_cost_usd"] or 0 for c in calls), 6),
            "accounted_spend_usd": round(self.accounted_usd, 6),
            "llm_calls_this_run": len(new_calls),
            "llm_calls_total": len(calls),
            "calls_without_proxy_cost": sum(c["proxy_cost_usd"] is None for c in calls),
            "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in calls),
            "completion_tokens": sum(c["completion_tokens"] or 0 for c in calls),
            "budget_usd": self.budget_usd,
        }
