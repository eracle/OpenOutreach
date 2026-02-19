.DEFAULT_GOAL := help
.PHONY: help attach test docker-test stop build up up-view setup run admin analytics analytics-test view

help:
	@perl -nle'print $& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

setup: ## install deps + Playwright browsers + migrate + bootstrap CRM
	uv sync --group dev
	uv run playwright install --with-deps chromium
	uv run python manage.py migrate --no-input
	uv run python manage.py setup_crm

run: ## run the daemon
	uv run python manage.py

test: ## run the test suite
	uv run pytest

admin: ## start the Django Admin web server
	@echo ""
	@echo "  Django Admin: http://localhost:8000/admin/"
	@echo "  CRM UI:       http://localhost:8000/crm/"
	@echo "  No superuser yet? Run: uv run python manage.py createsuperuser"
	@echo ""
	uv run python manage.py runserver

analytics: ## run dbt models (build analytics tables)
	cd analytics && uv run dbt run

analytics-test: ## run dbt schema tests
	cd analytics && uv run dbt test

# Docker targets
attach: ## follow the logs of the service
	docker compose -f local.yml logs -f

docker-test: ## run tests in Docker
	docker compose -f local.yml run --remove-orphans app py.test -vv --cache-clear

stop: ## stop all services defined in Docker Compose
	docker compose -f local.yml stop

build: ## build all services defined in Docker Compose
	docker compose -f local.yml build

up: ## run the defined service in Docker Compose
	docker compose -f local.yml up --build

up-view: ## run the defined service in Docker Compose and open vinagre
	docker compose -f local.yml up --build -d
	sleep 3
	$(MAKE) view
	docker compose -f local.yml logs -f app

view: ## open vinagre to view the app
	@sh -c 'vinagre vnc://127.0.0.1:5900 > /dev/null 2>&1 &'
