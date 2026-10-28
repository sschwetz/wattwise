from __future__ import annotations

import datetime
import decimal
import math
import zoneinfo
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, EmailStr, Field, field_validator, model_validator

from core_api.live import ENERGY_LIVE_METRICS, MAX_LIVE_VALUES, WEATHER_LIVE_METRICS
from core_api.models.identity import InviteStatus
from core_api.models.local_rates import LOCAL_RATE_CHANNELS
from core_api.models.pricing import PriceChannel, SpikeStatus
from core_api.models.readings import EnergyMetric, WeatherMetric
from core_api.models.sources import SourceKind

# A 1-day span comfortably covers real-world price intervals (typically 5-30
# minutes) while capping the per-request fan-out cost in
# ingestion._fan_out_price_readings at 1440 inserts.
MAX_PRICE_INTERVAL_MINUTES = 1440
MAX_TARIFF_NAME_LENGTH = 100


def _to_utc(value: datetime.datetime) -> datetime.datetime:
    """Normalize any inbound datetime to an aware UTC value.

    A naive value (no offset in the request) is assumed to already be UTC.
    An aware value is converted to UTC so that, e.g., `+10:00` and `Z`
    timestamps for the same instant compare/round-trip identically once
    stored (SQLite's DateTime column silently drops tzinfo on write)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


UtcDatetime = Annotated[datetime.datetime, AfterValidator(_to_utc)]


def _strip_and_require_nonblank(value: str) -> str:
    """Reject a name that is blank after trimming surrounding whitespace --
    bare min_length=1 lets " " through, which then renders as a blank header
    label with no fallback (display_name is non-null once set, so the email
    fallback never kicks back in)."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginChallenge(BaseModel):
    status: Literal["totp_setup_required", "totp_required"]
    setup_token: str | None = None
    mfa_token: str | None = None


# min_length=8 because a bare `str` accepted "" — an empty password registered
# a working account. max_length=72 because bcrypt silently truncates its input
# at 72 bytes, so anything longer creates a false sense of entropy; rejecting
# it is honest rather than pretending the extra characters count.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 72
Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


class RegisterRequest(BaseModel):
    email: EmailStr


class RegisterOut(BaseModel):
    message: str


class RegisterConfirmIn(BaseModel):
    token: str
    password: Password


class TotpSetupStartOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpSetupConfirmIn(BaseModel):
    code: str


class TotpVerifyIn(BaseModel):
    code: str


class TotpSetupConfirmOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    recovery_codes: list[str]


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetRequestOut(BaseModel):
    message: str


class PasswordResetConfirmIn(BaseModel):
    token: str
    new_password: Password


class UserMeOut(BaseModel):
    id: int
    email: str
    display_name: str | None
    # Never the raw key -- just whether one is on file, so the UI can show
    # "configured" without ever having the secret pass back through a response.
    has_amber_api_key: bool
    # Never the stored content-type -- the UI only ever needs true/false to decide
    # whether to fetch the image at all.
    has_avatar: bool


class UpdateAmberApiKeyIn(BaseModel):
    amber_api_key: str = Field(min_length=1)


class UpdateDisplayNameIn(BaseModel):
    display_name: Annotated[str, Field(max_length=100), AfterValidator(_strip_and_require_nonblank)]


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: Password
    totp_code: str


class RequestEmailChangeIn(BaseModel):
    new_email: EmailStr
    current_password: str
    totp_code: str


class RequestEmailChangeOut(BaseModel):
    message: str


class ConfirmEmailChangeIn(BaseModel):
    token: str


class StartTotpReenrollIn(BaseModel):
    current_password: str
    totp_code: str


class TotpReenrollStartOut(BaseModel):
    secret: str
    otpauth_uri: str


class ConfirmTotpReenrollIn(BaseModel):
    code: str


class TotpReenrollConfirmOut(BaseModel):
    recovery_codes: list[str]


@lru_cache(maxsize=1)
def _known_timezones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def validate_timezone(value: str) -> str:
    """Accepts only IANA zone names the server's tz database knows."""
    if value not in _known_timezones():
        raise ValueError(f"unknown timezone: {value!r}")
    return value


class SiteCreate(BaseModel):
    name: str
    timezone: Annotated[str, AfterValidator(validate_timezone)]


class SiteOut(BaseModel):
    id: int
    owner_user_id: int
    name: str
    timezone: str
    enabled: bool

    model_config = {"from_attributes": True}


