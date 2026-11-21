"""Envelopes ("comptes"): the tax wrappers that hold lots."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Envelope, Transaction
from app.schemas import EnvelopeForm

DEFAULT_ENVELOPE_NAME = "Compte par défaut"

# French labels, in the order the form offers them.
ENVELOPE_TYPE_LABELS = {
    "cto": "CTO",
    "pea": "PEA",
    "pea_pme": "PEA-PME",
    "assurance_vie": "Assurance-vie",
    "per": "PER",
}


class EnvelopeError(Exception):
    """A business rule refused the form. `field` says where to show `message`."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def all_envelopes(session: Session) -> list[Envelope]:
    return list(session.scalars(select(Envelope).order_by(Envelope.name)))


def last_used_envelope(session: Session) -> Envelope | None:
    """The envelope of the latest purchase typed in; the oldest envelope before any."""
    latest = session.scalar(
        select(Transaction).order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(1)
    )
    if latest is not None:
        return latest.envelope
    return session.scalar(select(Envelope).order_by(Envelope.id).limit(1))


def ensure_default_envelope(session: Session) -> None:
    """A purchase needs an envelope: make sure at least one exists."""
    if session.scalar(select(Envelope.id).limit(1)) is None:
        session.add(Envelope(name=DEFAULT_ENVELOPE_NAME, envelope_type="cto"))


def create_envelope(session: Session, form: EnvelopeForm) -> Envelope:
    _check_name_is_free(session, form.name)
    envelope = Envelope()
    _apply_fields(envelope, form)
    session.add(envelope)
    session.commit()
    return envelope


def update_envelope(session: Session, envelope: Envelope, form: EnvelopeForm) -> Envelope:
    _check_name_is_free(session, form.name, own_id=envelope.id)
    oldest = session.scalar(
        select(func.min(Transaction.trade_date)).where(Transaction.envelope_id == envelope.id)
    )
    if form.opening_date is not None and oldest is not None and oldest < form.opening_date:
        raise EnvelopeError("opening_date", "Un achat de ce compte est antérieur à cette date")
    _apply_fields(envelope, form)
    session.commit()
    return envelope


def delete_envelope(session: Session, envelope: Envelope) -> None:
    """Only an empty envelope can go, and never the last one."""
    if envelope.transactions:
        raise EnvelopeError(
            "delete",
            f"« {envelope.name} » contient des achats : déplacez-les vers un autre compte "
            "ou supprimez-les d'abord.",
        )
    if session.scalar(select(func.count()).select_from(Envelope)) == 1:
        raise EnvelopeError("delete", "Il faut au moins un compte pour enregistrer un achat.")
    session.delete(envelope)
    session.commit()


def _check_name_is_free(session: Session, name: str, own_id: int | None = None) -> None:
    other = session.scalar(select(Envelope).where(Envelope.name == name))
    if other is not None and other.id != own_id:
        raise EnvelopeError("name", "Un compte porte déjà ce nom")


def _apply_fields(envelope: Envelope, form: EnvelopeForm) -> None:
    envelope.name = form.name
    envelope.envelope_type = form.envelope_type
    envelope.broker = form.broker
    envelope.opening_date = form.opening_date
