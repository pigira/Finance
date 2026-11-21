# Pécule

A single-user portfolio tracker: what you own, what it cost, what it is worth today, and what you have already realized by selling, all in EUR.

## Language

**Asset**:
A security as listed on one exchange, identified by its ISIN and exchange together. The same ISIN on two exchanges is two Assets.
_Avoid_: Security, instrument, ticker

**Envelope**:
A named tax wrapper of one Envelope type, opened at one broker, that holds Lots and Sales. Called "Compte" in the interface.
_Avoid_: Account, wrapper, broker

**Envelope type**:
The legal kind of an Envelope (PEA, PEA-PME, CTO, Assurance-vie, PER, …). It suggests default cost properties; the Envelope's own values are the ones that count.
_Avoid_: Account type, regime

**Lot**:
One purchase of an Asset inside one Envelope, with an original quantity and a remaining quantity. A Lot is always a purchase; a sale is never a Lot.
_Avoid_: Transaction, buy order, line

**Sale**:
A disposal of part or all of an Asset's quantity held in one Envelope. It consumes remaining quantity from that Asset's Lots in the same Envelope, oldest purchase first (FIFO); it never touches Lots in another Envelope.
_Avoid_: Sell transaction, exit

**Position**:
The open holding of one Asset in one Envelope: the sum of the remaining quantities of its Lots there. FIFO, Sale limits and Exit cost all work at this level.
_Avoid_: Holding, line, row

**Consolidated position**:
One Asset summed over every Envelope that holds it: the sum of its open Positions.
_Avoid_: Total position, aggregate, holding

**Closed position**:
A Position whose Lots are all fully sold. It leaves the dashboard but keeps its Lots and Sales, so its Realized P/L stays visible. An Asset leaves the dashboard only when all its Positions are closed.
_Avoid_: Archived asset, deleted position

**Realized P/L**:
The gain or loss locked in by Sales, counting fees on both the purchase and the sale.
_Avoid_: Realized gain, profit taken

## Estimated costs

Estimates shown beside the P/L, never subtracted from it, always at today's values.

**Ongoing charges**:
The yearly fee an Asset itself charges (an ETF's or fund's TER), as a percentage of value. It belongs to the Asset, whatever Envelope holds it.
_Avoid_: TER (in prose), management fee

**Annual cost**:
What holding a Position costs per year at today's value: the Envelope's recurring fees (its fixed yearly fee shared by market value) plus the Asset's Ongoing charges.
_Avoid_: Running cost, yearly fees

**Latent exit tax**:
The tax that would be due if the Envelope were emptied today, from its rates and its age. It ignores personal allowances, so it is an upper bound.
_Avoid_: Deferred tax, tax liability

**Exit cost**:
What selling a Position today would cost: the estimated order fee plus its share of the Envelope's Latent exit tax, in proportion to its gain among the Positions with a gain.
_Avoid_: Exit fees, liquidation cost
