.DEFAULT_GOAL := help
.PHONY: help logs test stop build up install setup run find send status

help:
	@perl -nle'print $& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

install: ## install the package and dev dependencies (editable)
	pip install uv 2>/dev/null || true
	uv pip install -e ".[dev]"

setup: install ## install + migrate both children's apps into one database
	python manage.py migrate --no-input

run: ## onboard if needed, find leads with an address, then send: make run N=5
	python manage.py run $(N)

find: ## find leads: make find N=10 [UNIT=emails]
	python manage.py find $(or $(N),1) $(UNIT)

send: ## mail what is already stored: make send [N=5]
	python manage.py send $(N)

status: ## what is configured, blocked and counted
	python manage.py status

test: ## run the test suite
	pytest

# Docker targets — the server deploy only (docs/infrastructure.md §7).
# Development and tests run natively; there is no docker-test.
logs: ## follow the logs of the service
	docker compose -f local.yml logs -f

stop: ## stop all services defined in Docker Compose
	docker compose -f local.yml stop

build: ## build all services defined in Docker Compose
	docker compose -f local.yml build

up: ## run the defined service in Docker Compose
	docker compose -f local.yml up --build -d
	docker compose -f local.yml logs -f
