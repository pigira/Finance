"""Envelopes ("comptes"): the tax wrappers that hold lots."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import calc
from app.models import Envelope, Transaction
from app.schemas import EnvelopeForm
from app.services.errors import FormError

# The cost properties, in the order the form shows them (columns of Envelope and
# fields of EnvelopeForm share these names).
COST_FIELDS = (
    "management_fee_pct",
    "custody_fee_pct",
    "custody_fee_per_position_eur",
    "account_fee_eur",
    "order_fee_pct",
    "order_fee_min_eur",
    "entry_fee_pct",
    "exit_tax_rate_before_pct",
    "exit_tax_threshold_years",
    "exit_tax_rate_after_pct",
)

# Defaults each Envelope type suggests: the exit tax only, since fees depend on the
# broker. Rates as of DEFAULTS_AS_OF, with their sources in DECISIONS.md (#54):
# PFU 12.8 % + social charges 18.6 % (17.2 % on assurance-vie); 5 years for a PEA,
# 8 years for an assurance-vie (7.5 % after). A PER is taxed at the income tax rate:
# no default.
DEFAULTS_AS_OF = date(2026, 11, 21)
DEFAULT_EXIT_TAX: dict[str, calc.ExitTax | None] = {
    "cto": calc.ExitTax(rate_before_pct=31.4),
    "pea": calc.ExitTax(rate_before_pct=31.4, threshold_years=5, rate_after_pct=18.6),
    "pea_pme": calc.ExitTax(rate_before_pct=31.4, threshold_years=5, rate_after_pct=18.6),
    "assurance_vie": calc.ExitTax(rate_before_pct=30.0, threshold_years=8, rate_after_pct=24.7),
    "per": None,
}

# French labels of models.ENVELOPE_TYPES, in the order the form offers them.
ENVELOPE_TYPE_LABELS = {
    "cto": "CTO",
    "pea": "PEA",
    "pea_pme": "PEA-PME",
    "assurance_vie": "Assurance-vie",
    "per": "PER",
}


def all_envelopes(session: Session) -> list[Envelope]:
    """Every envelope, by name (Paramètres and the purchase form list them all)."""
    return list(session.scalars(select(Envelope).order_by(Envelope.name)))


def last_used_envelope(session: Session) -> Envelope | None:
    """The envelope of the latest purchase typed in; the oldest envelope before any."""
    latest = session.scalar(
        select(Transaction).order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(1)
    )
    if latest is not None:
        return latest.envelope
    return session.scalar(select(Envelope).order_by(Envelope.id).limit(1))


def default_exit_tax_fields(envelope_type: str) -> dict[str, float | None]:
    """The exit-tax fields an envelope type suggests, by their form and column names."""
    tax = DEFAULT_EXIT_TAX[envelope_type]
    return {
        "exit_tax_rate_before_pct": tax.rate_before_pct if tax else None,
        "exit_tax_threshold_years": tax.threshold_years if tax else None,
        "exit_tax_rate_after_pct": tax.rate_after_pct if tax else None,
    }


def effective_opening_date(envelope: Envelope) -> date | None:
    """The opening date typed in; without one, the date of its oldest purchase."""
    if envelope.opening_date is not None:
        return envelope.opening_date
    return min((lot.trade_date for lot in envelope.transactions), default=None)


def cost_properties(envelope: Envelope) -> calc.EnvelopeCosts:
    """The envelope's cost properties for calc: a fee left empty is no fee, an exit tax
    without its first rate is unknown."""
    exit_tax = None
    if envelope.exit_tax_rate_before_pct is not None:
        exit_tax = calc.ExitTax(
            rate_before_pct=envelope.exit_tax_rate_before_pct,
            threshold_years=envelope.exit_tax_threshold_years,
            rate_after_pct=envelope.exit_tax_rate_after_pct,
        )
    return calc.EnvelopeCosts(
        management_fee_pct=envelope.management_fee_pct or 0.0,
        custody_fee_pct=envelope.custody_fee_pct or 0.0,
        custody_fee_per_position_eur=envelope.custody_fee_per_position_eur or 0.0,
        account_fee_eur=envelope.account_fee_eur or 0.0,
        order_fee_pct=envelope.order_fee_pct or 0.0,
        order_fee_min_eur=envelope.order_fee_min_eur or 0.0,
        exit_tax=exit_tax,
    )


def create_envelope(session: Session, form: EnvelopeForm) -> Envelope:
    """Add an envelope. Names are unique."""
    _check_name_is_free(session, form.name)
    envelope = Envelope()
    _apply_fields(envelope, form)
    session.add(envelope)
    session.commit()
    return envelope


def update_envelope(session: Session, envelope: Envelope, form: EnvelopeForm) -> Envelope:
    """Edit an envelope. Its opening date cannot come after one of its purchases."""
    _check_name_is_free(session, form.name, own_id=envelope.id)
    oldest = session.scalar(
        select(func.min(Transaction.trade_date)).where(Transaction.envelope_id == envelope.id)
    )
    if form.opening_date is not None and oldest is not None and oldest < form.opening_date:
        raise FormError("opening_date", "Un achat de ce compte est antérieur à cette date")
    _apply_fields(envelope, form)
    session.commit()
    return envelope


def delete_envelope(session: Session, envelope: Envelope) -> None:
    """Only an empty envelope can go, and never the last one."""
    if envelope.transactions:
        raise FormError(
            "form",
            f"« {envelope.name} » contient des achats : déplacez-les vers un autre compte "
            "ou supprimez-les d'abord.",
        )
    if session.scalar(select(func.count()).select_from(Envelope)) == 1:
        raise FormError("form", "Il faut au moins un compte pour enregistrer un achat.")
    session.delete(envelope)
    session.commit()


def _check_name_is_free(session: Session, name: str, own_id: int | None = None) -> None:
    other = session.scalar(select(Envelope).where(Envelope.name == name))
    if other is not None and other.id != own_id:
        raise FormError("name", "Un compte porte déjà ce nom")


def _apply_fields(envelope: Envelope, form: EnvelopeForm) -> None:
    envelope.name = form.name
    envelope.envelope_type = form.envelope_type
    envelope.broker = form.broker
    envelope.opening_date = form.opening_date
    for name in COST_FIELDS:
        setattr(envelope, name, getattr(form, name))
