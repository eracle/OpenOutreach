import factory
from faker import Faker

fake = Faker()


class LeadFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Lead"

    first_name = factory.LazyFunction(fake.first_name)
    last_name = factory.LazyFunction(fake.last_name)
    website = factory.LazyFunction(
        lambda: f"https://www.linkedin.com/in/{fake.user_name()}/"
    )
