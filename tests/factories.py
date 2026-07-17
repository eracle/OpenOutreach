# tests/factories.py
import factory
import numpy as np
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

    class Params:
        # ``LeadFactory(embedded=True)`` — a lead the ranking legs can score. The
        # vector's contents are irrelevant to them; only its presence is, so an
        # un-embedded lead stays the default (several tests rely on it).
        embedded = factory.Trait(
            embedding=factory.LazyFunction(
                lambda: np.ones(384, dtype=np.float32).tobytes()
            ),
        )


class DealFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Deal"

    lead = factory.SubFactory(LeadFactory)
