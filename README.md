# Payment Processing Service

Асинхронный сервис процессинга платежей: FastAPI, SQLAlchemy 2.0 (async), PostgreSQL, RabbitMQ (FastStream), Alembic, Docker.

## Запуск

```bash
docker compose up -d --build
```

| Сервис | Адрес |
|---|---|
| API (Swagger) | http://localhost:8000/docs |
| RabbitMQ Management | http://localhost:15672 (guest / guest) |
| Webhook mock (полученные webhook) | http://localhost:8080/webhooks |
| pgAdmin | http://localhost:5050 |

Остановка с удалением данных: `docker compose --profile load down -v`.

## API

Эндпоинты `/api/v1/payments` требуют заголовок `X-API-Key: dev-secret-api-key`. Пробы `/health`, `/ready` и документация `/docs`, `/openapi.json` открыты без ключа.

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "X-API-Key: dev-secret-api-key" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"amount": "1500.00", "currency": "RUB", "description": "Order #2",
       "metadata": {"order_id": 2}, "webhook_url": "http://webhook-mock:8080/webhooks"}'

curl http://localhost:8000/api/v1/payments/<id> -H "X-API-Key: dev-secret-api-key"
```

Создание отвечает `202 {"id", "status": "pending", "created_at"}` с заголовком `Location: /api/v1/payments/<id>`. Через 2–5 с статус меняется на `succeeded` или `failed`, webhook виден на http://localhost:8080/webhooks.

| Ответ | Когда |
|---|---|
| 401 | нет или неверный `X-API-Key` |
| 404 | платёж не найден |
| 409 | `Idempotency-Key` (UUID) уже использован с другим телом |
| 422 | невалидное тело или нет `Idempotency-Key` |

Повтор запроса с тем же `Idempotency-Key` и телом возвращает тот же платёж.

## Генератор платежей

Фоновый сервис `order-generator` в цикле создаёт платежи через API: случайные сумма и валюта, webhook на `webhook-mock`. Часть запросов повторяет предыдущий с тем же `Idempotency-Key` и телом, чтобы была видна идемпотентность.

```bash
make load                                   # запустить в фоне (compose profile load)
docker compose logs -f order-generator      # смотреть, что отправляется
make load-stop                              # остановить
```

Параметры задаются переменными окружения:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `ORDER_GENERATOR_RATE_PER_SECOND` | `2` | запросов в секунду |
| `ORDER_GENERATOR_TOTAL` | `0` | сколько платежей создать, `0` — без ограничения |
| `ORDER_GENERATOR_DUPLICATE_RATE` | `0.1` | доля повторов предыдущего запроса |

Например, `ORDER_GENERATOR_RATE_PER_SECOND=10 ORDER_GENERATOR_TOTAL=500 make load`. Без Docker генератор запускается командой `make generator` и берёт настройки из `.env`.

## Как устроено

```text
api → [payments + payments_outbox в одной транзакции] → outbox-relay → payments.new → consumer → webhook
                                                                            ↓ reject
                                                               payments.dlx → payments.dlq
```

- **Outbox.** `outbox-relay` забирает неопубликованные записи (`FOR UPDATE SKIP LOCKED`) и публикует с publisher confirms. Доставка at-least-once.
- **Consumer.** Эмулирует шлюз (2–5 с, 90% успеха), фиксирует статус, отправляет webhook. Шлюз вызывается только для `pending`-платежа, поэтому повторная доставка не списывает деньги дважды.
- **Retry.** 3 попытки с экспоненциальной паузой (2 и 4 с). Webhook-mock отвечает 500 в 30% случаев, чтобы retry было видно в логах `consumer`.
- **DLQ.** После 3 неудачных попыток или при невалидном сообщении — `reject` в `payments.dlq`. `x-delivery-limit=3` страхует от падения consumer посреди обработки.
- **Webhook.** Заголовок `X-Message-Id` стабилен между повторами для дедупликации на стороне получателя. Приватные адреса заблокированы (защита от SSRF), кроме хостов из `WEBHOOK_TRUSTED_HOSTS` (в compose — `webhook-mock`).

## Тесты

```bash
make install
make test
```

Нужен Docker: тесты используют Postgres из compose и поднимают RabbitMQ через testcontainers.
