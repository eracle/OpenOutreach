"""Both children's apps are in one registry, on one database.

This is the whole premise of the project, so it is the one thing worth asserting outright:
if the labels ever collide again, or a child stops being installable beside its sibling,
every other test here would fail in some less legible way.
"""
from django.apps import apps
from django.db import connection


def test_both_children_are_installed_under_namespaced_labels():
    labels = {config.label for config in apps.get_app_configs()}
    assert {"outfind_core", "outfind_crm"} <= labels
    assert {"outsend_core", "outsend_leads", "outsend_emails"} <= labels


def test_each_child_keeps_its_own_siteconfig():
    """Two singletons, two tables — the wizard writes each through its own model."""
    from cold_outreach.core.models import SiteConfig as SenderConfig
    from openoutfind.core.models import SiteConfig as FinderConfig

    assert FinderConfig._meta.db_table != SenderConfig._meta.db_table


def test_one_database_holds_both(db):
    from cold_outreach.core.models import SiteConfig as SenderConfig
    from openoutfind.core.models import SiteConfig as FinderConfig

    tables = connection.introspection.table_names()
    assert FinderConfig._meta.db_table in tables
    assert SenderConfig._meta.db_table in tables
