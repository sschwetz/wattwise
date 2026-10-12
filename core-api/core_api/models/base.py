from __future__ import annotations

import enum

from sqlalchemy import CheckConstraint, Enum
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def str_enum(enum_cls: type[enum.Enum], length: int) -> Enum:
    """Enum column storing each member's lowercase .value (e.g. "weather"),
    not its uppercase Python name — SQLAlchemy's default is the name, which
    would silently mismatch any raw-SQL predicate (e.g. a partial index's
    WHERE clause) written against the spec's documented enum values.

    create_constraint is deliberately False: pair this with enum_check() in
    each table's __table_args__ instead. Enum(create_constraint=True) makes
    Alembic autogenerate render the same CheckConstraint twice on every
    `revision --autogenerate` (a known SQLAlchemy/Alembic interaction) — on
    Postgres a duplicate constraint name is a hard error, not a cosmetic one.
    An explicit, once-declared constraint has no such failure mode.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        validate_strings=True,
        length=length,
        values_callable=lambda obj: [e.value for e in obj],
        create_constraint=False,
    )


def enum_check(column: str, enum_cls: type[enum.Enum], name: str) -> CheckConstraint:
    """DB-level CHECK enforcing `column` is one of enum_cls's values — the
    explicit counterpart to a str_enum() column, added once to __table_args__
    per table (see str_enum's docstring for why it isn't the type's own
    create_constraint=True instead)."""
    values = ", ".join(repr(member.value) for member in enum_cls)
    return CheckConstraint(f"{column} IN ({values})", name=name)
