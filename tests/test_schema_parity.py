"""Django owns the schema; the SQLAlchemy models must describe the same tables.

If a Django model changes without the SQLAlchemy mapping being updated (or
vice versa), these tests fail before the mismatch can reach a database.
"""
import pytest
from django.apps import apps as django_apps

from fastapi_app.models import Base

TABLES = ["vendors", "purchase_orders", "invoices", "upload_batches", "upload_records"]


def django_model_for(table: str):
    return next(m for m in django_apps.get_models() if m._meta.db_table == table)


def test_every_platform_table_is_mapped_by_both_orms():
    django_tables = {
        m._meta.db_table for m in django_apps.get_models()
        if m._meta.app_label in {"vendors", "purchase_orders", "invoices", "processing"}
    }
    assert django_tables == set(TABLES) == set(Base.metadata.tables)


@pytest.mark.parametrize("table", TABLES)
def test_columns_and_nullability_match(table):
    django_columns = {
        field.column: field.null for field in django_model_for(table)._meta.concrete_fields
    }
    sqlalchemy_columns = {
        column.name: bool(column.nullable) for column in Base.metadata.tables[table].columns
    }
    assert sqlalchemy_columns == django_columns


@pytest.mark.parametrize("table", TABLES)
def test_string_lengths_match(table):
    django_lengths = {
        field.column: field.max_length
        for field in django_model_for(table)._meta.concrete_fields
        if getattr(field, "max_length", None)
    }
    sqlalchemy_lengths = {
        column.name: column.type.length
        for column in Base.metadata.tables[table].columns
        if getattr(column.type, "length", None)
    }
    assert sqlalchemy_lengths == django_lengths


@pytest.mark.parametrize("table", TABLES)
def test_unique_columns_match(table):
    model = django_model_for(table)
    django_unique = {(f.column,) for f in model._meta.concrete_fields
                     if f.unique and not f.primary_key}
    django_unique |= {
        tuple(model._meta.get_field(name).column for name in constraint.fields)
        for constraint in model._meta.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    sa_table = Base.metadata.tables[table]
    sqlalchemy_unique = {(c.name,) for c in sa_table.columns if c.unique}
    sqlalchemy_unique |= {
        tuple(c.name for c in constraint.columns)
        for constraint in sa_table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert sqlalchemy_unique == django_unique
