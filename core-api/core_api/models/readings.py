from __future__ import annotations

import datetime
import enum

from sqlalchemy import DateTime, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from core_api.models.base import Base, enum_check, str_enum


class EnergyMetric(str, enum.Enum):
    PRODUCTION_W = "production_w"
    CONSUMPTION_W = "consumption_w"
    GRID_IMPORT_W = "grid_import_w"
    GRID_EXPORT_W = "grid_export_w"
    BATTERY_CHARGE_W = "battery_charge_w"
    BATTERY_DISCHARGE_W = "battery_discharge_w"
    BATTERY_SOC_PCT = "battery_soc_pct"
    PRODUCTION_WH_LIFETIME = "production_wh_lifetime"
    GRID_IMPORT_WH_LIFETIME = "grid_import_wh_lifetime"
    GRID_EXPORT_WH_LIFETIME = "grid_export_wh_lifetime"
    GRID_VOLTAGE_PH_A_V = "grid_voltage_ph_a_v"
    GRID_VOLTAGE_PH_B_V = "grid_voltage_ph_b_v"
    GRID_VOLTAGE_PH_C_V = "grid_voltage_ph_c_v"
    GRID_RELAY_STATE = "grid_relay_state"


class WeatherMetric(str, enum.Enum):
    TEMP_C = "temp_c"
    HUMIDITY_PCT = "humidity_pct"
    WIND_SPEED_KPH = "wind_speed_kph"
    WIND_DIRECTION_DEG = "wind_direction_deg"
    RAIN_MM = "rain_mm"
    SOLAR_RADIATION_WM2 = "solar_radiation_wm2"
    BAROMETRIC_PRESSURE_HPA = "barometric_pressure_hpa"


class AggregationMethod(str, enum.Enum):
    AVERAGE = "average"
    LAST_VALUE = "last_value"
    CIRCULAR_MEAN = "circular_mean"


# Single lookup the rollup job (and any future consumer) reads instead of
# branching per metric — see 'Retention & rollup' in the design spec.
ENERGY_METRIC_AGGREGATION: dict[EnergyMetric, AggregationMethod] = {
    EnergyMetric.PRODUCTION_W: AggregationMethod.AVERAGE,
    EnergyMetric.CONSUMPTION_W: AggregationMethod.AVERAGE,
    EnergyMetric.GRID_IMPORT_W: AggregationMethod.AVERAGE,
    EnergyMetric.GRID_EXPORT_W: AggregationMethod.AVERAGE,
    EnergyMetric.BATTERY_CHARGE_W: AggregationMethod.AVERAGE,
    EnergyMetric.BATTERY_DISCHARGE_W: AggregationMethod.AVERAGE,
    EnergyMetric.BATTERY_SOC_PCT: AggregationMethod.LAST_VALUE,
    # Lifetime counters and the relay state are cumulative or discrete, so the
    # last value in the bucket is the right one; voltages are continuous.
    EnergyMetric.PRODUCTION_WH_LIFETIME: AggregationMethod.LAST_VALUE,
    EnergyMetric.GRID_IMPORT_WH_LIFETIME: AggregationMethod.LAST_VALUE,
    EnergyMetric.GRID_EXPORT_WH_LIFETIME: AggregationMethod.LAST_VALUE,
    EnergyMetric.GRID_VOLTAGE_PH_A_V: AggregationMethod.AVERAGE,
    EnergyMetric.GRID_VOLTAGE_PH_B_V: AggregationMethod.AVERAGE,
    EnergyMetric.GRID_VOLTAGE_PH_C_V: AggregationMethod.AVERAGE,
    EnergyMetric.GRID_RELAY_STATE: AggregationMethod.LAST_VALUE,
}

# Weather metrics are continuous sensor values, so they average — except
# WIND_DIRECTION_DEG, a circular quantity where an ordinary mean is
# physically wrong (350° and 10° average to 180°, due south, instead of due
# north). It takes a circular mean, which the rollup job implements.
WEATHER_METRIC_AGGREGATION: dict[WeatherMetric, AggregationMethod] = {
    metric: AggregationMethod.AVERAGE for metric in WeatherMetric
} | {WeatherMetric.WIND_DIRECTION_DEG: AggregationMethod.CIRCULAR_MEAN}


class _EnergyReadingColumns:
    """Narrow/long shape shared by EnergyReading and its 5-min rollup twin —
    same columns, only __tablename__ (and the rollup's coarser ts grain)
    differ, so both raw and rollup are built from this one mixin."""

    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    metric: Mapped[EnergyMetric] = mapped_column(str_enum(EnergyMetric, length=32), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)


class EnergyReading(_EnergyReadingColumns, Base):
    __tablename__ = "energy_readings"
    __table_args__ = (enum_check("metric", EnergyMetric, "ck_energy_readings_metric"),)


class EnergyReadingRollup5m(_EnergyReadingColumns, Base):
    __tablename__ = "energy_readings_rollup_5m"
    __table_args__ = (enum_check("metric", EnergyMetric, "ck_energy_readings_rollup_5m_metric"),)


class _WeatherReadingColumns:
    """Narrow/long shape shared by WeatherReading and its 5-min rollup twin —
    see _EnergyReadingColumns above for the same rationale."""

    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    metric: Mapped[WeatherMetric] = mapped_column(str_enum(WeatherMetric, length=32), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)


class WeatherReading(_WeatherReadingColumns, Base):
    __tablename__ = "weather_readings"
    __table_args__ = (enum_check("metric", WeatherMetric, "ck_weather_readings_metric"),)


class WeatherReadingRollup5m(_WeatherReadingColumns, Base):
    __tablename__ = "weather_readings_rollup_5m"
    __table_args__ = (enum_check("metric", WeatherMetric, "ck_weather_readings_rollup_5m_metric"),)
