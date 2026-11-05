# claude-support-agent

A small, production-minded support agent built on the **Claude API with tool use**.
It resolves customer tickets for a fictional store (all data is invented) and shows the
patterns that matter when an LLM is allowed to take real actions:

| Concern | How it's handled |
|---|---|
| **Tool selection** | Claude picks from 4 tools (`get_order`, `check_inventory`, `issue_refund`, `submit_resolution`) in a manual agentic loop. |
| **Output validation** | Every tool call is validated with Pydantic before it runs. The JSON schemas sent to Claude are generated from the same models, so there's a single source of truth. |
| **Self-correction** | Invalid input goes back to Claude as a `tool_result` with `is_error: true` and a precise message, so the model fixes the call itself. |
| **Retry budget** | Each invalid call spends one retry. When the budget runs out, the ticket is **escalated to a human** instead of guessing. Transient API errors (429/5xx) are retried by the SDK with backoff. |
| **Business rules in code** | Refunds only for delivered orders, never above the refundable balance. These are enforced in Python, not in the prompt. |
| **Human-in-the-loop** | `issue_refund` pauses and asks a human reviewer. A rejection is fed back to Claude so it can explain and offer an alternative. Invalid refunds are blocked *before* a human is bothered. |
| **Structured final output** | The run ends only when `submit_resolution` passes validation (category, internal summary, customer reply, actions). |
| **Guardrails** | Turn limit and retry limit, both configurable. |
| **Observability** | One JSONL trace per run: LLM calls (stop reason, latency, tokens), tool calls, validation errors, approvals, outcome. |

## Flow

```
ticket ──▶ Claude ──▶ tool_use ──▶ validate (Pydantic)
                ▲                    │ invalid → is_error result (−1 retry)
                │                    ▼
                │              business rules ── fail → is_error result
                │                    │
                │              needs approval? ── human reviewer (y/n + note)
                │                    │
                └──── tool_result ◀──┘

submit_resolution (valid) → resolved
retries or turns exhausted → escalated to a human
```

## Run it

```bash
pip install -r requirements.txt

# Offline tests (no API key needed: a scripted fake client replaces the API)
python -m unittest discover tests -v

# Real run (needs ANTHROPIC_API_KEY)
python -m agent "Hi, my lamp from order ORD-1001 arrived broken. Can I get a refund?"
```

The model defaults to `claude-opus-4-8`; override it with `AGENT_MODEL`.

Sample orders: `ORD-1001` (delivered), `ORD-1002` (shipped), `ORD-1003` (processing).

## Project layout

```
agent/
  loop.py     agent loop, retry budget, escalation
  tools.py    Pydantic schemas, business rules, approval gate
  store.py    fake order/inventory data
  trace.py    JSONL run logs
  __main__.py CLI with an interactive human approver
tests/
  test_agent.py  9 offline tests (happy path, self-correction, human rejection,
                 business rules, retry exhaustion, invalid final payload, turn limit)
```

## Design notes

- **Why not `strict: true` tool schemas?** Strict mode guarantees the JSON shape, but business
  rules (refund limits, order state) still need code. Validating server-side keeps one place for
  both and shows the self-correction loop working.
- **Why a manual loop instead of the SDK tool runner?** The approval gate, the retry budget and
  per-step logging need control over each turn.
- Swapping `store.py` for a real database or API (Supabase, Shopify, an ERP) doesn't touch the loop.

## License

MIT
