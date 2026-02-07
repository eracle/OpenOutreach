import factory
from faker import Faker

from linkedin.db.models import Profile
from linkedin.navigation.enums import ProfileState

fake = Faker()


class ProfileFactory(factory.Factory):
    class Meta:
        model = Profile

    public_identifier = factory.LazyFunction(lambda: fake.user_name())
    profile = factory.LazyFunction(lambda: {
        "first_name": fake.first_name(),
        "last_name": fake.last_name(),
        "full_name": fake.name(),
        "headline": fake.job(),
        "summary": fake.text(),
        "url": f"https://www.linkedin.com/in/{fake.user_name()}/",
    })
    data = factory.LazyFunction(lambda: {"included": [], "data": {}})
    cloud_synced = False
    state = ProfileState.DISCOVERED.value
