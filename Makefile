.DEFAULT_GOAL := help

help:
	@perl -nle'print $& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

attach: ## follow the logs of the 'scrapy' service
	docker compose logs -f

stop: ## stop all services defined in Docker Compose
	docker compose stop

build: ## build all services defined in Docker Compose
	docker compose build