class InviteCreate(BaseModel):
    email: EmailStr


class InviteOut(BaseModel):
    id: int
    site_id: int
    email: str
    status: InviteStatus
    created_at: datetime.datetime
    accepted_at: datetime.datetime | None
    declined_at: datetime.datetime | None
    revoked_at: datetime.datetime | None
    inviter_user_id: int
    inviter_email: str


class PendingInviteOut(BaseModel):
    id: int
    site_id: int
    site_name: str
    inviter_email: str
    created_at: datetime.datetime


class SiteUpdate(BaseModel):
    name: str | None = None
    timezone: Annotated[str, AfterValidator(validate_timezone)] | None = None
    enabled: bool | None = None


def normalize_tariff_name(name: str | None) -> str:
    """Strips a local tariff's name and checks it is 1-MAX_TARIFF_NAME_LENGTH
    characters. Raises ValueError otherwise. Used on create and on rename."""
    stripped = (name or "").strip()
    if not stripped or len(stripped) > MAX_TARIFF_NAME_LENGTH:
        raise ValueError(f"A local tariff needs a name of 1-{MAX_TARIFF_NAME_LENGTH} characters")
    return stripped


class SourceCreate(BaseModel):
    kind: SourceKind
    provider: str
    config: dict = {}
    name: str | None = None

    @model_validator(mode="after")
    def _local_provider_rules(self) -> SourceCreate:
        if self.provider != "local":
            return self
        if self.kind != SourceKind.PRICING:
            raise ValueError("The local provider is only available for pricing sources")
        self.name = normalize_tariff_name(self.name)
        return self


class SourceOut(BaseModel):
    id: int
    site_id: int
    kind: SourceKind
    provider: str
    name: str | None
    enabled: bool
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None
    renewal_url: str | None = None
    config: dict

    model_config = {"from_attributes": True}


class SourceUpdate(BaseModel):
    enabled: bool | None = None
    effective_to: UtcDatetime | None = None
    config: dict | None = None
    name: str | None = None


class EnergyReadingIn(BaseModel):
    ts: UtcDatetime
    metric: EnergyMetric
    value: float


class EnergyReadingBatch(BaseModel):
    readings: list[EnergyReadingIn]


class WeatherReadingIn(BaseModel):
    ts: UtcDatetime
    metric: WeatherMetric
    value: float


class WeatherReadingBatch(BaseModel):
    readings: list[WeatherReadingIn]


def _check_live_values(values: dict[str, float], allowed: frozenset[str]) -> dict[str, float]:
    if not 1 <= len(values) <= MAX_LIVE_VALUES:
        raise ValueError(f"values must hold between 1 and {MAX_LIVE_VALUES} entries")
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unknown live metrics: {', '.join(unknown)}")
    if not all(math.isfinite(v) for v in values.values()):
        raise ValueError("values must be finite numbers")
    return values


class EnergyLiveSampleIn(BaseModel):
    ts: UtcDatetime
    values: dict[str, float]

    @field_validator("values")
    @classmethod
    def _values_valid(cls, v: dict[str, float]) -> dict[str, float]:
        return _check_live_values(v, ENERGY_LIVE_METRICS)


class WeatherLiveSampleIn(BaseModel):
    ts: UtcDatetime
    values: dict[str, float]

    @field_validator("values")
    @classmethod
    def _values_valid(cls, v: dict[str, float]) -> dict[str, float]:
        return _check_live_values(v, WEATHER_LIVE_METRICS)


class PriceIntervalIn(BaseModel):
    channel: PriceChannel
    interval_start: UtcDatetime
    interval_end: UtcDatetime
    price: decimal.Decimal
    price_low: decimal.Decimal | None = None
    price_high: decimal.Decimal | None = None
    spike_status: SpikeStatus = SpikeStatus.NONE
    is_demand_window: bool = False

    @model_validator(mode="after")
    def _validate_interval_range(self) -> PriceIntervalIn:
        if self.interval_end <= self.interval_start:
            raise ValueError("interval_end must be after interval_start")
        span = self.interval_end - self.interval_start
        if span > datetime.timedelta(minutes=MAX_PRICE_INTERVAL_MINUTES):
            raise ValueError(
                f"interval span cannot exceed {MAX_PRICE_INTERVAL_MINUTES} minutes"
            )
        return self


class PriceIntervalBatch(BaseModel):
    intervals: list[PriceIntervalIn]


