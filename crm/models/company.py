from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.models import Base1
from common.utils.helpers import token_default


class Company(Base1):
    class Meta:
        verbose_name = _("Company")
        verbose_name_plural = _("Companies")

    # -- Counterparty info (ex-BaseCounterparty) --
    address = models.TextField(blank=True, default='', verbose_name=_("Address"))
    region = models.CharField(max_length=100, blank=True, default='', verbose_name=_("Region/State"))
    district = models.CharField(max_length=100, blank=True, default='', verbose_name=_("District/County"))
    description = models.TextField(blank=True, default='', verbose_name=_("Description"))
    disqualified = models.BooleanField(default=False, verbose_name=_("Disqualified"))
    email = models.CharField(
        max_length=200, null=False, blank=False,
        verbose_name="Email",
        help_text=_("Use comma to separate Emails.")
    )
    lead_source = models.ForeignKey(
        'LeadSource', blank=True, null=True, on_delete=models.SET_NULL,
        verbose_name=_("Lead Source")
    )
    token = models.CharField(max_length=11, default=token_default, unique=True)
    was_in_touch = models.DateField(blank=True, null=True, verbose_name=_("Last contact date"))
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, blank=True, null=True, on_delete=models.CASCADE,
        verbose_name=_("Assigned to"),
        related_name="%(app_label)s_%(class)s_owner_related",
    )

    # -- Company-specific fields --
    full_name = models.CharField(
        max_length=200, null=False, blank=False,
        verbose_name=_("Company name")
    )
    alternative_names = models.CharField(
        max_length=100, default='', blank=True,
        verbose_name=_("Alternative names"),
        help_text=_("Separate them with commas.")
    )
    website = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name=_("Website")
    )
    active = models.BooleanField(default=True, verbose_name=_("Active"))
    phone = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name=_("Phone")
    )
    city_name = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name=_("City name")
    )
    registration_number = models.CharField(
        max_length=30, default='', blank=True,
        verbose_name=_("Registration number"),
        help_text=_("Registration number of Company")
    )

    def get_absolute_url(self):
        return reverse('admin:crm_company_change', args=(self.id,))

    def __str__(self):
        return self.full_name
