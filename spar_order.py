#!/usr/bin/env python3
"""SPAR Slovenia grocery ordering CLI.

Wraps SparClient (spar_api.py) with persistent state and subcommands.
Run through the ./spar wrapper (picks the right python) or directly.

    spar login
    spar search "mleko"
    spar cart [--suggest]
    spar add SKU [qty]
    spar remove PRODUCT_ID
    spar qty PRODUCT_ID QTY
    spar slots [YYYY-MM-DD [END_DATE]]
    spar addresses
    spar orders
    spar reorder ORDER_INDEX
    spar due [--telegram]
    spar payment-methods
    spar checkout SLOT_INDEX [ADDR_ID]

Config comes from .env next to this file (see .env.example).
"""

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))
from spar_api import SparClient  # noqa: E402


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs into os.environ; values already exported win."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(SKILL_DIR / ".env")

STATE_FILE = SKILL_DIR / ".state.json"
SPAR_EMAIL = os.environ.get("SPAR_EMAIL", "")
SPAR_PASSWORD = os.environ.get("SPAR_PASSWORD", "")
SPAR_ADDRESS_ID = os.environ.get("SPAR_ADDRESS_ID", "")  # empty = first saved address
LOCAL_TZ = ZoneInfo(os.environ.get("SPAR_TZ", "Europe/Ljubljana"))
TOKEN_MAX_AGE = 50 * 60  # SPAR's JWT lives ~1 h
PACKAGING_TYPES = ("PACKAGE", "BAG", "SAMPLE")  # delivery bags and free samples: never reorder


def _to_local(dt: datetime) -> datetime:
    return dt.astimezone(LOCAL_TZ)


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _suggest_keyword(name: str) -> str:
    """Broad search keyword from a product name, for substitute lookup.

    Text before the first comma, then the first two words: "TRIBARVNA PAPRIKA,
    500G" -> "tribarvna paprika", "BOROVNICE, 125G" -> "borovnice". Best-effort;
    refine with a manual search if the hints miss.
    """
    head = name.split(",")[0].strip()
    return " ".join(head.split()[:2]).lower()


def _is_avoided(product: dict) -> bool:
    """True if the product's brand is on the SPAR_AVOID_BRANDS list."""
    avoid = [b.strip().lower() for b in os.environ.get("SPAR_AVOID_BRANDS", "").split(",") if b.strip()]
    brand = (product.get("brand") or "").strip().lower()
    name = (product.get("name") or "").lower()
    return any(b == brand or b in name for b in avoid)


def _short(name: str) -> str:
    """'KEFIR Z BREGOV, VINDIJA, 200G' -> 'Kefir Z Bregov'."""
    return name.split(",")[0].strip().title()


def _delivery_dates(orders: list[dict]) -> list:
    """Sorted unique delivery dates of non-cancelled orders."""
    days = {_parse_iso(o["deliverySlot"]["from"]).date() for o in orders
            if o.get("status") != "CANCELLED" and (o.get("deliverySlot") or {}).get("from")}
    return sorted(days)


def order_cadence(orders: list[dict]) -> float | None:
    """Median days between orders, or None with fewer than three orders."""
    dates = _delivery_dates(orders)
    if len(dates) < 3:
        return None
    return median((b - a).days for a, b in zip(dates, dates[1:]))