class GapMarkerCreate(BaseModel):
    channel: PriceChannel
    gap_date: datetime.date


class SiteDataFloorOut(BaseModel):
    floor_ts: datetime.datetime | None


class ChannelCoverageOut(BaseModel):
    first: UtcDatetime
    last: UtcDatetime


class PriceCoverageOut(BaseModel):
    channels: dict[str, ChannelCoverageOut]


class RepriceMarkerClear(BaseModel):
    version: int


class ReadingOut(BaseModel):
    # UtcDatetime, not a plain datetime: rows read back from SQLite are naive
    # (tzinfo is dropped on write) but are always actually UTC. Without this,
    # a naive value serializes with no offset suffix at all, and a browser's
    # `new Date(...)` on a timezone-less ISO string parses it as local time
    # instead of UTC -- silently skewing every client-side date computation
    # by the viewer's UTC offset.
    ts: UtcDatetime
    metric: str
    value: float

    model_config = {"from_attributes": True}


class LatestReadingOut(BaseModel):
    # None when the site has no readings yet.
    ts: UtcDatetime | None


class PriceReadingOut(ReadingOut):
    # True when this reading sits in a forecast interval that arrived without
    # Amber's advancedPrice band, so its value is the plain perKwh forecast.
    forecast_fallback: bool = False
    # Amber's advancedPrice band for the reading's interval, raw (not adjusted
    # like `value` is on the sell channel). None for settled intervals and
    # fallback forecasts.
    price_low: float | None = None
    price_high: float | None = None
    spike_status: SpikeStatus = SpikeStatus.NONE


class DateRangeCheck(BaseModel):
    """Rejects an effective_to that is not after effective_from, for the
    schemas that carry both. effective_from may be None where the API fills
    it in (the tariff window schemas)."""

    @model_validator(mode="after")
    def _check_dates(self) -> DateRangeCheck:
        start, end = getattr(self, "effective_from", None), getattr(self, "effective_to", None)
        if start is not None and end is not None and end <= start:
            raise ValueError("effective_to must be after effective_from")
        return self


class DatedWindowCreate(DateRangeCheck):
    """The optional dates of a tariff window. A missing effective_from becomes
    the tariff's start."""

    effective_from: UtcDatetime | None = None
    effective_to: UtcDatetime | None = None


class SiteTariffCreate(DateRangeCheck):
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None = None
    service_availability_c_per_day: decimal.Decimal
    monthly_fee_c: decimal.Decimal | None = None


class SiteTariffOut(SiteTariffCreate):
    id: int
    site_id: int

    model_config = {"from_attributes": True}


class SiteTariffUpdate(BaseModel):
    effective_to: UtcDatetime


WindowUpdate = SiteTariffUpdate


class DemandWindowCreate(DatedWindowCreate):
    label: str
    months: list[int]
    days_of_week: list[int]
    start_time: datetime.time
    end_time: datetime.time
    rate_c_per_kw_per_day: decimal.Decimal
    measurement_interval_minutes: int = 30


class DemandWindowOut(DemandWindowCreate):
    id: int
    site_tariff_id: int
    effective_from: UtcDatetime

    model_config = {"from_attributes": True}


class ExportAdjustmentWindowCreate(DatedWindowCreate):
    label: str
    months: list[int]
    days_of_week: list[int]
    start_time: datetime.time
    end_time: datetime.time
    adjustment_c_per_kwh: decimal.Decimal
    daily_allowance_kwh: decimal.Decimal = Field(default=decimal.Decimal("0"), ge=0, max_digits=10, decimal_places=4)


class ExportAdjustmentWindowOut(ExportAdjustmentWindowCreate):
    id: int
    site_tariff_id: int
    effective_from: UtcDatetime

    model_config = {"from_attributes": True}


def _check_time_pattern(
    months: list[int], days_of_week: list[int], start_time: datetime.time, end_time: datetime.time
) -> None:
    """The months/days-of-week/time-of-day checks shared by the window types
    that take a whole-minute pattern (LocalRateWindowCreate and
    ConditionalCreditWindowCreate)."""
    if any(month < 1 or month > 12 for month in months):
        raise ValueError("months must be between 1 and 12")
    if any(day < 0 or day > 6 for day in days_of_week):
        raise ValueError("days_of_week must be between 0 (Monday) and 6 (Sunday)")
    # SQLite stores a time without its offset, so an offset would be lost.
    if any(t.tzinfo is not None for t in (start_time, end_time)):
        raise ValueError("start_time and end_time must not carry a timezone offset")
    if any(t.second or t.microsecond for t in (start_time, end_time)):
        raise ValueError("start_time and end_time must be whole minutes")


