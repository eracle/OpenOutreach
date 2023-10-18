import scrapy


class LinkedinUser(scrapy.Item):
    lastName = scrapy.Field()
    firstName = scrapy.Field()
    locale = scrapy.Field()
    headline = scrapy.Field()
    linkedinUrl = scrapy.Field()
    # summary = scrapy.Field()
    connection_msg = scrapy.Field()
    email_address = scrapy.Field()
    phone_numbers = scrapy.Field()
    education = scrapy.Field()
    experience = scrapy.Field()
    industryName = scrapy.Field()
    geoLocationName = scrapy.Field()
