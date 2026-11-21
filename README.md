# Pécule

A single-user, local-first portfolio tracker: one page showing what you own,
what it cost and what it is worth today, in EUR.

- **Purchases** ("lots") are entered by hand: ISIN, date, quantity, unit price, fees,
  and the **Envelope** they were bought in ("compte" in the interface: PEA, CTO,
  assurance-vie…). Envelopes are managed in Paramètres.
- **Dashboard**: KPI cards, allocation donuts (by asset, type, currency) and a
  positions table with one sub-row per purchase, grouped by asset or by
  Envelope (with a subtotal per Envelope).
- **Foreign currencies**: the P/L of a USD or GBP holding can be split into
  local performance and FX effect.
- **Prices** come from Yahoo Finance, **FX rates** from the ECB (Frankfurter API).
  Both are cached in SQLite, so the page always renders, even offline.

The interface is in French. Code, comments and documentation are in English.

Stack: Python 3.11+, FastAPI, SQLAlchemy 2, SQLite, Pydantic v2, Jinja2 + HTMX +
Alpine.js + Chart.js. No Node, no build step, no login.

## Quick start

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

```bash
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The database `./data/portfolio.db` is created on
first start. On Windows, activate the environment with
`.venv\Scripts\Activate.ps1` instead of the `source` line.

In VS Code, the "Pécule: uvicorn (debug)" launch configuration does the same
with the debugger attached (`.vscode/launch.json`).

### Try it with demo data, offline

One command on macOS or Linux (needs Python 3.11 or newer; it sets up `.venv`,
the demo database and the server, and never touches `data/portfolio.db`):

```bash
./run_demo.sh
```

Then open <http://127.0.0.1:8000>. The manual steps are below.

The mock providers return fixed prices, so no network is needed. Use a separate
database file to keep the demo away from your real data:

```bash
DATABASE_URL=sqlite:///./data/demo.db PRICE_PROVIDER=mock python -m app.seed_demo
```

```bash
DATABASE_URL=sqlite:///./data/demo.db PRICE_PROVIDER=mock uvicorn app.main:app --reload
```

PowerShell sets variables differently: run `$env:DATABASE_URL="sqlite:///./data/demo.db"`
and `$env:PRICE_PROVIDER="mock"` first, then the two commands without the prefix.

The demo holds Microsoft (USD, two purchases), an MSCI World ETF bought in
Amsterdam (EUR), the same ETF bought in London where it is quoted in pence
(GBX), and an unlisted fund whose price is typed by hand. They sit in three
Envelopes: two CTOs at two brokers and a PER. The empty "Compte par défaut"
that every database starts with is listed too; delete it in Paramètres if you like.

## Configuration

Copy `.env.example` to `.env` and edit it. Every variable is optional.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/portfolio.db` | SQLite file. |
| `PRICE_PROVIDER` | `yahoo` | `yahoo` (live, through yfinance) or `mock` (offline, deterministic). |
| `FX_PROVIDER` | `auto` | `auto`, `frankfurter`, `yahoo` or `mock`. `auto` means Frankfurter with a Yahoo fallback, or mock when `PRICE_PROVIDER=mock`. |
| `FRANKFURTER_BASE_URL` | `https://api.frankfurter.dev/v1` | Base URL of the ECB rates API. |
| `HTTP_TIMEOUT_SECONDS` | `5` | Timeout of every outgoing request. |
| `PRICE_TTL_MINUTES` | `15` | On-demand refreshes skip anything fetched more recently. |
| `STALE_AFTER_HOURS` | `24` | Age after which a price gets the "périmé" badge. |
| `BACKGROUND_REFRESH_HOURS` | `6` | Period of the in-process refresh; `0` disables it. |
| `DISPLAY_TIMEZONE` | `Europe/Amsterdam` | Time zone of displayed times and of "today". |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host names the app answers to. |