class ConditionalCreditWindowCreate(DatedWindowCreate):
    label: str = Field(min_length=1, max_length=100)
    months: list[int] = Field(min_length=1)
    days_of_week: list[int] = Field(min_length=1)
    start_time: datetime.time
    end_time: datetime.time
    max_avg_import_w: decimal.Decimal = Field(gt=0, max_digits=10, decimal_places=4)
    credit_c: decimal.Decimal = Field(gt=0, max_digits=10, decimal_places=4)

    @model_validator(mode="after")
    def _check_pattern(self) -> ConditionalCreditWindowCreate:
        # Whole minutes matter here because cost.py counts one reading per minute.
        _check_time_pattern(self.months, self.days_of_week, self.start_time, self.end_time)
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class ConditionalCreditWindowOut(ConditionalCreditWindowCreate):
    id: int
    site_tariff_id: int
    effective_from: UtcDatetime

    model_config = {"from_attributes": True}


class MonthlyFeeDiscountWindowCreate(BaseModel):
    label: str
    starts_at: UtcDatetime
    ends_at: UtcDatetime
    discounted_fee_c: decimal.Decimal


class MonthlyFeeDiscountWindowOut(MonthlyFeeDiscountWindowCreate):
    id: int
    site_tariff_id: int

    model_config = {"from_attributes": True}


class LocalRateWindowCreate(BaseModel):
    channel: PriceChannel
    label: str = Field(min_length=1, max_length=100)
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None = None
    months: list[int] = Field(min_length=1)
    days_of_week: list[int] = Field(min_length=1)
    start_time: datetime.time
    end_time: datetime.time
    rate_c_per_kwh: decimal.Decimal = Field(ge=0, max_digits=12, decimal_places=4)

    @field_validator("channel")
    @classmethod
    def _check_channel(cls, channel: PriceChannel) -> PriceChannel:
        # The table's CHECK allows only these channels. Rejecting the rest here
        # makes the API answer 422 instead of a 500 from the database.
        if channel not in LOCAL_RATE_CHANNELS:
            allowed = ", ".join(c.value for c in LOCAL_RATE_CHANNELS)
            raise ValueError(f"channel must be one of {allowed}")
        return channel

    @model_validator(mode="after")
    def _check_pattern(self) -> LocalRateWindowCreate:
        _check_time_pattern(self.months, self.days_of_week, self.start_time, self.end_time)
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class LocalRateWindowOut(LocalRateWindowCreate):
    id: int
    source_id: int

    model_config = {"from_attributes": True}


class BillingCycleCreate(DateRangeCheck):
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None = None
    cycle_months: int = Field(gt=0)
    anchor_day: int = Field(ge=1, le=31)


class BillingCycleOut(BillingCycleCreate):
    id: int
    site_id: int

    model_config = {"from_attributes": True}


class BillingCycleUpdate(BaseModel):
    effective_to: UtcDatetime


class CostOut(BaseModel):
    total_cost_c: decimal.Decimal
    total_import_kwh: decimal.Decimal
    total_export_kwh: decimal.Decimal
    minutes_missing_price: int


class BillingPeriodCostOut(CostOut):
    monthly_fee_discounts_c: decimal.Decimal


class BillingPeriodBoundsOut(BaseModel):
    start: UtcDatetime
    end: UtcDatetime


class CostSeriesPointOut(BaseModel):
    bucket_start: UtcDatetime
    cost_c: decimal.Decimal


class SourceConfigOut(BaseModel):
    config: dict


class CollectorSourceOut(BaseModel):
    source_id: int
    site_id: int
    config: dict


class DiscoveredSourceConfig(BaseModel):
    model_config = {"extra": "forbid"}

    serial_num: str | None = None
    cert_fingerprint: str | None = None
    cert_expires_at: AwareDatetime | None = None
    cert_serial: str | None = None
    token: str | None = None


class DemandChargeOut(BaseModel):
    id: int
    site_id: int
    billing_period_start: datetime.datetime
    billing_period_end: datetime.datetime
    peak_interval_start: datetime.datetime
    peak_avg_import_w: float
    rate_applied: decimal.Decimal
    charge_amount: decimal.Decimal
    is_estimated: bool

    model_config = {"from_attributes": True}
