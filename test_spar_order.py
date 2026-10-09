"""Smallest check that fails if the reorder logic breaks. Run: python3 test_spar_order.py"""
from datetime import datetime, timedelta, timezone

from spar_order import _is_avoided, _suggest_keyword, compute_due, due_report, order_cadence

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def order(days_ago, products, status="FINISHED"):
    frm = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT10:00:00.000Z")
    return {"status": status, "deliverySlot": {"from": frm},
            "products": [{"reference": sku, "name": name, "type": typ, "quantity": 1}
                         for sku, name, typ in products]}


KEFIR = ("1", "KEFIR, VINDIJA, 200G", "DEFAULT")
BAG = ("9", "PAPIRNATA VREČKA", "BAG")
SAMPLE = ("8", "Vzorec", "SAMPLE")
DETERGENT = ("2", "DETERGENT, DUEL", "DEFAULT")
OLD = ("3", "TORTILJA, MEXICANA", "DEFAULT")
RARE = ("4", "LOSOS, SPAR", "DEFAULT")

orders = [
    order(14, [KEFIR, BAG, SAMPLE]),                 # 14d cadence, last 14d ago -> due
    order(28, [KEFIR, DETERGENT, BAG]),
    order(42, [KEFIR, RARE]),
    order(56, [KEFIR, DETERGENT]),                   # detergent every 28d, last 28d ago -> due (occasional)
    order(84, [KEFIR, DETERGENT, RARE]),             # rare: only 2 orders -> ignored
    order(200, [OLD]), order(214, [OLD]), order(228, [OLD]),  # dropped: 14d cadence, 200d silence
    order(1, [("5", "CANCELLED THING", "DEFAULT")], status="CANCELLED"),
]

due = compute_due(orders, NOW)
skus = {d["sku"] for d in due}
assert skus == {"1", "2"}, skus
assert next(d for d in due if d["sku"] == "1")["cadence"] == 14
assert order_cadence(orders) == 14
report = due_report(orders, NOW)
assert "Regulars due (1): Kefir" in report and "Occasional items due (1):" in report and "Detergent" in report, report
assert due_report([], NOW).startswith("No delivered orders")

assert _suggest_keyword("KOZJI SIR ZGO, V REZINAH, 100G") == "kozji sir"
assert _suggest_keyword("BOROVNICE, 125G") == "borovnice"
import os
os.environ["SPAR_AVOID_BRANDS"] = "S-Budget"
assert _is_avoided({"brand": "S-BUDGET", "name": "X"}) and _is_avoided({"brand": "", "name": "JOGURT S-BUDGET"})
assert not _is_avoided({"brand": "Spar", "name": "JOGURT SPAR"})
print("ok")