There is no authentication: keep the app bound to `127.0.0.1` (uvicorn's default).

## How prices and FX rates are refreshed

- Every refresh writes **today's row** per asset in `price_history` and per
  currency in `fx_rates` (insert or update), so the tables hold one row per day.
- **Page load**: the page renders at once from the cache; if something is older
  than `PRICE_TTL_MINUTES`, the browser then asks for a refresh in the background.
- **"Actualiser" button**: same refresh, and it also retries tickers that just failed.
- **Background task**: every `BACKGROUND_REFRESH_HOURS` while the server runs.
- **Failures never break the page**: the last known price stays, with a
  "périmé" badge whose tooltip gives the reason.
- An FX rate is "units of foreign currency per 1 EUR" (EUR/USD = 1.09). For a
  weekend or holiday, the rate of the last previous business day is used.

### Refresh from cron

`python -m app.refresh` refreshes everything once, ignoring the cache. It
exits with code 1 if a price or a rate could not be fetched.

```bash
crontab -e
```

```
# Every weekday at 18:10, after the European close
10 18 * * 1-5  cd /path/to/Finance && .venv/bin/python -m app.refresh >> data/refresh.log 2>&1
```

On Windows, the equivalent is a Task Scheduler task running
`.venv\Scripts\python.exe -m app.refresh` with the project folder as its
start directory.

## Backup

Everything lives in one file, `data/portfolio.db`. SQLite's online backup is
safe while the app is running and needs nothing but Python:

```bash
python -c "import sqlite3; src = sqlite3.connect('data/portfolio.db'); dst = sqlite3.connect('data/portfolio-backup.db'); src.backup(dst); dst.close(); src.close()"
```

To restore, stop the app and copy the backup over `data/portfolio.db`.

As a second, human-readable safety net, **Paramètres > Exporter CSV** downloads
every purchase (also available at `/export/transactions.csv`). The CSV holds
what you typed; prices and rates are a cache and can always be fetched again.

## Tests and lint

```bash
pytest
```

```bash
ruff check . && ruff format --check .
```

The tests run offline on temporary databases: Yahoo and Frankfurter are replaced
by fakes, and the mock providers are deterministic.

## Check the live providers

```bash
python -m app.smoke_live
```

This asks the real Yahoo Finance and Frankfurter services a few known questions
and prints the answers (nothing is written to the database). Run it if prices
stop updating: Yahoo has no official API and changes without notice.

## Calculation rules

All maths is in `app/calc.py` (pure functions, no I/O). Per purchase, with
`r_buy` the FX rate at purchase and `r_now` the current one (both 1 for EUR):

```
cost_eur   = (qty * unit_price + fees) / r_buy
value_eur  = qty * last_price * price_scale / r_now
pl_eur     = value_eur - cost_eur            pl_pct = pl_eur / cost_eur
local_perf = value_local / cost_local - 1    fx_effect = r_buy / r_now - 1
(1 + local_perf) * (1 + fx_effect) - 1 == pl_pct
```

Per asset, quantities and EUR amounts are summed and percentages are
recomputed from the sums (never averaged). PRU = total EUR cost / total quantity.
The figures are an unrealised gain, **excluding dividends**.

## Project layout

```
app/
  main.py            application factory, background refresh task
  config.py          settings (.env)
  db.py, models.py   SQLAlchemy engine and tables
  calc.py            portfolio maths (pure)
  isin.py            ISIN check digit
  schemas.py         validation of the purchase and envelope forms
  formatting.py      French number and date formatting
  security.py        Host and Origin checks
  providers/         base.py (interfaces), yahoo.py, frankfurter.py, mock.py
  services/          pricing.py (refresh), fx.py, portfolio.py, lots.py, envelopes.py,
                     export.py
  routers/           pages.py, lots.py, settings.py (Paramètres, Comptes),
                     fragments.py (HTMX)
  templates/, static/
  refresh.py         python -m app.refresh
  seed_demo.py       python -m app.seed_demo
  smoke_live.py      python -m app.smoke_live
tests/
```

`DECISIONS.md` lists the choices made where the specification left room.

## Verification report (17 November 2026)

- `pytest`: 128 tests pass. They cover ISIN validation, the reference vectors,
  the FX decomposition identity (randomised), GBX scaling, the weekend FX
  fallback, CRUD routes, page rendering and provider failures.
- `ruff check` and `ruff format --check`: clean.
- Fresh virtual environment built from `requirements.txt`: tests pass, and the
  app starts with its default settings and creates `data/portfolio.db`.
- Demo seeded with `PRICE_PROVIDER=mock`, server started, every page and
  fragment requested: HTTP 200 with the expected content. The dashboard shows
  the reference figures (MSFT: value 6 818,18 €, P/L +1 172,98 €, +20,78 %).
- Browser check (headless Chromium, libraries loaded from the real CDN with
  their integrity hashes): 42 scripted checks covering add, edit of the manual
  price, delete, the toggles, sorting, refresh and the theme; no console error.
  Screenshots reviewed at 1440, 900 and 390 px, dark and light.
- **Live smoke test: done.** `python -m app.smoke_live`, then the app itself
  with `PRICE_PROVIDER=yahoo`, against the real services:

  | Check | Result |
  |---|---|
  | Yahoo, ISIN lookup through `yfinance.Search` | Works, but returns one listing per ISIN (IWDA.L, USD, for IE00B4L5Y983). |
  | Yahoo, other exchanges of the same security | Found by searching the long name: IWDA.AS, EUNL.DE, SWDA.MI, SWDA.L, SWDA.SW. |
  | Yahoo, search endpoint called with httpx (the fallback) | Works. An unknown ISIN gives an empty list. |
  | Yahoo, quotes | MSFT 517,99 USD; IWDA.AS 129,99 EUR; SWDA.L 10 849 GBp, stored as 108,49 GBP with `price_scale` 0.01. |
  | Frankfurter, Saturday 2024-03-16, EUR/USD | 1.0892 dated 2024-03-15: the API returns the previous business day by itself. |
  | Yahoo FX fallback (`EURUSD=X`), same date | 1.0883 dated 2024-03-15. |

## Known limitations

- Purchases only: no sales, no dividends, so the P/L is unrealised and excludes income.
- "Rechercher la cotation" finds listings on other exchanges by name. They are
  flagged "même nom, ISIN à vérifier": Yahoo can return a different line of the
  same company (an ADR, for instance), so check the ticker before saving.
- Yahoo Finance is an unofficial source: it can rate-limit or change its
  answers. Prices are then kept and flagged as stale.
- The ECB publishes rates for about 30 currencies, once per business day. Other
  currencies fall back to Yahoo.
- ISIN, exchange and currency of an existing purchase cannot be edited: delete
  the purchase and enter it again.
- The reason of a failed refresh is kept in memory, so it is lost on restart
  and a failure in the cron command is not shown in the web page.
- HTMX, Alpine.js and Chart.js load from a CDN: the first page view needs
  Internet access (the browser cache covers later ones).
- Amounts are floats, rounded for display only: fine for tracking, not for tax returns.