def compute_due(orders: list[dict], today: datetime, horizon_days: int = 7, min_orders: int = 3) -> list[dict]:
    """Items whose usual reorder interval runs out within `horizon_days`.

    For every product seen in >= min_orders non-cancelled orders, the cadence is
    the median gap between its order dates. It is due when
    days_since_last + horizon >= cadence. Items not ordered for a long time
    relative to their cadence are treated as dropped from the repertoire and
    left out. Returns dicts sorted most-overdue first:
    {sku, name, cadence, since, times, qty}.
    """
    seen: dict[str, dict] = {}
    for o in orders:
        if o.get("status") == "CANCELLED" or not (o.get("deliverySlot") or {}).get("from"):
            continue
        day = _parse_iso(o["deliverySlot"]["from"]).date()
        for p in o.get("products", []):
            if p.get("type") in PACKAGING_TYPES:
                continue
            e = seen.setdefault(p["reference"], {"name": p["name"], "dates": set(), "qty": p.get("quantity", 1)})
            e["dates"].add(day)
    due = []
    for sku, e in seen.items():
        dates = sorted(e["dates"])
        if len(dates) < min_orders:
            continue
        cadence = median((b - a).days for a, b in zip(dates, dates[1:]))
        since = (today.date() - dates[-1]).days
        # ponytail: fixed "dropped" rule; make it a setting if someone's pantry
        # rhythm needs it. 3 missed cycles plus a month of slack.
        if since > 3 * cadence + 30:
            continue
        if since + horizon_days >= cadence:
            due.append({"sku": sku, "name": e["name"], "cadence": cadence, "since": since,
                        "times": len(dates), "qty": e["qty"]})
    due.sort(key=lambda d: d["since"] - d["cadence"], reverse=True)
    return due


def due_report(orders: list[dict], today: datetime, horizon_days: int = 7) -> str:
    """Short human/Telegram-friendly summary of what is running low."""
    dates = _delivery_dates(orders)
    if not dates:
        return "No delivered orders yet — nothing to base a reorder on."
    oc = order_cadence(orders)
    lines = [f"Last SPAR order: {dates[-1]}, {(today.date() - dates[-1]).days} days ago"
             + (f" (you order every ~{oc:.0f} days)" if oc else "")]
    due = compute_due(orders, today, horizon_days)
    if not due:
        lines.append("Nothing due yet.")
        return "\n".join(lines)
    # Regulars ride along with any order; occasional items (cadence well above
    # the order rhythm: detergent, bin bags, ...) are the ones easy to forget.
    split = 1.5 * oc if oc else float("inf")
    regular = [d for d in due if d["cadence"] <= split]
    occasional = [d for d in due if d["cadence"] > split]
    if regular:
        names = [_short(d["name"]) for d in regular]
        more = f" (+{len(names) - 8} more)" if len(names) > 8 else ""
        lines.append(f"Regulars due ({len(regular)}): " + ", ".join(names[:8]) + more)
    if occasional:
        lines.append(f"Occasional items due ({len(occasional)}):")
        for d in occasional:
            lines.append(f"  {_short(d['name'])} — every ~{d['cadence']:.0f}d, last {d['since']}d ago")
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to send pings.")
    fields = {"chat_id": chat_id, "text": text}
    if os.environ.get("TELEGRAM_THREAD_ID"):
        fields["message_thread_id"] = os.environ["TELEGRAM_THREAD_ID"]
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=urllib.parse.urlencode(fields).encode())
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise SystemExit(f"Telegram send failed: HTTP {resp.status}")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))
    STATE_FILE.chmod(0o600)


def token_is_fresh(state: dict) -> bool:
    return (time.time() - state.get("login_time", 0)) < TOKEN_MAX_AGE


async def get_client(force_login: bool = False) -> SparClient:
    """Authenticated SparClient, re-using the saved token while it is fresh."""
    client = SparClient()
    state = load_state()
    if not force_login and state.get("token") and token_is_fresh(state):
        client.token = state["token"]
        client.cart_id = state.get("cart_id")
        return client
    if not SPAR_EMAIL or not SPAR_PASSWORD:
        raise SystemExit("Set SPAR_EMAIL and SPAR_PASSWORD in .env (copy .env.example).")
    await client.login(SPAR_EMAIL, SPAR_PASSWORD)
    state["token"] = client.token
    state["login_time"] = time.time()
    client.cart_id = state.get("cart_id")
    save_state(state)
    return client


async def ensure_cart(client: SparClient):
    """Make sure client.cart_id points at the active delivery cart and persist it."""
    if not client.cart_id:
        await client.get_active_cart()
    if client.cart_id:
        state = load_state()
        state["cart_id"] = client.cart_id
        save_state(state)


async def default_address_id(client: SparClient) -> str:
    if SPAR_ADDRESS_ID:
        return SPAR_ADDRESS_ID
    addresses = await client.get_addresses()
    if not addresses:
        raise SystemExit("No saved delivery address. Add one on online.spar.si first.")
    return str(addresses[0]["id"])


