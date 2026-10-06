-include .env

.PHONY: install db broker webhook pgadmin infra db-upgrade db-downgrade db-revision api consumer relay generator \
	docker-compose-up demo-start load load-stop docker-compose-down docker-compose-down-clean lint-format test

.env:
	cp .env.example .env

install: .env
	uv sync
	uv run pre-commit install

db:
	docker compose up -d --wait postgres

broker:
	docker compose up -d --wait rabbitmq

webhook:
	docker compose up -d --wait --build webhook-mock

pgadmin:
	docker compose up -d --wait pgadmin

infra:
	docker compose up -d --wait --build postgres rabbitmq webhook-mock pgadmin

db-upgrade: .env db
	uv run alembic upgrade $(if $(steps),+$(steps),head)

db-downgrade: .env db
	uv run alembic downgrade $(if $(steps),-$(steps),base)

db-revision: .env db
	uv run alembic revision --autogenerate $(if $(m),-m "$(m)")

api: .env
	uv run uvicorn src.main:api --reload

consumer: .env
	uv run faststream run src.main:consumer_app

relay: .env
	uv run python -m src.main

generator: .env
	uv run python -m order_generator.main

docker-compose-up:
	docker compose up -d --build

demo-start: docker-compose-up
	@echo "API (Swagger):        http://localhost:8000/docs"
	@echo "RabbitMQ Management:  http://localhost:15672 (guest / guest)"
	@echo "pgAdmin:              http://localhost:5050"
	@echo "Webhook mock:         http://localhost:8080"

load:
	docker compose --profile load up -d --build order-generator
	@echo "Logs: docker compose logs -f order-generator"

load-stop:
	docker compose --profile load stop order-generator

docker-compose-down:
	docker compose --profile load down

docker-compose-down-clean:
	docker compose --profile load down --volumes --remove-orphans

lint-format:
	uv run ruff check --fix --exit-non-zero-on-fix .
	uv run ruff format --exit-non-zero-on-format .
	uv run mypy
	uv run vulture

test: .env db
	uv run pytest $(if $(PYTEST_WORKERS),-n $(PYTEST_WORKERS) --dist loadgroup)
