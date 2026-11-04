import pytest
from pydantic import ValidationError

from core_api.actor_capabilities import (
    validate_action_against,
    validate_conditions_against,
)
from core_api.actor_schemas import (
    ActionSpec,
    Capabilities,
    ConditionSpec,
    PriceAboveTrigger,
    RetrySpec,
)
from core_api.errors import AppError

SITE_READINGS = {"outdoor_temp_c", "battery_soc_pct", "price_c_per_kwh"}


def make_capabilities() -> Capabilities:
    return Capabilities.model_validate(
        {
            "version": 1,
            "actions": [
                {
                    "id": "set_power",
                    "label": "Set power",
                    "restorable": True,
                    "params": [
                        {"name": "setpoint", "label": "Setpoint", "type": "number", "min": 16, "max": 30, "unit": "C"},
                        {
                            "name": "mode",
                            "label": "Mode",
                            "type": "choice",
                            "options": [{"value": "cool", "label": "Cool"}, {"value": "heat", "label": "Heat"}],
                        },
                        {
                            "name": "zones",
                            "label": "Zones",
                            "type": "multi_choice",
                            "options": [{"value": "z1", "label": "Living"}, {"value": "z2", "label": "Bed"}],
                        },
                    ],
                }
            ],
            "readings": [
                {"id": "indoor_temp_c", "label": "Indoor", "unit": "C", "type": "number"},
                {
                    "id": "zone_temp_c",
                    "label": "Zone temp",
                    "unit": "C",
                    "type": "number",
                    "target_options": [{"value": "z1", "label": "Living"}],
                },
            ],
        }
    )


def action(**params: object) -> ActionSpec:
    base: dict[str, object] = {"setpoint": 24, "mode": "cool", "zones": ["z1"]}
    base.update(params)
    return ActionSpec(id="set_power", params=base)  # type: ignore[arg-type]


def test_valid_action_accepted() -> None:
    validate_action_against(make_capabilities(), action())


def test_unknown_action_rejected() -> None:
    with pytest.raises(AppError) as exc:
        validate_action_against(make_capabilities(), ActionSpec(id="explode", params={}))
    assert exc.value.status_code == 422
    assert exc.value.code == "invalid_schedule"
    assert "explode" in exc.value.message


def test_number_param_outside_min_max_rejected() -> None:
    with pytest.raises(AppError, match="setpoint"):
        validate_action_against(make_capabilities(), action(setpoint=40))
    with pytest.raises(AppError, match="setpoint"):
        validate_action_against(make_capabilities(), action(setpoint=10))


def test_choice_param_not_in_options_rejected() -> None:
    with pytest.raises(AppError, match="mode"):
        validate_action_against(make_capabilities(), action(mode="dry"))


def test_multi_choice_requires_known_options() -> None:
    with pytest.raises(AppError, match="z9"):
        validate_action_against(make_capabilities(), action(zones=["z1", "z9"]))


def test_condition_reading_resolves_device_then_site_ids() -> None:
    caps = make_capabilities()
    device = ConditionSpec(reading="indoor_temp_c", op="gt", value=27)
    site = ConditionSpec(reading="outdoor_temp_c", op="gt", value=30)
    validate_conditions_against(caps, SITE_READINGS, [device, site])
    # outdoor_temp_c is also accepted when the device declares it
    caps.readings.append(caps.readings[0].model_copy(update={"id": "outdoor_temp_c"}))
    validate_conditions_against(caps, SITE_READINGS, [site])


def test_unknown_reading_rejected() -> None:
    cond = ConditionSpec(reading="humidity", op="lt", value=50)
    with pytest.raises(AppError, match="humidity"):
        validate_conditions_against(make_capabilities(), SITE_READINGS, [cond])


def test_zone_target_must_be_a_declared_target_option() -> None:
    caps = make_capabilities()
    ok = ConditionSpec(reading="zone_temp_c", op="gt", value=25, target="z1")
    validate_conditions_against(caps, SITE_READINGS, [ok])
    bad = ConditionSpec(reading="zone_temp_c", op="gt", value=25, target="z2")
    with pytest.raises(AppError, match="z2"):
        validate_conditions_against(caps, SITE_READINGS, [bad])
    no_target = ConditionSpec(reading="indoor_temp_c", op="gt", value=25, target="z1")
    with pytest.raises(AppError, match="target"):
        validate_conditions_against(caps, SITE_READINGS, [no_target])


def test_retry_floors() -> None:
    assert RetrySpec().max_attempts == 3
    with pytest.raises(ValidationError):
        RetrySpec(max_attempts=0)
    with pytest.raises(ValidationError):
        RetrySpec(interval_seconds=4)
    with pytest.raises(ValidationError):
        RetrySpec(give_up_minutes=0)
    RetrySpec(max_attempts=1, interval_seconds=5, give_up_minutes=1)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_number_param_rejected(bad: float) -> None:
    # model_construct skips model validation so the validator's own check is exercised
    spec = ActionSpec.model_construct(id="set_power", params={"setpoint": bad, "mode": "cool", "zones": ["z1"]})
    with pytest.raises(AppError, match="setpoint"):
        validate_action_against(make_capabilities(), spec)
    unbounded = Capabilities.model_validate(
        {
            "version": 1,
            "actions": [
                {"id": "a", "label": "A", "params": [{"name": "level", "label": "Level", "type": "number"}]}
            ],
        }
    )
    with pytest.raises(AppError, match="level"):
        validate_action_against(unbounded, ActionSpec.model_construct(id="a", params={"level": bad}))


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_floats_rejected_by_models(bad: float) -> None:
    with pytest.raises(ValidationError):
        ConditionSpec(reading="indoor_temp_c", op="gt", value=bad)
    with pytest.raises(ValidationError):
        PriceAboveTrigger(type="price_above", c_per_kwh=bad)
    with pytest.raises(ValidationError):
        ActionSpec(id="a", params={"x": bad})