def print_cart_lines(cart: dict):
    products = cart.get("products", [])
    summary = cart.get("pricesSummary", {})
    print(f"\nCart ({len(products)} items):")
    for p in products:
        print(f"  {p['unitQuantity']}x {p['name']} — {p['price']['total']:.2f} EUR  [id: {p['id']}]")
    print(f"\nTotal: {summary.get('total', 0):.2f} EUR")


async def cmd_login():
    client = await get_client(force_login=True)
    try:
        await ensure_cart(client)
        if client.cart_id:
            cart = await client.get_cart()
            total = cart.get("pricesSummary", {}).get("total", 0)
            print(f"Logged in. Cart: {client.cart_id} ({len(cart.get('products', []))} items, {total:.2f} EUR)")
        else:
            print("Logged in. No active cart.")
    finally:
        await client.close()


async def cmd_search(query: str):
    client = await get_client()
    try:
        result = await client.search_full(query)
        products = result["products"]
        print(f"Search: \"{query}\" — {result['pagination']['total']['value']} results (showing {len(products)})\n")
        for p in products:
            avail = "In stock" if p["isAvailable"] else "OUT OF STOCK"
            promo = p.get("promotion") or {}
            cond = (promo.get("conditions") or [{}])[0]
            promo_str = ""
            if promo.get("isActive") and cond.get("price") and cond["price"] < p["price"]:
                promo_str = f" → {cond['price']:.2f} promo ({promo.get('description', '')})"
            per_sub = f" ({p['pricePerSubUnit']:.2f}/{p['subUnit']})" if p.get("pricePerSubUnit") else ""
            brand = f" | {p['brand']}" if p.get("brand") else ""
            cats = " > ".join(c["name"] for c in p.get("categories", []) if c)
            print(f"  {p['name']}")
            print(f"    {p['price']:.2f} EUR{promo_str}{per_sub}{brand}")
            print(f"    SKU: {p['sku']} | {avail} | {cats}")
            print()
    finally:
        await client.close()


async def cmd_cart(suggest: bool = False):
    client = await get_client()
    try:
        await ensure_cart(client)
        if not client.cart_id:
            print("No active cart. Add something first.")
            return
        cart = await client.get_cart()
        products = cart.get("products", [])
        summary = cart.get("pricesSummary", {})

        avail_map = {}
        try:
            validated = await client.validate_cart_with_groups()
            for vp in validated.get("products", []):
                avail_map[vp.get("name", "")] = vp.get("availability", True)
        except Exception:
            pass

        print(f"Cart: {cart['id']} ({cart.get('operationalModel', '?')}, {cart.get('status', '?')})")
        if cart.get("address"):
            print(f"Address: {cart['address'].get('description', '')} — {cart['address'].get('city', '')}")
        if cart.get("deliverySlot"):
            print(f"Delivery: {cart['deliverySlot']['start']} - {cart['deliverySlot']['end']}")
        print(f"\n{len(products)} items:")
        out_of_stock = []
        for p in products:
            available = avail_map.get(p["name"], True)
            tag = "" if available else " [OUT OF STOCK]"
            print(f"  {p['unitQuantity']}x {p['name']} — {p['price']['total']:.2f} EUR  [id: {p['id']}]{tag}")
            if not available:
                out_of_stock.append(p)
        print(f"\nSubtotal: {summary.get('subtotal', 0):.2f} EUR")
        print(f"Shipping: {summary.get('shippingFee', 0):.2f} EUR")
        print(f"Total: {summary.get('total', 0):.2f} EUR")
        ps = summary.get("productsSummary", {})
        if ps.get("missingForMinimumOrderValue", 0) > 0:
            print(f"Missing for minimum order: {ps['missingForMinimumOrderValue']:.2f} EUR")
        if ps.get("missingForFreeShipping", 0) > 0:
            print(f"Missing for free shipping: {ps['missingForFreeShipping']:.2f} EUR")
        if out_of_stock:
            print(f"\nWARNING: {len(out_of_stock)} item(s) OUT OF STOCK:")
            for oos in out_of_stock:
                print(f"  - {oos['name']}  [id: {oos['id']}]")
                if suggest:
                    keyword = _suggest_keyword(oos["name"])
                    try:
                        res = await client.search_full(keyword)
                        cands = [c for c in res.get("products", [])
                                 if c.get("isAvailable") and not _is_avoided(c)][:3]
                    except Exception:
                        cands = []
                    if cands:
                        print(f"      substitutes (search \"{keyword}\"):")
                        for c in cands:
                            brand = f" ({c['brand']})" if c.get("brand") else ""
                            print(f"        [{c['sku']}] {c['name']} — {c['price']:.2f} EUR{brand}")
                    else:
                        print(f"      (no in-stock substitutes for \"{keyword}\" — try a manual search)")
            print("Remove these items before checkout.")
            if not suggest:
                print("Tip: run 'cart --suggest' for in-stock substitute candidates.")
    finally:
        await client.close()


