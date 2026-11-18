# Pécule

A single-user portfolio tracker: what you own, what it cost, what it is worth today, and what you have already realized by selling, all in EUR.

## Language

**Asset**:
A security as listed on one exchange, identified by its ISIN and exchange together. The same ISIN on two exchanges is two Assets.
_Avoid_: Security, instrument, ticker

**Lot**:
One purchase of an Asset, with an original quantity and a remaining quantity. A Lot is always a purchase; a sale is never a Lot.
_Avoid_: Transaction, buy order, line

**Sale**:
A disposal of part or all of an Asset's quantity. It consumes remaining quantity from the Asset's Lots, oldest purchase first (FIFO).
_Avoid_: Sell transaction, exit

**Position**:
The open holding of one Asset: the sum of the remaining quantities of its Lots.
_Avoid_: Holding, line, row

**Closed position**:
An Asset whose Lots are all fully sold. It leaves the dashboard but keeps its Lots and Sales, so its Realized P/L stays visible.
_Avoid_: Archived asset, deleted position

**Realized P/L**:
The gain or loss locked in by Sales, counting fees on both the purchase and the sale.
_Avoid_: Realized gain, profit taken
