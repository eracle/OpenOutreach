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


def test_neither_child_keeps_configuration_of_its_own():
    """One config model, and it is this host's.

    Both children read `OPENOUTFIND_*` / `OUTSEND_*` fresh on every run, so a config
    table appearing on either side again would mean two places remembering the same
    answer — the drift the export exists to make impossible.
    """
    labels = {
        (config.label, model.__name__.lower())
        for config in apps.get_app_configs()
        for model in config.get_models()
    }
    config_models = {(label, name) for label, name in labels if name == "siteconfig"}

    assert config_models == {("openoutreach_config", "siteconfig")}


def test_one_database_holds_the_config_and_both_pipelines(db):
    from cold_outreach.emails.models import Mailbox
    from openoutfind.crm.models import Lead

    from openoutreach.config.models import SiteConfig

    tables = connection.introspection.table_names()
    assert SiteConfig._meta.db_table in tables
    assert Lead._meta.db_table in tables
    assert Mailbox._meta.db_table in tables
