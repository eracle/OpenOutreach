import factory
from factory.fuzzy import FuzzyText, FuzzyInteger
from linkedin.models import Profile, Company
from faker import Faker

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

class CompanyFactory(factory.Factory):
    class Meta:
        model = Company

    linkedin_url = factory.LazyFunction(lambda: fake.url())
    name = factory.LazyFunction(lambda: fake.company())
    tagline = factory.LazyFunction(lambda: fake.bs())
    about = factory.LazyFunction(lambda: fake.text())
    website = factory.LazyFunction(lambda: fake.url())
    industry = factory.LazyFunction(lambda: fake.word())
    company_size = factory.LazyFunction(lambda: "1001-5000 employees")
    headquarters = factory.LazyFunction(lambda: {"city": fake.city(), "country": fake.country()})
