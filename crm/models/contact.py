from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.models import Base1
from common.utils.helpers import token_default


class Contact(Base1):
    class Meta:
        verbose_name = _("Contact person")
        verbose_name_plural = _("Contact persons")

    # -- Contact info (ex-BaseContact) --
    first_name = models.CharField(
        max_length=100, null=False, blank=False,
        help_text=_("The name of the contact person (one word)."),
        verbose_name=_("First name"),
    )
    middle_name = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name=_("Middle name"),
        help_text=_("The middle name of the contact person.")
    )
    last_name = models.CharField(
        max_length=100, blank=True, default='',
        help_text=_("The last name of the contact person (one word)."),
        verbose_name=_("Last name"),
    )
    title = models.CharField(
        max_length=100, null=True, blank=True,
        help_text=_("The title (position) of the contact person."),
        verbose_name=_("Title / Position"),
    )
    sex = models.CharField(
        null=True, blank=True,
        max_length=1, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')], default='M',
        verbose_name=_("Sex"),
    )
    birth_date = models.DateField(
        blank=True, null=True,
        verbose_name=_("Date of Birth")
    )
    secondary_email = models.EmailField(
        blank=True, default='',
        verbose_name=_("Secondary email")
    )
    phone = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name=_("Phone")
    )
    other_phone = models.CharField(max_length=100, blank=True, default='')
    mobile = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name=_("Mobile phone")
    )
    city_name = models.CharField(
        max_length=50, blank=True, default='',
        verbose_name=_("City")
    )

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

    # -- Contact-specific fields --
    company = models.ForeignKey(
        'Company', blank=False, null=False, on_delete=models.CASCADE,
        related_name="contacts",
        verbose_name=_("Company of contact")
    )

    def __str__(self):
        return f"{self.first_name} {self.last_name}, {self.company}"

    def get_absolute_url(self):
        return reverse('admin:crm_contact_change', args=(self.id,))
