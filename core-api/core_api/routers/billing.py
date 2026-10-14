from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.models.billing import (
    BillingCycle,
    ConditionalCreditWindow,
    DemandWindow,
    ExportAdjustmentWindow,
    MonthlyFeeDiscountWindow,
    SiteTariff,
)
from core_api.models.identity import Site
from core_api.schemas import (
    BillingCycleCreate,
    BillingCycleOut,
    BillingCycleUpdate,
    ConditionalCreditWindowCreate,
    ConditionalCreditWindowOut,
    DemandWindowCreate,
    DemandWindowOut,
    ExportAdjustmentWindowCreate,
    ExportAdjustmentWindowOut,
    MonthlyFeeDiscountWindowCreate,
    MonthlyFeeDiscountWindowOut,
    SiteTariffCreate,
    SiteTariffOut,
    SiteTariffUpdate,
)

router = APIRouter(prefix="/sites/{site_id}", tags=["billing"])


def _get_owned_tariff(db: Session, site: Site, tariff_id: int) -> SiteTariff:
    """The site's SiteTariff by id, or a 404 if it doesn't exist or belongs
    to another site -- shared by every endpoint nested under a tariff."""
    tariff = db.get(SiteTariff, tariff_id)
    if tariff is None or tariff.site_id != site.id:
        raise AppError(404, "not_found", f"Tariff {tariff_id} not found")
    return tariff


def _create_tariff_child(db: Session, site: Site, tariff_id: int, model_cls: type, payload):
    """Create-under-an-owned-tariff, shared by every window type (DemandWindow,
    ExportAdjustmentWindow, ConditionalCreditWindow, MonthlyFeeDiscountWindow) that's just
    `site_tariff_id` plus the payload's own fields."""
    tariff = _get_owned_tariff(db, site, tariff_id)
    row = model_cls(site_tariff_id=tariff.id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _list_tariff_children(db: Session, site: Site, tariff_id: int, model_cls: type):
    """List-under-an-owned-tariff, shared by every window type -- see
    _create_tariff_child."""
    tariff = _get_owned_tariff(db, site, tariff_id)
    return db.execute(select(model_cls).where(model_cls.site_tariff_id == tariff.id)).scalars().all()


@router.post("/tariffs", response_model=SiteTariffOut, status_code=status.HTTP_201_CREATED)
def create_tariff(
    payload: SiteTariffCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> SiteTariff:
    tariff = SiteTariff(site_id=site.id, **payload.model_dump())
    db.add(tariff)
    db.commit()
    db.refresh(tariff)
    return tariff


@router.get("/tariffs", response_model=list[SiteTariffOut])
def list_tariffs(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[SiteTariff]:
    return db.execute(select(SiteTariff).where(SiteTariff.site_id == site.id)).scalars().all()


@router.patch("/tariffs/{tariff_id}", response_model=SiteTariffOut)
def update_tariff(
    tariff_id: int,
    payload: SiteTariffUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> SiteTariff:
    """Closing effective_to is the delete-equivalent for this effective-dated
    entity (spec's design principle, already applied to Source)."""
    tariff = _get_owned_tariff(db, site, tariff_id)
    tariff.effective_to = payload.effective_to
    db.commit()
    db.refresh(tariff)
    return tariff


@router.post(
    "/tariffs/{tariff_id}/demand-windows",
    response_model=DemandWindowOut,
    status_code=status.HTTP_201_CREATED,
)
def create_demand_window(
    tariff_id: int,
    payload: DemandWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> DemandWindow:
    return _create_tariff_child(db, site, tariff_id, DemandWindow, payload)


@router.get("/tariffs/{tariff_id}/demand-windows", response_model=list[DemandWindowOut])
def list_demand_windows(
    tariff_id: int,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[DemandWindow]:
    return _list_tariff_children(db, site, tariff_id, DemandWindow)


@router.post(
    "/tariffs/{tariff_id}/export-adjustments",
    response_model=ExportAdjustmentWindowOut,
    status_code=status.HTTP_201_CREATED,
)
def create_export_adjustment_window(
    tariff_id: int,
    payload: ExportAdjustmentWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> ExportAdjustmentWindow:
    return _create_tariff_child(db, site, tariff_id, ExportAdjustmentWindow, payload)


@router.get("/tariffs/{tariff_id}/export-adjustments", response_model=list[ExportAdjustmentWindowOut])
def list_export_adjustment_windows(
    tariff_id: int,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[ExportAdjustmentWindow]:
    return _list_tariff_children(db, site, tariff_id, ExportAdjustmentWindow)


@router.post(
    "/tariffs/{tariff_id}/conditional-credits",
    response_model=ConditionalCreditWindowOut,
    status_code=status.HTTP_201_CREATED,
)
def create_conditional_credit_window(
    tariff_id: int,
    payload: ConditionalCreditWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> ConditionalCreditWindow:
    return _create_tariff_child(db, site, tariff_id, ConditionalCreditWindow, payload)


@router.get("/tariffs/{tariff_id}/conditional-credits", response_model=list[ConditionalCreditWindowOut])
def list_conditional_credit_windows(
    tariff_id: int,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[ConditionalCreditWindow]:
    return _list_tariff_children(db, site, tariff_id, ConditionalCreditWindow)


@router.post(
    "/tariffs/{tariff_id}/monthly-fee-discounts",
    response_model=MonthlyFeeDiscountWindowOut,
    status_code=status.HTTP_201_CREATED,
)
def create_monthly_fee_discount_window(
    tariff_id: int,
    payload: MonthlyFeeDiscountWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> MonthlyFeeDiscountWindow:
    return _create_tariff_child(db, site, tariff_id, MonthlyFeeDiscountWindow, payload)


@router.get("/tariffs/{tariff_id}/monthly-fee-discounts", response_model=list[MonthlyFeeDiscountWindowOut])
def list_monthly_fee_discount_windows(
    tariff_id: int,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[MonthlyFeeDiscountWindow]:
    return _list_tariff_children(db, site, tariff_id, MonthlyFeeDiscountWindow)


@router.post("/billing-cycle", response_model=BillingCycleOut, status_code=status.HTTP_201_CREATED)
def create_billing_cycle(
    payload: BillingCycleCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> BillingCycle:
    cycle = BillingCycle(site_id=site.id, **payload.model_dump())
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


@router.get("/billing-cycle", response_model=list[BillingCycleOut])
def list_billing_cycles(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[BillingCycle]:
    return db.execute(select(BillingCycle).where(BillingCycle.site_id == site.id)).scalars().all()


@router.patch("/billing-cycle/{cycle_id}", response_model=BillingCycleOut)
def update_billing_cycle(
    cycle_id: int,
    payload: BillingCycleUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> BillingCycle:
    """Closing effective_to is the delete-equivalent for this effective-dated
    entity (spec's design principle, already applied to Source)."""
    cycle = db.get(BillingCycle, cycle_id)
    if cycle is None or cycle.site_id != site.id:
        raise AppError(404, "not_found", f"Billing cycle {cycle_id} not found")
    cycle.effective_to = payload.effective_to
    db.commit()
    db.refresh(cycle)
    return cycle
