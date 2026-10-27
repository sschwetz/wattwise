from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.jobs.demand_charge_job import invalidate_demand_data
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
    WindowUpdate,
)
from core_api.time_utils import as_utc

router = APIRouter(prefix="/sites/{site_id}", tags=["billing"])


def _get_owned_tariff(db: Session, site: Site, tariff_id: int) -> SiteTariff:
    """The site's SiteTariff by id, or a 404 if it doesn't exist or belongs
    to another site -- shared by every endpoint nested under a tariff."""
    tariff = db.get(SiteTariff, tariff_id)
    if tariff is None or tariff.site_id != site.id:
        raise AppError(404, "not_found", f"Tariff {tariff_id} not found")
    return tariff


def _earliest_change(old_end, new_end):
    """The first instant whose dates differ when an item's end moves from
    `old_end` to `new_end`; an old end of None is open ended."""
    new_end = as_utc(new_end)
    return new_end if old_end is None else min(as_utc(old_end), new_end)


def _require_end_after_start(start, end) -> None:
    if as_utc(end) <= as_utc(start):
        raise AppError(422, "invalid_dates", "effective_to must be after effective_from")


def _reject_overlap(rows, start, end, *, what: str, exclude_id: int | None = None) -> None:
    """422 if [start, end) overlaps another row's span. `end` None is open ended."""
    for row in rows:
        if row.id == exclude_id:
            continue
        row_end = None if row.effective_to is None else as_utc(row.effective_to)
        if (row_end is None or start < row_end) and (end is None or as_utc(row.effective_from) < end):
            raise AppError(422, "overlap", f"That {what} would overlap another one. End the earlier {what} first.")


def _check_window_inside_tariff(tariff: SiteTariff, start, end) -> None:
    """A window starts inside its tariff and, when the tariff has an end,
    does not end after it. An open-ended window is allowed under an ended
    tariff: it stops applying when the tariff does."""
    if start < as_utc(tariff.effective_from):
        raise AppError(422, "window_outside_tariff", "The window cannot start before its tariff starts")
    if tariff.effective_to is None:
        return
    tariff_end = as_utc(tariff.effective_to)
    if start >= tariff_end or (end is not None and end > tariff_end):
        raise AppError(422, "window_outside_tariff", "The window cannot run past the end of its tariff")


def _create_dated_window(db: Session, site: Site, tariff_id: int, model_cls: type[Any], payload):
    """Create-under-an-owned-tariff for the window types with their own dates.
    A missing effective_from becomes the tariff's start."""
    tariff = _get_owned_tariff(db, site, tariff_id)
    data = payload.model_dump()
    start = as_utc(data["effective_from"] or tariff.effective_from)
    end = data["effective_to"]
    if end is not None:
        _require_end_after_start(start, end)
    _check_window_inside_tariff(tariff, start, end)
    row = model_cls(site_tariff_id=tariff.id, **{**data, "effective_from": start})
    db.add(row)
    if model_cls is DemandWindow:
        invalidate_demand_data(db, site.id, start)
    db.commit()
    db.refresh(row)
    return row


def _end_dated_window(db: Session, site: Site, tariff_id: int, window_id: int, model_cls: type[Any], payload: WindowUpdate):
    tariff = _get_owned_tariff(db, site, tariff_id)
    window = db.get(model_cls, window_id)
    if window is None or window.site_tariff_id != tariff.id:
        raise AppError(404, "not_found", f"Window {window_id} not found")
    _require_end_after_start(window.effective_from, payload.effective_to)
    _check_window_inside_tariff(tariff, as_utc(window.effective_from), payload.effective_to)
    if model_cls is DemandWindow:
        invalidate_demand_data(db, site.id, _earliest_change(window.effective_to, payload.effective_to))
    window.effective_to = payload.effective_to
    db.commit()
    db.refresh(window)
    return window


