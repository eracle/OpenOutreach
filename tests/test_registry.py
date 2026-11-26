"""Both children's apps are in one registry, on one database.

This is the whole premise of the project, so it is the one thing worth asserting outright:
if the labels ever collide, or a child stops being installable beside its sibling, every
other test here would fail in some less legible way.
"""
from django.apps import apps


def test_both_children_are_installed_under_namespaced_labels():
    labels = {config.label for config in apps.get_app_configs()}
    assert {"outfind_core", "outfind_crm"} <= labels
    assert {"outsend_core", "outsend_leads", "outsend_emails"} <= labels
