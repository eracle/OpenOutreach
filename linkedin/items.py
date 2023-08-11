import scrapy


class LinkedinUser(scrapy.Item):
    name = scrapy.Field()
    title = scrapy.Field()
    company = scrapy.Field()
    link = scrapy.Field()
    connection_msg_sent = scrapy.Field()