def _create_tariff_child(db: Session, site: Site, tariff_id: int, model_cls: type[Any], payload):
    """Create-under-an-owned-tariff, shared by every window type (DemandWindow,
    ExportAdjustmentWindow, ConditionalCreditWindow, MonthlyFeeDiscountWindow) that's just
    `site_tariff_id` plus the payload's own fields."""
    tariff = _get_owned_tariff(db, site, tariff_id)
    row = model_cls(site_tariff_id=tariff.id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _list_tariff_children(db: Session, site: Site, tariff_id: int, model_cls: type[Any]):
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
    existing = db.execute(select(SiteTariff).where(SiteTariff.site_id == site.id)).scalars().all()
    _reject_overlap(existing, payload.effective_from, payload.effective_to, what="tariff")
    tariff = SiteTariff(site_id=site.id, **payload.model_dump())
    db.add(tariff)
    invalidate_demand_data(db, site.id, payload.effective_from)
    db.commit()
    db.refresh(tariff)
    return tariff


@router.get("/tariffs", response_model=list[SiteTariffOut])
def list_tariffs(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[SiteTariff]:
    return list(db.execute(select(SiteTariff).where(SiteTariff.site_id == site.id)).scalars())


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
    _require_end_after_start(tariff.effective_from, payload.effective_to)
    siblings = db.execute(select(SiteTariff).where(SiteTariff.site_id == site.id)).scalars().all()
    _reject_overlap(siblings, as_utc(tariff.effective_from), payload.effective_to, what="tariff", exclude_id=tariff.id)
    invalidate_demand_data(db, site.id, _earliest_change(tariff.effective_to, payload.effective_to))
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
    return _create_dated_window(db, site, tariff_id, DemandWindow, payload)


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
    return _create_dated_window(db, site, tariff_id, ExportAdjustmentWindow, payload)


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
    return _create_dated_window(db, site, tariff_id, ConditionalCreditWindow, payload)


@router.get("/tariffs/{tariff_id}/conditional-credits", response_model=list[ConditionalCreditWindowOut])
def list_conditional_credit_windows(
    tariff_id: int,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[ConditionalCreditWindow]:
    return _list_tariff_children(db, site, tariff_id, ConditionalCreditWindow)


@router.patch("/tariffs/{tariff_id}/demand-windows/{window_id}", response_model=DemandWindowOut)
def end_demand_windows(
    tariff_id: int,
    window_id: int,
    payload: WindowUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> DemandWindow:
    return _end_dated_window(db, site, tariff_id, window_id, DemandWindow, payload)


@router.patch("/tariffs/{tariff_id}/export-adjustments/{window_id}", response_model=ExportAdjustmentWindowOut)
def end_export_adjustments(
    tariff_id: int,
    window_id: int,
    payload: WindowUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> ExportAdjustmentWindow:
    return _end_dated_window(db, site, tariff_id, window_id, ExportAdjustmentWindow, payload)


@router.patch("/tariffs/{tariff_id}/conditional-credits/{window_id}", response_model=ConditionalCreditWindowOut)
def end_conditional_credits(
    tariff_id: int,
    window_id: int,
    payload: WindowUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> ConditionalCreditWindow:
    return _end_dated_window(db, site, tariff_id, window_id, ConditionalCreditWindow, payload)


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
    existing = db.execute(select(BillingCycle).where(BillingCycle.site_id == site.id)).scalars().all()
    _reject_overlap(existing, payload.effective_from, payload.effective_to, what="billing cycle")
    cycle = BillingCycle(site_id=site.id, **payload.model_dump())
    db.add(cycle)
    invalidate_demand_data(db, site.id, payload.effective_from)
    db.commit()
    db.refresh(cycle)
    return cycle


@router.get("/billing-cycle", response_model=list[BillingCycleOut])
def list_billing_cycles(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[BillingCycle]:
    return list(db.execute(select(BillingCycle).where(BillingCycle.site_id == site.id)).scalars())


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
    _require_end_after_start(cycle.effective_from, payload.effective_to)
    siblings = db.execute(select(BillingCycle).where(BillingCycle.site_id == site.id)).scalars().all()
    _reject_overlap(siblings, as_utc(cycle.effective_from), payload.effective_to, what="billing cycle", exclude_id=cycle.id)
    invalidate_demand_data(db, site.id, _earliest_change(cycle.effective_to, payload.effective_to))
    cycle.effective_to = payload.effective_to
    db.commit()
    db.refresh(cycle)
    return cycle
