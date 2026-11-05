"""Tool definitions, input validation and execution.

Every tool has a Pydantic model. The JSON schema sent to Claude is generated
from that model, and every tool call is validated against it *again* before it
runs: the model's output is treated as untrusted input. Business rules (refund
limits, order status) are checked in code, never left to the prompt.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

from pydantic import BaseModel, Field, ValidationError

from .store import Store

ORDER_ID = r"^ORD-\d{4}$"


# ---------- Input schemas ----------

class GetOrder(BaseModel):
    """Look up an order by id. Returns status, items, total and amount already refunded."""

    order_id: str = Field(pattern=ORDER_ID, description="Order id, e.g. ORD-1001")


class CheckInventory(BaseModel):
    """Check how many units of a SKU are in stock."""

    sku: str = Field(pattern=r"^[A-Z]+-\d{2}$", description="Product SKU, e.g. LAMP-01")


class IssueRefund(BaseModel):
    """Refund part or all of an order. Requires human approval before money moves."""

    order_id: str = Field(pattern=ORDER_ID)
    amount: float = Field(gt=0, description="Amount in USD")
    reason: str = Field(min_length=10, max_length=300)


class Category(str, Enum):
    order_status = "order_status"
    refund = "refund"
    product_question = "product_question"
    other = "other"


class SubmitResolution(BaseModel):
    """Finish the ticket. Call this exactly once, as the last step."""

    category: Category
    summary: str = Field(min_length=10, max_length=400, description="Internal summary for the support team")
    customer_reply: str = Field(min_length=20, max_length=1200, description="Reply to send to the customer")
    actions_taken: list[str] = Field(default_factory=list)


# ---------- Errors ----------

class ToolInputError(Exception):
    """Bad input from the model: sent back as is_error so Claude can correct itself."""


class BusinessRuleError(Exception):
    """Valid input that breaks a business rule (e.g. refund above order total)."""


# ---------- Execution ----------

class ToolBox:
    """Holds the tools for one run. `approver` is the human-in-the-loop gate."""

    def __init__(self, store: Store, approver: Callable[[str, dict], tuple[bool, str]]):
        self.store = store
        self.approver = approver
        self._handlers = {
            "get_order": (GetOrder, self._get_order),
            "check_inventory": (CheckInventory, self._check_inventory),
            "issue_refund": (IssueRefund, self._issue_refund),
            "submit_resolution": (SubmitResolution, None),
        }

    def definitions(self) -> list[dict]:
        defs = []
        for name, (model, _) in self._handlers.items():
            schema = model.model_json_schema()
            schema.pop("title", None)
            defs.append({
                "name": name,
                "description": (model.__doc__ or "").strip(),
                "input_schema": schema,
            })
        return defs

    def validate(self, name: str, raw_input: dict) -> BaseModel:
        if name not in self._handlers:
            raise ToolInputError(f"Unknown tool '{name}'.")
        model, _ = self._handlers[name]
        try:
            return model.model_validate(raw_input)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            raise ToolInputError(f"Invalid input for {name}: {problems}") from exc

    def run(self, name: str, args: BaseModel) -> dict:
        _, handler = self._handlers[name]
        return handler(args)

    # --- handlers ---

    def _get_order(self, args: GetOrder) -> dict:
        order = self.store.get_order(args.order_id)
        if order is None:
            raise BusinessRuleError(f"Order {args.order_id} not found. Ask the customer to confirm the id.")
        return order

    def _check_inventory(self, args: CheckInventory) -> dict:
        qty = self.store.stock_for(args.sku)
        if qty is None:
            raise BusinessRuleError(f"SKU {args.sku} does not exist.")
        return {"sku": args.sku, "in_stock": qty}

    def _issue_refund(self, args: IssueRefund) -> dict:
        order = self.store.get_order(args.order_id)
        if order is None:
            raise BusinessRuleError(f"Order {args.order_id} not found.")
        if order["status"] != "delivered":
            raise BusinessRuleError(
                f"Order {args.order_id} is '{order['status']}'. Only delivered orders can be refunded."
            )
        refundable = round(order["total"] - order["refunded"], 2)
        if args.amount > refundable:
            raise BusinessRuleError(f"Amount {args.amount:.2f} exceeds refundable balance {refundable:.2f}.")

        approved, note = self.approver("issue_refund", args.model_dump())
        if not approved:
            raise BusinessRuleError(f"Refund rejected by a human reviewer. Note: {note or 'none'}")
        result = self.store.refund(args.order_id, args.amount)
        return {**result, "approved_by_human": True, "reviewer_note": note}
