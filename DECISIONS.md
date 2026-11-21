# Decisions

Choices made where the specification was silent or ambiguous. Each one is the
simplest option that seemed reasonable; the "why" is given so it can be revisited.

## Product

| # | Decision | Why |
|---|---|---|
| 1 | The app is called **Pécule** (French for "nest egg"), with a hand-drawn ring logo. | Original name and mark, no existing brand imitated. |
| 2 | P/L is shown as "Plus-value latente (hors dividendes)" in the KPI card and above the table. | Required label; the column header stays short. |
| 3 | ~~A free-text **broker** field exists on a purchase and is shown under the date.~~ Superseded: every purchase belongs to an **Envelope** ("Compte"), whose name is shown under the date; the broker is a property of the Envelope (GitHub issue #2). | FIFO, Sale limits and estimated costs all depend on the Envelope. |
| 4 | In edit mode, ISIN, exchange and currency are read-only. Name, type, ticker and the manual-price flag are editable and apply to the asset, hence to all its lots. | Changing the identity of an asset under existing lots would silently re-value them. |
| 5 | Adding a lot to an existing (ISIN, exchange) reuses the asset and updates its name, type, ticker and manual flag from the form. A different currency is refused with a message. | "Last write wins" is predictable; a currency mismatch would corrupt valuations. |
| 6 | An asset without listing (manual fund) has `exchange = ''`, not NULL. | SQLite treats NULLs as distinct, which would defeat `unique(isin, exchange)`. |
| 7 | Dates are typed as `jj/mm/aaaa` in a text field with an automatic mask; a button opens the browser's calendar. | `<input type="date">` follows the browser language and would show mm/dd/yyyy in an English browser. |
| 8 | Numbers are typed with a decimal comma or point ("1 234,56" and "1234.56" both work). | French UI, but brokers' statements use both. |
| 9 | The CSV export is comma-separated with ISO dates and decimal points. | It is a machine-readable backup, not a report. |
| 10 | `python -m app.seed_demo` refuses to run on a database that already has purchases. | It must never pollute real data. |

## Prices and currencies

| # | Decision | Why |
|---|---|---|
| 11 | A pence-quoted listing is stored as currency **GBP** with `price_scale = 0.01`. The form shows it as "GBX". Unit price and fees are typed in **pounds**. | The specification applies `price_scale` to quotes only, so cost stays in the major unit. |
| 12 | `price_history.price` holds the raw quote (8250 for pence); scaling happens in `calc.py`. | Keeps the cache faithful to the provider. |
| 13 | When a quote arrives in the same currency but another unit than recorded, the asset's `price_scale` is corrected. When it arrives in another currency, the price is rejected and the asset flagged. | A listing typed by hand as "GBP" although quoted in pence would be wrong by a factor 100; a wrong currency cannot be repaired automatically. |
| 14 | A manual-price asset always has `price_scale = 1` and is never flagged stale by age. | The owner types its price in the major unit and decides when to update it. |
| 15 | An asset with no price at all is valued at its latest purchase price, with the "périmé" badge explaining so. | Totals stay meaningful; the alternative was to exclude it and understate the portfolio. |
| 16 | `fx_rates` has one extra column, `fetched_at`. | Needed to apply the 15-minute TTL to rates, as required for prices. |
| 17 | A rate looked up for a past purchase date is cached under that date. It is treated as "current" only if it was fetched on the day it is dated. | Otherwise an old rate fetched a minute ago for a form prefill would pass for today's rate. |
| 18 | The weekend rule is implemented twice: Frankfurter answers a Saturday with Friday's rate by itself (verified live), and the service also walks back day by day, up to 7 days, when a provider returns nothing. | The rule must hold for any provider, including the Yahoo fallback. |
| 19 | `fx_source` is `ecb`, `yahoo` or `mock` when the prefilled rate is kept, and `manual` as soon as the user edits it. | The specification names `ecb` and `manual`; the other two say which provider answered. |
| 20 | On-demand refreshes respect the TTL. The background task and the CLI ignore it. A ticker that just failed is not retried by the automatic page-load refresh, only by the button. | Avoids one timeout per page view for a dead ticker, while keeping a manual retry. |
| 21 | The reason of the last failed refresh is kept in memory, not in the database. | It only feeds a tooltip; no extra table for it. |
| 22 | "Today" (which cache row is today's, whether a purchase date is in the future) is taken in `DISPLAY_TIMEZONE`. Datetimes are stored as naive UTC. | SQLite has no time-zone type; one convention avoids off-by-one days around midnight. |
| 23 | `PRICE_PROVIDER=mock` also switches FX to the mock unless `FX_PROVIDER` says otherwise. | "Mock" should mean fully offline. |

## Yahoo and Frankfurter (checked against the live services)

| # | Decision | Why |
|---|---|---|
| 24 | "Rechercher la cotation" searches by ISIN with `yfinance.Search`, then searches the long name of the result to find the same security on other exchanges. Name matches are flagged "ISIN à vérifier". | Yahoo returns a single listing per ISIN (the London USD line for an ETF held in Amsterdam). The name search finds the others but can also return an ADR. |
| 25 | If `yfinance.Search` raises or returns nothing, Yahoo's search endpoint is called with httpx. | Asked for as the fallback. |
| 26 | A quote is one `Ticker.history(period="5d")` call; price and currency are read from its metadata, key by key. | One request per ticker with our own timeout. Copying the metadata object whole would trigger a second, slow request. |
| 27 | yfinance is configured to raise errors instead of printing them. | So that the real cause reaches the tooltip. `raise_errors=` is deprecated in yfinance 1.8. |
| 28 | Quotes are fetched in parallel, 8 at a time. | With a 5 s timeout each, a sequential refresh of a slow provider would take minutes. |
| 29 | Frankfurter is called as `GET {base}/{date}?base=EUR&symbols=USD`. HTTP 404 means "no rate", anything else unexpected is an error that triggers the Yahoo fallback. | Matches the v1 API. |

## Calculations

| # | Decision | Why |
|---|---|---|
| 30 | In "€" mode, the FX split shows EUR amounts that add up exactly: local gain converted at the purchase rate, plus the FX effect on the current value. In "%" mode it shows the factors of the specification. | The specification defines the split as percentages only; the KPI needs an amount that can be summed. |
| 31 | The portfolio "Effet de change total" in % is the FX amount divided by the value the portfolio would have at purchase rates. | Local performance cannot be summed across currencies, so the asset formula does not extend to the portfolio. |
| 32 | A division by zero returns 0 instead of failing. | The form forbids zero quantities and prices; the calc layer must still never crash the page. |
| 33 | A `sell` transaction raises `NotImplementedError` in `calc.compute_lot`, and therefore breaks the page if one is inserted by hand. | Wrong numbers shown silently would be worse. |

## Interface

| # | Decision | Why |
|---|---|---|
| 34 | Both the € and the % value are rendered; `<html data-pl-mode>` and CSS show one. Same for the FX split. The choice is saved in localStorage and applied before first paint. | No request and no flash when toggling. |
| 35 | Expanded rows are remembered in memory only: they survive a refresh of the table, not a page reload. | "Collapsed by default". |
| 36 | Column sorting is done in the browser and survives a refresh of the table. | Optional feature, kept small. |
| 37 | The donut shows at most 6 named slices and folds the rest into a grey "Autres". Slice colours follow a fixed order. | With this palette, 6 is the largest count whose neighbouring colours stay distinguishable for colour-blind readers (checked with a contrast and colour-vision validator), including the first and last slice, which touch. |
| 38 | The table next to the donut is the legend and the accessible equivalent of the chart. Two holdings with the same name get their ticker appended. | Colour alone must not carry identity. |
| 39 | Gains and losses always carry a sign and an arrow. A value that rounds to 0,00 is neutral. | Never colour-only; no red "−0,00 €". |
| 40 | Loading skeletons are used where content is fetched on demand (listing search, FX rate). During a refresh the dashboard keeps its figures, dimmed. | Replacing existing figures by placeholders would make the page jump. |
| 41 | Below 760 px the table becomes one card per asset; between 761 and 1100 px it scrolls sideways inside its card. | Both options were allowed. |

## Technical

| # | Decision | Why |
|---|---|---|
| 42 | Endpoints, SQLAlchemy and providers are synchronous. FastAPI runs them in its thread pool; the background task runs in a worker thread. | yfinance is synchronous, and sync code is easier to read and debug. |
| 43 | Extra packages beyond the listed stack: uvicorn, jinja2, python-multipart (forms), pydantic-settings (.env), tzdata (time zones on Windows). | Each is the standard companion of the listed stack. |
| 44 | `create_app(settings)` builds the app; `app = create_app()` at module level serves `uvicorn app.main:app`. Engine, providers and templates live on `app.state`. | Tests build an isolated app on a temporary database without globals. |
| 45 | Two guards replace authentication: requests with an unknown `Host` header are refused, and so are writes whose `Origin` is another site. | A no-login app on localhost is otherwise exposed to DNS rebinding and cross-site form posts from any web page the owner visits. |
| 46 | CDN scripts are pinned to exact versions with Subresource Integrity hashes. | A changed file on the CDN is refused by the browser. |
| 47 | The property test uses `random` with a fixed seed, not Hypothesis. | No extra dependency; failures are reproducible. |
| 48 | `python -m app.smoke_live` was added. | A one-command check of the unofficial Yahoo integration. |
| 49 | Migrations run inside `init_db`, at start-up, in one SQLite transaction opened explicitly (DDL included). Version 2 rebuilds `transactions` to make `envelope_id` NOT NULL, turns each distinct broker into a CTO Envelope and puts lots without broker in "Compte par défaut". It is refused, and nothing changes, if the number of copied purchases differs from the original. | pysqlite does not open a transaction before DDL by itself; without it, a failure halfway would leave a half-migrated file. |
| 50 | A database always holds at least one Envelope: "Compte par défaut" (CTO) is created when none exists, and the last Envelope cannot be deleted. | The first purchase must be possible without visiting Paramètres. |
| 51 | An Envelope's opening date is optional. When set, a purchase dated before it is refused, and so is an opening date later than one of its purchases. | Migrated and default envelopes have no known opening date; the oldest purchase stands in for it. |
| 52 | Envelope names are unique. | The CSV export and the purchase form identify an Envelope by its name. |
| 53 | "Par actif / Par compte" works like "€ / %" (#34): both tables are rendered, CSS shows the chosen one, the choice is saved in localStorage. In "Par compte", sorting a column sorts the Positions inside each Envelope; Envelopes stay ordered by value. KPIs and donuts are the same in both views. | No request when switching; a sort that mixed Envelopes would break the subtotals apart. |
| 54 | Default exit tax per Envelope type, as of 21/11/2026: CTO 31.4 % (single rate); PEA and PEA-PME 31.4 % before 5 years, 18.6 % after; assurance-vie 30.0 % before 8 years, 24.7 % after; PER none. That is the 12.8 % PFU plus 18.6 % social charges, which stay at 17.2 % on assurance-vie; 7.5 % income tax on assurance-vie after 8 years. Sources (service-public.gouv.fr): F2329 (social charges 2026, checked 30/06/2026), F22449 and F2385 (PEA, PEA-PME), F22414 and F15274 (assurance-vie). The PEA rate after 5 years is the general 18.6 % of F2329: no page names it explicitly. Fees have no default. | Rates change (they did in 2026) and contracts differ: defaults are only suggestions, labelled "valeurs indicatives", and every value stays editable. Allowances (4 600 / 9 200 €), the 150 000 € premium threshold and the progressive-scale option are ignored, so the tax is an upper bound. |
| 55 | A fee left empty is no fee; an exit tax left empty is unknown, not zero ("—"), and so is any total it is part of. The exit tax is empty, a single rate, or a rate before + a threshold in whole years (1 to 30) + a rate after; the rate after applies from the anniversary of the effective opening date (28 February for an Envelope opened on 29 February). "Réappliquer les valeurs par défaut" sends the form as typed and shows it again with the exit tax of the type picked in it; it saves nothing, and fees stay (defaults have none). On a new purchase of a known Asset, an empty "Frais courants" keeps the stored value; emptying it when editing a purchase clears it. | A 0 € tax would read as "no tax", which no French Envelope has. |
