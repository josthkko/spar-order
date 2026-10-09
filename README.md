# spar-order

A [Claude Code](https://claude.com/claude-code) skill that orders groceries
from **SPAR Slovenia** (online.spar.si) for you: search, build the cart, swap
out-of-stock items, pick a delivery window, check out. It also works out what
is running low from your order history and can ping you on Telegram once a
week.

It talks to the same API the SPAR web shop uses, so there is no browser
automation and a whole order takes a couple of minutes of conversation.

```
you:    order the usual
claude: Last order was 16 days ago. Due: kefir, eggs, milk, kombucha…
        and bin bags (every ~27 days, last 60 days ago). Reorder #0 and add
        the bin bags?
        [AskUserQuestion: substitutes for 2 out-of-stock items, priced]
        [AskUserQuestion: delivery Tue 10–12 / Tue 16–18 / Wed 08–10]
        Final cart, 31 lines, 118.40 EUR, free shipping. Place it?
you:    yes
claude: Order 87ABCD1234 placed, Tue 10:00–12:00, pay the driver by card.
```

## Install

```bash
git clone https://github.com/josthkko/spar-order ~/.claude/skills/spar-order
cd ~/.claude/skills/spar-order
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # aiohttp only
cp .env.example .env                                                  # then edit
./spar login
./spar addresses                                                      # copy your address id into .env
```

If `./spar login` reports no active delivery cart, add any item to the cart on
online.spar.si once (the API cannot create a delivery cart itself) and retry.

The `spar` wrapper uses `.venv` when it exists, otherwise the system `python3`
(which needs `aiohttp` installed). Python 3.11+.

`.env` holds your SPAR login, delivery address id, brands to avoid, and the
optional Telegram settings. It is gitignored along with `.state.json` (auth
token, cart id) and `staples.md` (your personal shopping reference).

## Use

In Claude Code, just ask: *"order groceries"*, *"what's running low?"*,
*"add kefir and eggs to the SPAR cart"*. The skill drives the CLI, asks you
structured questions with prices, and **never places an order without showing
you the full cart and getting a yes**.

The CLI also works on its own:

```
./spar search "kefir"            ./spar add 195770 6
./spar cart --suggest            ./spar remove <line-id>
./spar orders                    ./spar reorder 0
./spar due                       ./spar slots
./spar checkout 2
```

### Your staples file

`staples.md` is the skill's memory of what you buy: usual quantities,
preferred brands, brands to avoid, and substitutes for things that are often
out of stock. Claude reads it before every order and updates it after one.
Start from `staples.example.md` or let Claude draft it from your order
history.

### What's running low

`./spar due` looks at your past orders, works out each product's reorder
rhythm (median gap between purchases) and lists what is due within the next
week. Regulars are summarised; occasional items with a longer rhythm
(detergent, bin bags, softener) are listed one by one because those are the
ones a plain reorder forgets. Products you have not bought for several cycles
are treated as dropped.

### Weekly Telegram ping

Create a bot with [@BotFather](https://t.me/BotFather), put `TELEGRAM_BOT_TOKEN`
and your `TELEGRAM_CHAT_ID` in `.env` (and `TELEGRAM_THREAD_ID` for a forum
topic), then:

```
0 9 * * 0  cd ~/.claude/skills/spar-order && ./spar due --telegram >> due.log 2>&1
```

The ping is read-only. When you want an order, open Claude Code and say so.

## Payment and delivery

Checkout is always **pay on delivery with the driver's card terminal**.
Prepaid card payment needs a 3-D Secure browser redirect the API cannot
complete, and it leaves broken orders when attempted. Delivery goes to the
address in `SPAR_ADDRESS_ID` (or your first saved address).

## Caveats

- Unofficial API, reverse-engineered from the web shop (see [API.md](API.md)).
  SPAR can change it any time; `client-version` and the two public client keys
  can be overridden from `.env` if they rotate.
- One active delivery cart per account. The cart persists between sessions.
- Weight-priced produce is ordered in whole kilograms; fractions are dropped.
- Delivery slots expire about 15 minutes after being fetched.
- Tested only with one account in Ljubljana. Other delivery areas should
  work (the store reference is the online store), but are unverified.

## Test

```
./.venv/bin/python3 test_spar_order.py
```

## License

MIT
