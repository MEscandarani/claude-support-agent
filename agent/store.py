"""Fake in-memory data for a fictional online store ("Acme Home Supply").

Everything here is invented. In a real deployment these functions would call
your order database / ERP / inventory API.
"""

from __future__ import annotations

import copy

_ORDERS = {
    "ORD-1001": {
        "order_id": "ORD-1001",
        "customer": "Jane Doe",
        "status": "delivered",
        "total": 84.50,
        "items": [{"sku": "LAMP-01", "name": "Ceramic table lamp", "qty": 1, "price": 84.50}],
        "refunded": 0.0,
    },
    "ORD-1002": {
        "order_id": "ORD-1002",
        "customer": "John Smith",
        "status": "shipped",
        "total": 39.90,
        "items": [{"sku": "MUG-12", "name": "Stoneware mug (set of 2)", "qty": 1, "price": 39.90}],
        "refunded": 0.0,
    },
    "ORD-1003": {
        "order_id": "ORD-1003",
        "customer": "Ana García",
        "status": "processing",
        "total": 129.00,
        "items": [{"sku": "RUG-07", "name": "Handwoven rug 120x170", "qty": 1, "price": 129.00}],
        "refunded": 0.0,
    },
}

_INVENTORY = {
    "LAMP-01": 4,
    "MUG-12": 0,
    "RUG-07": 2,
}


class Store:
    """Tiny mutable store so each run (or test) gets its own copy of the data."""

    def __init__(self) -> None:
        self.orders = copy.deepcopy(_ORDERS)
        self.inventory = copy.deepcopy(_INVENTORY)

    def get_order(self, order_id: str) -> dict | None:
        return copy.deepcopy(self.orders.get(order_id))

    def stock_for(self, sku: str) -> int | None:
        return self.inventory.get(sku)

    def refund(self, order_id: str, amount: float) -> dict:
        order = self.orders[order_id]
        order["refunded"] = round(order["refunded"] + amount, 2)
        return {"order_id": order_id, "refunded_total": order["refunded"]}
