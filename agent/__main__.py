"""CLI: python -m agent "My lamp arrived broken, order ORD-1001"

Needs ANTHROPIC_API_KEY. Refunds pause and ask you (the human) to approve.
"""

from __future__ import annotations

import json
import sys

import anthropic

from . import Store, SupportAgent, ToolBox


def cli_approver(action: str, payload: dict) -> tuple[bool, str]:
    print(f"\n[APPROVAL NEEDED] {action}: {json.dumps(payload)}")
    answer = input("Approve? [y/N] ").strip().lower()
    note = input("Note for the agent (optional): ").strip()
    return answer == "y", note


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    ticket = " ".join(sys.argv[1:])
    client = anthropic.Anthropic(max_retries=3)  # SDK retries 429/5xx with backoff
    agent = SupportAgent(client, ToolBox(Store(), cli_approver))
    result = agent.handle(ticket)

    print(f"\nStatus: {result.status} (turns: {result.turns}, tools: {result.tool_calls})")
    if result.resolution:
        print(f"Category: {result.resolution.category.value}")
        print(f"Summary: {result.resolution.summary}")
        print(f"\nReply to customer:\n{result.resolution.customer_reply}")
    else:
        print(f"Escalated to a human: {result.reason}")
    print(f"\nTrace: logs/run_{result.run_id}.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