async def cmd_add(sku: str, qty: int = 1):
    client = await get_client()
    try:
        await ensure_cart(client)
        result = await client.add_to_cart(sku, qty)
        client.cart_id = result["id"]
        await ensure_cart(client)
        print(f"Added {qty}x {sku} to cart.")
        print_cart_lines(result)
    finally:
        await client.close()


async def cmd_remove(product_id: str):
    client = await get_client()
    try:
        await ensure_cart(client)
        result = await client.remove_from_cart(product_id)
        print(f"Removed product {product_id} from cart.")
        print_cart_lines(result)
    finally:
        await client.close()


async def cmd_qty(product_id: str, qty: int):
    client = await get_client()
    try:
        await ensure_cart(client)
        result = await client.update_quantity(product_id, qty)
        print(f"Set product {product_id} to {qty}.")
        print_cart_lines(result)
    finally:
        await client.close()


async def cmd_slots(start_date: str | None = None, end_date: str | None = None):
    client = await get_client()
    try:
        await ensure_cart(client)
        if start_date is None:
            start_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        if end_date is None:
            end_date = (datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")

        # The slot lookup needs an address on the cart.
        await client.update_cart(address_id=await default_address_id(client))
        validated = await client.validate_cart_with_groups()
        groups = validated.get("groups", [])
        if not groups:
            print("Error: cart has no delivery groups. Add products first.")
            return
        group_id = groups[0]["id"]

        slots = await client.get_delivery_slots(start_date, end_date, group_id)
        print(f"Delivery slots {start_date} to {end_date}: {len(slots)} available\n")
        if not slots:
            print("  No slots available.")
            return

        state = load_state()
        state["last_slots"] = [{"id": s["id"], "start": s["start"], "end": s["end"],
                                "type": s.get("type", "STATIC")} for s in slots]
        state["group_id"] = group_id
        save_state(state)

        for i, s in enumerate(slots):
            prices = s.get("prices", {})
            price_str = ""
            if prices.get("final") is not None:
                price_str = f" | Shipping: {prices['final']:.2f} EUR"
                if prices.get("initial") and prices["initial"] != prices["final"]:
                    price_str += f" (was {prices['initial']:.2f})"
            try:
                st = _to_local(_parse_iso(s["start"]))
                et = _to_local(_parse_iso(s["end"]))
                print(f"  [{i}] {st.strftime('%a %b %d')} {st.strftime('%H:%M')}-{et.strftime('%H:%M')}{price_str}")
            except (ValueError, AttributeError):
                print(f"  [{i}] {s['start']} - {s['end']}{price_str}")
    finally:
        await client.close()


async def cmd_addresses():
    client = await get_client()
    try:
        addresses = await client.get_addresses()
        print(f"Saved delivery addresses ({len(addresses)}):\n")
        for a in addresses:
            desc = f" ({a['description']})" if a.get("description") else ""
            print(f"  [{a.get('id', '?')}] {a.get('address', {}).get('other', '')}{desc}")
            print(f"         {a.get('zipCode', '?')} {a.get('city', '?')}")
        if not addresses:
            print("  None. Add one on online.spar.si.")
    finally:
        await client.close()


def _real_products(order: dict) -> list[dict]:
    return [p for p in order.get("products", []) if p.get("type") not in PACKAGING_TYPES]


async def cmd_orders():
    client = await get_client()
    try:
        orders = await client.get_orders()
        print(f"Past orders ({len(orders)}):\n")
        for i, o in enumerate(orders):
            real = _real_products(o)
            slot_str = ""
            if (o.get("deliverySlot") or {}).get("from"):
                slot_str = f" | {_to_local(_parse_iso(o['deliverySlot']['from'])).strftime('%Y-%m-%d %H:%M')}"
            total = sum(p.get("price", 0) * p.get("quantity", 1) for p in real)
            print(f"  [{i}] Order {o.get('jobNumber', '?')} — {o.get('status', '?')}{slot_str} | {len(real)} items, ~{total:.2f} EUR")
            for p in real[:4]:
                print(f"      {p.get('quantity', 1)}x {p['name']} ({p.get('price', 0):.2f} EUR)")
            if len(real) > 4:
                print(f"      ... and {len(real) - 4} more items")
            print()
    finally:
        await client.close()


async def cmd_reorder(order_index: str):
    client = await get_client()
    try:
        await ensure_cart(client)
        orders = await client.get_orders()
        try:
            idx = int(order_index)
            order = orders[idx]
        except (ValueError, IndexError):
            print(f"Error: '{order_index}' is not a valid index (0-{len(orders) - 1}). Use 'orders' to list.")
            return
        real = _real_products(order)
        if not real:
            print("Error: order has no products to reorder.")
            return

        print(f"Reordering {len(real)} items from order {order['jobNumber']}...")
        items, rounded_notes = [], []
        for p in real:
            unit = p.get("baseUnit", "KOS")
            qty = p.get("quantity", 1)
            if unit == "KG":
                # Weight-priced items: the API silently drops fractional quantities.
                rounded = max(1, round(qty))
                if abs(rounded - qty) > 0.01:
                    rounded_notes.append((p["name"], qty, rounded))
                qty = rounded
            items.append({"reference": p["reference"], "unit": unit, "unitQuantity": qty})
        for name, orig, rnd in rounded_notes:
            print(f"  note: {name}: {orig}kg → {rnd}kg (whole kg only)")

        cart = await client.add_products_to_cart(items)
        client.cart_id = cart["id"]
        await ensure_cart(client)
        print_cart_lines(cart)
    finally:
        await client.close()


async def cmd_due(telegram: bool = False, horizon_days: int = 7):
    client = await get_client()
    try:
        orders = await client.get_orders()
    finally:
        await client.close()
    text = due_report(orders, datetime.now(timezone.utc), horizon_days)
    print(text)
    if telegram:
        send_telegram(text + "\n\nTo order: open Claude Code and say \"order groceries\".")
        print("\n(sent to Telegram)")


async def cmd_payment_methods():
    client = await get_client()
    try:
        await ensure_cart(client)
        methods = await client.get_cart_payment_methods()
        print("Available payment methods for this cart:\n")
        for m in methods:
            print(f"  [{m['type']}] {m['names']}")
    finally:
        await client.close()


async def cmd_checkout(slot_index: str, addr_id: str | None = None):
    client = await get_client()
    try:
        await ensure_cart(client)
        state = load_state()
        last_slots = state.get("last_slots", [])
        try:
            idx = int(slot_index)
            slot = last_slots[idx]
        except (ValueError, IndexError):
            print(f"Error: slot index {slot_index} not in 0-{len(last_slots) - 1}. Run 'slots' first.")
            return

        group_id = state.get("group_id")
        if not group_id:
            groups = (await client.validate_cart_with_groups()).get("groups", [])
            group_id = groups[0]["id"] if groups else None
        addr_id = addr_id or await default_address_id(client)

        slot_input = client.slot_to_input(slot["id"], slot_type=slot.get("type", "STATIC"))
        decoded = client.decode_slot_jwt(slot["id"])
        try:
            print(f"Delivery window: {_to_local(_parse_iso(decoded['from'])).strftime('%a %b %d %H:%M')}"
                  f" - {_to_local(_parse_iso(decoded['to'])).strftime('%H:%M')}")
            print(f"Slot expires: {_to_local(_parse_iso(decoded['expiresAt'])).strftime('%a %b %d %H:%M')}")
        except (ValueError, KeyError):
            print(f"Delivery window: {decoded.get('from')} - {decoded.get('to')}")

        print("\nValidating cart...")
        validated = await client.validate_cart_with_groups()
        unavailable = [p["name"] for p in validated.get("products", []) if not p.get("availability", True)]
        if unavailable:
            print(f"\nERROR: {len(unavailable)} item(s) out of stock:")
            for name in unavailable:
                print(f"  - {name}")
            print("\nRemove them ('cart' for ids, then 'remove ID'), refresh 'slots', retry.")
            return

        print("Setting delivery address and slot...")
        await client.update_cart(address_id=addr_id, slot=slot_input, group_id=group_id)

        # The cart must carry a paymentConfigurationId or the purchase fails /
        # produces a broken order. Pay-on-delivery with the driver's card
        # terminal is the only method that works without a browser (prepaid
        # card needs a 3-D Secure redirect).
        print("Setting payment configuration (pay on delivery, card terminal)...")
        methods = await client.get_cart_payment_methods()
        config_id = next((m.get("paymentConfigurationId") for m in methods
                          if m.get("type") == "PAY_ON_DELIVERY" and "PAYMENT_TERMINAL" in m.get("names", [])), None)
        if not config_id:
            config_id = next((m.get("paymentConfigurationId") for m in methods if m.get("type") == "PAY_ON_DELIVERY"), None)
        if not config_id:
            print("ERROR: no pay-on-delivery payment method offered for this cart. Aborting.")
            return
        await client.update_cart(payment_configuration_id=config_id)

        print("Placing order...")
        result = await client.purchase_cart()
        job_id = result.get("jobId")
        print("\nOrder placed!")
        print(f"  Job Number: {result.get('jobNumber', '?')}")
        print(f"  Status: {result.get('status', '?')}")
        print(f"  Order ID: {job_id or result.get('id', '?')}")
        if not job_id:
            print("  WARNING: no jobId in the response — check the order on online.spar.si, it may be broken.")

        state = load_state()
        for k in ("cart_id", "last_slots", "group_id"):
            state.pop(k, None)
        save_state(state)
        print("\nPayment: on delivery (card terminal or cash with the driver)")
    finally:
        await client.close()


def _usage(msg: str):
    print(f"Usage: spar {msg}")
    sys.exit(1)


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd, args = sys.argv[1], sys.argv[2:]
    flags = {a for a in args if a.startswith("--")}
    args = [a for a in args if not a.startswith("--")]

    if cmd == "login":
        await cmd_login()
    elif cmd == "search":
        args or _usage('search "query"')
        await cmd_search(args[0])
    elif cmd == "cart":
        await cmd_cart(suggest="--suggest" in flags)
    elif cmd == "add":
        args or _usage("add SKU [qty]")
        await cmd_add(args[0], int(args[1]) if len(args) > 1 else 1)
    elif cmd == "remove":
        args or _usage("remove PRODUCT_ID")
        await cmd_remove(args[0])
    elif cmd == "qty":
        len(args) == 2 or _usage("qty PRODUCT_ID QTY")
        await cmd_qty(args[0], int(args[1]))
    elif cmd == "slots":
        await cmd_slots(*args[:2])
    elif cmd == "addresses":
        await cmd_addresses()
    elif cmd == "orders":
        await cmd_orders()
    elif cmd == "reorder":
        args or _usage("reorder ORDER_INDEX")
        await cmd_reorder(args[0])
    elif cmd == "due":
        await cmd_due(telegram="--telegram" in flags, horizon_days=int(args[0]) if args else 7)
    elif cmd == "payment-methods":
        await cmd_payment_methods()
    elif cmd == "checkout":
        args or _usage("checkout SLOT_INDEX [ADDR_ID]")
        await cmd_checkout(args[0], args[1] if len(args) > 1 else None)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
