"""Create, update and delete purchases ("lots") and the assets they belong to."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Asset, Envelope, Transaction
from app.schemas import LotForm
from app.services import pricing
from app.services.errors import FormError


def find_asset(session: Session, isin: str, exchange: str) -> Asset | None:
    """An asset is identified by (isin, exchange)."""
    statement = select(Asset).where(Asset.isin == isin, Asset.exchange == exchange)
    return session.scalar(statement)


def all_assets(session: Session) -> list[Asset]:
    """Every known asset, including those without any lot left (for autocomplete)."""
    return list(session.scalars(select(Asset).order_by(Asset.name)))


def create_lot(session: Session, form: LotForm, settings: Settings) -> Transaction:
    """Add a purchase. The asset is created on first use and reused afterwards."""
    envelope = _get_envelope(session, form)
    asset = find_asset(session, form.isin, form.exchange)
    if asset is None:
        asset = Asset(
            isin=form.isin,
            exchange=form.exchange,
            currency=form.currency,
            price_scale=form.price_scale,
            name=form.name,
            asset_type=form.asset_type,
            ticker=form.ticker or None,
            manual_price=form.manual_price,
        )
        session.add(asset)
    else:
        if asset.currency != form.currency:
            place = f"sur {asset.exchange}" if asset.exchange else "sans place de cotation"
            raise FormError(
                "currency",
                f"Cet ISIN existe déjà {place} en {asset.currency}. "
                "Indiquez une autre place de cotation pour créer un actif distinct.",
            )
        _apply_asset_fields(session, asset, form, settings)

    transaction = Transaction(asset=asset, type="buy")
    _apply_lot_fields(transaction, form, envelope)
    session.add(transaction)
    session.commit()
    return transaction


def update_lot(
    session: Session, transaction: Transaction, form: LotForm, settings: Settings
) -> Transaction:
    """Edit a purchase. ISIN, exchange and currency of its asset are not editable."""
    envelope = _get_envelope(session, form, current=transaction.envelope)
    _apply_asset_fields(session, transaction.asset, form, settings)
    _apply_lot_fields(transaction, form, envelope)
    session.commit()
    return transaction


def delete_lot(session: Session, transaction: Transaction) -> None:
    """Delete a purchase. The asset row is kept even when this was its last lot:
    it simply disappears from the positions and stays available for autocomplete."""
    session.delete(transaction)
    session.commit()


def _get_envelope(session: Session, form: LotForm, current: Envelope | None = None) -> Envelope:
    """The envelope chosen in the form. `current` is the lot's envelope when it is edited."""
    envelope = session.get(Envelope, form.envelope_id)
    if envelope is None:
        raise FormError("envelope_id", "Compte inconnu")
    if envelope.opening_date is not None and form.trade_date < envelope.opening_date:
        if current is not None and current.id != envelope.id:
            # The lot is being moved: the envelope is what the user changed.
            raise FormError("envelope_id", "Compte ouvert après la date de cet achat")
        raise FormError("trade_date", "Date antérieure à l'ouverture du compte")
    return envelope


def _apply_lot_fields(transaction: Transaction, form: LotForm, envelope: Envelope) -> None:
    transaction.trade_date = form.trade_date
    transaction.quantity = form.quantity
    transaction.unit_price = form.unit_price
    transaction.fees = form.fees
    transaction.fx_rate = form.fx_rate
    transaction.fx_source = form.fx_source
    transaction.envelope = envelope


def _apply_asset_fields(session: Session, asset: Asset, form: LotForm, settings: Settings) -> None:
    """Update the editable attributes of an existing asset (shared by all its lots)."""
    asset.name = form.name
    asset.asset_type = form.asset_type
    asset.ticker = form.ticker or None

    becomes_manual = form.manual_price and not asset.manual_price
    asset.manual_price = form.manual_price
    if becomes_manual and asset.price_scale != 1.0:
        # Manual prices are typed in the major unit. Re-express the last quote
        # (e.g. 8250 pence) in that unit (82.50 pounds) before dropping the scale.
        last_price = pricing.latest_prices(session).get(asset.id)
        scaled_price = last_price.price * asset.price_scale if last_price else None
        asset.price_scale = 1.0
        if scaled_price is not None:
            pricing.set_manual_price(session, asset, scaled_price, settings)
