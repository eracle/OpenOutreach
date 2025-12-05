import factory
from faker import Faker

from linkedin.db.models import Profile

fake = Faker()


class ProfileFactory(factory.Factory):
    class Meta:
        model = Profile

    linkedin_url = factory.LazyFunction(lambda: fake.url())
    public_id = factory.LazyFunction(lambda: fake.user_name())
    profile_id = factory.LazyFunction(lambda: str(fake.random_int(min=100000, max=999999)))
    first_name = factory.LazyFunction(lambda: fake.first_name())
    last_name = factory.LazyFunction(lambda: fake.last_name())
    headline = factory.LazyFunction(lambda: fake.job())
    summary = factory.LazyFunction(lambda: fake.text())
    country = factory.LazyFunction(lambda: fake.country())
    city = factory.LazyFunction(lambda: fake.city())
    experience = factory.LazyFunction(lambda: [
        {"title": "Software Engineer", "company": "Google", "starts_at": "2020-01-01", "ends_at": "2023-12-31"}
    ])
    education = factory.LazyFunction(lambda: [
        {"degree": "B.Sc. Computer Science", "field_of_study": "Computer Science", "school": "University of Example"}
    ])
    skills = factory.LazyFunction(lambda: ["Python", "SQL", "Cloud"])
