from __future__ import annotations

import datetime

from core_api.alerts import clear_alert, set_alert
from core_api.models.alerts import SUBJECT_SITE, Alert


def _raise(db, site, message="m", **kwargs) -> None:
    set_alert(db, site.id, SUBJECT_SITE, site.id, "code", message, **kwargs)


def _clear(db, site) -> None:
    clear_alert(db, SUBJECT_SITE, site.id, "code")


def _row(db) -> Alert:
    db.expire_all()
    return db.query(Alert).one()


def _age(a: datetime.datetime) -> float:
    if a.tzinfo is None:
        a = a.replace(tzinfo=datetime.UTC)
    return (datetime.datetime.now(datetime.UTC) - a).total_seconds()


def test_raise_inserts_with_since_now(db, site):
    _raise(db, site)
    row = _row(db)
    assert abs(_age(row.since)) < 5
    assert (row.severity, row.sticky, row.cleared_at) == ("failure", False, None)


def test_raise_unresolved_keeps_since_and_updates_message_and_severity(db, site):
    _raise(db, site, "first")
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    db.query(Alert).update({Alert.since: old})
    db.commit()
    _raise(db, site, "second", severity="partial")
    row = _row(db)
    assert _age(row.since) > 3500
    assert (row.message, row.severity, row.cleared_at) == ("second", "partial", None)


def test_raise_after_clear_replaces_the_resolved_row(db, site):
    _raise(db, site)
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    db.query(Alert).update({Alert.since: old})
    db.commit()
    _clear(db, site)
    _raise(db, site)
    row = _row(db)
    assert row.cleared_at is None
    assert _age(row.since) < 1


def test_clear_sets_cleared_at_once(db, site):
    _raise(db, site)
    _clear(db, site)
    first = _row(db).cleared_at
    assert first is not None
    _clear(db, site)
    assert _row(db).cleared_at == first


def test_clear_leaves_sticky_rows(db, site):
    _raise(db, site, sticky=True)
    _clear(db, site)
    assert _row(db).cleared_at is None


def test_sticky_raise_replaces_existing_row(db, site):
    _raise(db, site, "one", sticky=True)
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    db.query(Alert).update({Alert.since: old})
    db.commit()
    _raise(db, site, "two", sticky=True)
    row = _row(db)
    assert row.message == "two"
    assert _age(row.since) < 5


def test_clear_without_a_row_does_nothing(db, site):
    _clear(db, site)
    assert db.query(Alert).count() == 0
