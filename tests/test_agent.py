"""Offline tests: a scripted fake client replaces the Anthropic API.

Run with: python -m unittest discover tests
"""

from __future__ import annotations

import itertools
import unittest
from types import SimpleNamespace as NS

from agent import Store, SupportAgent, ToolBox

_ids = itertools.count(1)


def tool_use(name: str, **inp):
    return NS(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=inp)


def text(t: str):
    return NS(type="text", text=t)


def reply(*blocks):
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=100, output_tokens=50))


class FakeClient:
    """Returns pre-scripted responses and records every request it gets."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot
        return self._responses.pop(0)


RESOLUTION = dict(
    category="refund",
    summary="Customer received a broken lamp; full refund approved.",
    customer_reply="Hi Jane, sorry about the lamp! We refunded the full USD 84.50.",
    actions_taken=["refund ORD-1001 84.50"],
)


def make_agent(responses, approve=True, **kw):
    decisions = []

    def approver(action, payload):
        decisions.append((action, payload))
        return approve, "ok" if approve else "Ask for a photo first"

    store = Store()
    client = FakeClient(responses)
    agent = SupportAgent(client, ToolBox(store, approver), log_dir=None, **kw)
    return agent, client, store, decisions


class AgentTests(unittest.TestCase):
    def test_happy_path_refund_with_human_approval(self):
        agent, client, store, decisions = make_agent([
            reply(tool_use("get_order", order_id="ORD-1001")),
            reply(tool_use("issue_refund", order_id="ORD-1001", amount=84.5, reason="Lamp arrived broken")),
            reply(tool_use("submit_resolution", **RESOLUTION)),
        ])
        result = agent.handle("My lamp arrived broken, order ORD-1001")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.resolution.category.value, "refund")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(store.orders["ORD-1001"]["refunded"], 84.5)

    def test_invalid_tool_input_is_returned_as_error_and_model_recovers(self):
        agent, client, _, _ = make_agent([
            reply(tool_use("get_order", order_id="1001")),  # wrong format
            reply(tool_use("get_order", order_id="ORD-1001")),
            reply(tool_use("submit_resolution", **RESOLUTION)),
        ])
        result = agent.handle("Where is order 1001?")

        self.assertEqual(result.status, "resolved")
        error_msg = client.requests[1]["messages"][-1]["content"][0]
        self.assertTrue(error_msg["is_error"])
        self.assertIn("order_id", error_msg["content"])

    def test_refund_rejected_by_human_does_not_move_money(self):
        agent, client, store, decisions = make_agent([
            reply(tool_use("issue_refund", order_id="ORD-1001", amount=84.5, reason="Lamp arrived broken")),
            reply(tool_use("submit_resolution", **RESOLUTION)),
        ], approve=False)
        agent.handle("Refund ORD-1001 please")

        self.assertEqual(store.orders["ORD-1001"]["refunded"], 0.0)
        result_msg = client.requests[1]["messages"][-1]["content"][0]
        self.assertIn("rejected by a human", result_msg["content"])

    def test_business_rules_block_refund_before_asking_human(self):
        agent, client, store, decisions = make_agent([
            reply(tool_use("issue_refund", order_id="ORD-1001", amount=500, reason="Customer is angry")),
            reply(tool_use("issue_refund", order_id="ORD-1003", amount=10, reason="Not delivered yet")),
            reply(tool_use("submit_resolution", **RESOLUTION)),
        ])
        agent.handle("I want my money back")

        self.assertEqual(decisions, [])  # human never bothered with invalid refunds
        self.assertIn("exceeds refundable", client.requests[1]["messages"][-1]["content"][0]["content"])
        self.assertIn("Only delivered", client.requests[2]["messages"][-1]["content"][0]["content"])

    def test_escalates_after_retry_budget_is_spent(self):
        bad = [reply(tool_use("get_order", order_id="nope")) for _ in range(4)]
        agent, _, _, _ = make_agent(bad, max_retries=3)
        result = agent.handle("???")

        self.assertEqual(result.status, "escalated")
        self.assertIn("invalid tool calls", result.reason)

    def test_invalid_final_payload_is_rejected(self):
        agent, client, _, _ = make_agent([
            reply(tool_use("submit_resolution", category="refund", summary="short", customer_reply="ok")),
            reply(tool_use("submit_resolution", **RESOLUTION)),
        ])
        result = agent.handle("Broken lamp ORD-1001")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.turns, 2)

    def test_plain_text_answer_is_nudged_then_escalated(self):
        agent, client, _, _ = make_agent([reply(text("Sure, done!")) for _ in range(3)], max_retries=1)
        result = agent.handle("Hi")

        self.assertEqual(result.status, "escalated")

    def test_turn_limit(self):
        loop = [reply(tool_use("check_inventory", sku="LAMP-01")) for _ in range(3)]
        agent, _, _, _ = make_agent(loop, max_turns=3)
        result = agent.handle("Do you have lamps?")

        self.assertEqual(result.status, "escalated")
        self.assertIn("Turn limit", result.reason)

    def test_tool_schemas_come_from_pydantic(self):
        defs = ToolBox(Store(), lambda *_: (True, "")).definitions()
        names = {d["name"] for d in defs}
        self.assertEqual(names, {"get_order", "check_inventory", "issue_refund", "submit_resolution"})
        refund = next(d for d in defs if d["name"] == "issue_refund")
        self.assertIn("amount", refund["input_schema"]["required"])


if __name__ == "__main__":
    unittest.main()
