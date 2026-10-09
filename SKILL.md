---
name: spar-order
description: >
  Order groceries from SPAR Slovenia (online.spar.si) through its API: search,
  build the cart, swap out-of-stock items, pick a delivery slot, check out, and
  work out what is running low from past orders. Use this whenever the user
  wants to "order groceries", "buy food", "shop at SPAR", "do a SPAR order",
  "reorder the usual", "what's running low", "add X to the cart", "check my
  cart", "delivery slots", or mentions any Slovenian grocery item to buy — even
  if they don't say SPAR.
---

# SPAR Slovenia grocery ordering

Everything runs through one CLI: `$SKILL_DIR/spar <command>` where `$SKILL_DIR`
is the directory this file lives in. It needs a `.env` with the account
credentials (see README.md) — if a command says so, stop and tell the user.

Two files shape every order:

- **`staples.md`** (in `$SKILL_DIR`, user-specific, gitignored) — the curated
  list of what this household buys: usual quantities, brands they like, brands
  they avoid, substitutes for items that are often out of stock. Read it before
  building any cart. If it does not exist yet, build it from `orders` and
  `staples.example.md` and keep it up to date after every order.
- **Order history** (`spar orders`, `spar due`) — the ground truth of what was
  actually bought and when.

## Commands

| Command | What it does |
|---|---|
| `login` | Log in and show the active cart |
| `search "query"` | Product search with price, promo, brand, SKU, stock (Slovenian names: "mleko", "kruh") |
| `cart [--suggest]` | Cart lines with ids, totals, out-of-stock flags; `--suggest` adds in-stock substitute candidates |
| `add SKU [qty]` | Add a product (SKU from `search`) |
| `qty PRODUCT_ID QTY` | Change a line's quantity (id from `cart`) |
| `remove PRODUCT_ID` | Remove a line |
| `reorder N` | Add every product of past order N (from `orders`) to the cart |
| `orders` | Past orders, newest first |
| `due [DAYS] [--telegram]` | What is running low, from order cadence; `--telegram` sends it as a ping |
| `addresses` | Saved delivery addresses |
| `slots [START [END]]` | Delivery windows (default: tomorrow + 3 days), numbered |
| `payment-methods` | Payment methods SPAR offers for this cart |
| `checkout SLOT_INDEX [ADDR_ID]` | Place the order — pay on delivery, card terminal with the driver |

Weight-priced produce (bananas, apples, oranges) is ordered in whole kilograms;
the API silently drops fractional quantities.

## How to run an order

### 1. See where things stand
```bash
$SKILL_DIR/spar login
$SKILL_DIR/spar due
```
`due` says how long since the last order, which regulars are due and which
occasional items (detergent, bin bags, the things that are easy to forget) are
due. Lead with that when the user asks what they need, and offer the occasional
items explicitly — they are the ones a plain "reorder the usual" misses.

### 2. Build the cart
Fastest path is a reorder plus edits:
```bash
$SKILL_DIR/spar orders
$SKILL_DIR/spar reorder 0
$SKILL_DIR/spar cart --suggest
```
Or search and add item by item. Either way, read `staples.md` first so the cart
reflects what the user actually wants: usual quantities, preferred brands,
things they stopped buying.

### 3. Decide with the user — questions, not prose
Use the AskUserQuestion tool for every choice (which items, substitutes,
quantities, slot). It is much faster for the user than reading paragraphs:

- Every option shows its price: `Spar Premium 175 g (3.99)`.
- The first option is your recommended pick, labeled `(Recommended)`.
- For an out-of-stock staple, use the `substitute:` note in `staples.md`
  first, then a `cart --suggest` candidate, then a manual `search`. Skip
  brands listed under *avoid*.
- Batch related decisions into one call (max 4 questions per call).
- Detect blockers up front: run `cart --suggest` right after building the
  cart so substitutions happen in the same round as everything else, not at
  checkout.

Before fetching slots, sanity-check quantities against the usual counts in
`staples.md` and flag anything oversized with ⚠️ — accidental bulk from a
reorder multiplier or a weight-priced line is the classic mistake. Packaging
bags are server-managed; SPAR recomputes them on every cart change, so don't
fight them.

### 4. Delivery slot
```bash
$SKILL_DIR/spar slots
```
Windows are two hours, indexed from 0, and expire about 15 minutes after being
fetched. Someone has to be home: if a calendar tool is available, cross-check
and only offer windows that fit. Shipping is shown per slot; orders above the
free-shipping threshold ship free.

### 5. Final cart confirmation — never skip
Show the complete assembled cart (every line with quantity and price, subtotal,
shipping, total) and get an explicit yes before `checkout`. Individually
approved items do not count: the user confirms the whole cart. Batch this
with the slot pick so the slot doesn't expire while waiting. A "wait" or "let me
see" mid-turn means stop. If anything changes, show the cart again and
re-confirm.

### 6. Check out and report
```bash
$SKILL_DIR/spar checkout 3
```
Then tell the user the order number, delivery window, and that payment is on
delivery. A response without a `jobId` means a broken order — say so and point
them to online.spar.si.

If checkout reports out-of-stock items, remove them, run `slots` again, and
re-confirm before retrying.

### 7. Keep `staples.md` current
After a successful order (or when the user mentions an order they placed
themselves), update `staples.md`: add new items, promote repeat purchases to
staples, update prices, note stated preferences ("too expensive", "the best
feta", "no more oats"). Keep items that rotate; drop only what the user
explicitly dropped.

## Weekly "what's running low" ping

`spar due --telegram` sends the `due` summary to a Telegram chat (bot token
and chat id in `.env`). Put it on a weekly cron (README.md) so the user gets a
nudge and starts an order from Claude Code when they want one. The ping is
read-only; nothing is ever ordered without the confirmation step above.

## Optional: independent test verdicts

If a product-test skill is installed (for example one wrapping a consumer
organisation's rankings), use it for categories it covers — detergent,
softener, olive oil, cleaning products — and put a matched test winner first in
the options with a marker like `🏆 test winner`. The user still chooses.

## Gotchas

- Auth token lives ~1 h; the CLI re-logs in from `.env` automatically. A
  "Requests from referer blocked" error means the login Referer header is gone.
- One active delivery cart per account. It persists between sessions —
  `cart` first, don't assume it's empty.
- Items can go out of stock between adding and checkout; `cart --suggest`
  early, re-validate at checkout.
- Online card payment is not supported through the API (3-D Secure redirect);
  checkout is always pay-on-delivery with the driver's terminal.
- Product names come back in uppercase Slovenian with the brand after a
  comma: `KEFIR, VINDIJA, 200G`.
