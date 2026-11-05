"""The agent loop.

Flow per ticket:
  1. Claude reads the ticket and picks a tool.
  2. The tool input is validated (Pydantic). Bad input goes back as is_error,
     so Claude can fix it. Each failure spends one unit of the retry budget.
  3. Sensitive tools (refunds) stop and ask a human before running.
  4. The run ends when Claude calls submit_resolution with a valid payload.
  5. If the retry budget or the turn limit runs out, the ticket is escalated
     to a human instead of guessing.

Transient API errors (429, 5xx, timeouts) are retried by the Anthropic SDK
itself with exponential backoff (`max_retries`).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from .tools import BusinessRuleError, SubmitResolution, ToolBox, ToolInputError
from .trace import Trace

DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "claude-opus-4-8")

SYSTEM_PROMPT = """You are a support agent for Acme Home Supply, a small online home-goods store.

Resolve the customer's ticket using the tools. Rules:
- Always look up the order before making claims about it.
- Only refund delivered orders, and never more than the refundable balance.
  A human reviews every refund; if they reject it, explain politely and offer an alternative.
- Never invent order data, prices or delivery dates.
- Finish by calling submit_resolution exactly once. The customer_reply must be friendly,
  short and in the same language the customer wrote in."""


@dataclass
class RunResult:
    status: str  # "resolved" | "escalated"
    resolution: SubmitResolution | None
    reason: str = ""
    turns: int = 0
    tool_calls: list[str] = field(default_factory=list)
    run_id: str = ""


class SupportAgent:
    def __init__(
        self,
        client,
        toolbox: ToolBox,
        model: str = DEFAULT_MODEL,
        max_turns: int = 8,
        max_retries: int = 3,
        log_dir: str | None = "logs",
    ) -> None:
        self.client = client
        self.toolbox = toolbox
        self.model = model
        self.max_turns = max_turns
        self.max_retries = max_retries
        self.log_dir = log_dir

    def handle(self, ticket: str) -> RunResult:
        trace = Trace(self.log_dir)
        trace.log("ticket_received", ticket=ticket, model=self.model)
        messages: list[dict] = [{"role": "user", "content": ticket}]
        errors_left = self.max_retries
        tool_calls: list[str] = []

        for turn in range(1, self.max_turns + 1):
            started = time.perf_counter()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=self.toolbox.definitions(),
                thinking={"type": "adaptive"},
                messages=messages,
            )
            trace.log(
                "llm_call",
                turn=turn,
                stop_reason=response.stop_reason,
                latency_ms=round((time.perf_counter() - started) * 1000),
                input_tokens=getattr(response.usage, "input_tokens", None),
                output_tokens=getattr(response.usage, "output_tokens", None),
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                # Claude answered in plain text instead of calling submit_resolution.
                errors_left -= 1
                trace.log("missing_resolution", turn=turn, errors_left=errors_left)
                if errors_left < 0:
                    return self._escalate(trace, "Model did not submit a resolution.", turn, tool_calls)
                messages.append({
                    "role": "user",
                    "content": "Please finish by calling the submit_resolution tool.",
                })
                continue

            results = []
            for block in tool_uses:
                tool_calls.append(block.name)
                try:
                    args = self.toolbox.validate(block.name, block.input)
                except ToolInputError as exc:
                    errors_left -= 1
                    trace.log("validation_error", tool=block.name, input=block.input,
                              error=str(exc), errors_left=errors_left)
                    results.append(_tool_result(block.id, str(exc), is_error=True))
                    continue

                if isinstance(args, SubmitResolution):
                    trace.log("resolved", turn=turn, resolution=args.model_dump(mode="json"))
                    return RunResult("resolved", args, turns=turn, tool_calls=tool_calls, run_id=trace.run_id)

                try:
                    output = self.toolbox.run(block.name, args)
                    trace.log("tool_call", tool=block.name, input=args.model_dump(), output=output)
                    results.append(_tool_result(block.id, json.dumps(output)))
                except BusinessRuleError as exc:
                    # Not a model mistake: a legitimate "no". Doesn't spend retry budget.
                    trace.log("tool_rejected", tool=block.name, input=args.model_dump(), error=str(exc))
                    results.append(_tool_result(block.id, str(exc), is_error=True))

            if errors_left < 0:
                return self._escalate(trace, "Too many invalid tool calls.", turn, tool_calls)
            messages.append({"role": "user", "content": results})

        return self._escalate(trace, f"Turn limit ({self.max_turns}) reached.", self.max_turns, tool_calls)

    def _escalate(self, trace: Trace, reason: str, turns: int, tool_calls: list[str]) -> RunResult:
        trace.log("escalated", reason=reason)
        return RunResult("escalated", None, reason=reason, turns=turns, tool_calls=tool_calls, run_id=trace.run_id)


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    result = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        result["is_error"] = True
    return result
