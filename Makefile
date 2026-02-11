.DEFAULT_GOAL := help

help:
	@perl -nle'print $& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

attach: ## follow the logs of the service
	docker compose -f local.yml logs -f

test: ## run the test suite
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

install: ## install all Python dependencies (local dev)
	pip install uv 2>/dev/null || true
	uv pip install --no-deps -r requirements/crm.txt
	uv pip install -r requirements/local.txt

setup: install ## install deps + migrate + bootstrap CRM
	python manage_crm.py migrate --no-input
	python -m linkedin.management.setup_crm

run: ## run the scraper (optionally: make run HANDLE=myhandle)
	python -m main $(if $(HANDLE),$(HANDLE),)

admin: ## start the Django Admin web server
	@echo ""
	@echo "  Django Admin: http://localhost:8000/admin/"
	@echo "  CRM UI:       http://localhost:8000/crm/"
	@echo "  No superuser yet? Run: python manage_crm.py createsuperuser"
	@echo ""
	python manage_crm.py runserver

analytics: ## run dbt models (build analytics tables)
	cd analytics && dbt run

analytics-test: ## run dbt schema tests
	cd analytics && dbt test

view: ## open vinagre to view the app
	@sh -c 'vinagre vnc://127.0.0.1:5900 > /dev/null 2>&1 &'

