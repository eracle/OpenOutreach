.DEFAULT_GOAL := help

help:
	@perl -nle'print $& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

view: ## view the Selenium browser's activity
	vinagre localhost:5900

companies: build ## run the 'companies' Scrapy spider
	docker-compose up scrapy_companies

random: build ## run the 'random' Scrapy spider
	docker-compose up scrapy_random

byname: build ## run the 'byname' Scrapy spider
	docker-compose up scrapy_byname

test: ## run Pytest on the 'tests/*' directory
	docker-compose up scrapy_test

attach: ## follow the logs of the 'scrapy' service
	docker-compose logs -f

stop: ## stop all services defined in Docker Compose
	docker-compose stop

build: ## build all services defined in Docker Compose
	docker-compose build

