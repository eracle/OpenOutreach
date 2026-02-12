# tests/factories.py
import uuid

import factory
from django.contrib.auth.models import User
from faker import Faker

fake = Faker()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.LazyFunction(fake.user_name)
    is_staff = True
    is_active = True


class CompanyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Company"

    full_name = factory.LazyFunction(fake.company)
    email = ""


class LeadFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Lead"

    first_name = factory.LazyFunction(fake.first_name)
    last_name = factory.LazyFunction(fake.last_name)
    website = factory.LazyFunction(
        lambda: f"https://www.linkedin.com/in/{fake.user_name()}/"
    )


class ContactFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Contact"

    first_name = factory.LazyFunction(fake.first_name)
    last_name = factory.LazyFunction(fake.last_name)
    company = factory.SubFactory(CompanyFactory)
    email = ""


class DealFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Deal"

    name = factory.LazyFunction(lambda: f"LinkedIn: {fake.user_name()}")
    lead = factory.SubFactory(LeadFactory)
    next_step = ""
    next_step_date = factory.LazyFunction(lambda: __import__("datetime").date.today())
    ticket = factory.LazyFunction(lambda: uuid.uuid4().hex[:16])
