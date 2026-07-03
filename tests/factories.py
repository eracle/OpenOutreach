# tests/factories.py
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


class LeadFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Lead"

    profile_url = factory.Sequence(lambda n: f"https://www.linkedin.com/in/lead-{n}/")


class DealFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Deal"

    lead = factory.SubFactory(LeadFactory)
