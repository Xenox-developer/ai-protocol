# AI Protocol

Экспериментальный HTTP-протокол взаимодействия сервисов и AI-агентов.
Rust-сервер публикует доступные операции и лимит параллелизма,
приоритетно обслуживает запросы людей и передаёт операции Python-каталогу.
Агентные клиенты получают описание операций через `/agent-policy`,
проверяют параметры по JSON Schema и обрабатывают `429` с `Retry-After`.

Это локальный MVP, а не готовый публичный стандарт.

## Структура

```text
ai-protocol/
├── README.md
├── LICENSE
├── .gitignore
├── Cargo.toml
├── Cargo.lock
├── requirements.txt
├── src/
│   ├── main.rs
│   └── bin/
│       ├── load.rs
│       └── load_plain.rs
├── examples/
│   ├── catalog_service.py
│   ├── discover_client.py
│   └── llm_client.py
├── docs/
│   └── protocol.md
└── benchmarks/
    ├── README.md
    └── results/
```

## Запуск

Нужны Rust/Cargo с поддержкой edition 2024 и Python 3.10+.
Все команды выполняются из корня репозитория.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cargo build --locked --bins
```

В первом терминале запустите каталог:

```sh
.venv/bin/python examples/catalog_service.py
```

Во втором терминале запустите сервер:

```sh
cargo run --locked --bin ai-protocol
```

Каталог слушает `127.0.0.1:4000`, сервер — `127.0.0.1:3000`.
В третьем терминале можно запустить интерактивное обнаружение операций:

```sh
.venv/bin/python examples/discover_client.py
```

Выберите операцию `1` и введите `{"query":"boots"}` либо операцию `2`
и `{"id":1}`. Клиент покажет JSON-ответ каталога.

Для выбора операции языковой моделью:

```sh
.venv/bin/python examples/llm_client.py
```

Клиент использует `OPENAI_API_KEY`, если переменная задана, иначе запрашивает
ключ скрытым вводом. Пример задачи: `Найди все ботинки`.
В текущем коде используется `gpt-4.1-mini`; клиент выполняет не более одной
операции и печатает её JSON-результат. Вызов модели требует доступа к OpenAI API.

## HTTP API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/agent-policy` | Версия `2`, `max_in_flight`, описания операций |
| POST | `/search` | Поиск по подстроке: `{"query":"boots"}` |
| POST | `/product` | Получение товара: `{"id":1}` |

```sh
curl http://127.0.0.1:3000/agent-policy
curl -X POST http://127.0.0.1:3000/search \
  -H 'Content-Type: application/json' \
  -H 'X-Client-Type: agent' \
  -d '{"query":"boots"}'
```

Для проверки повтора запустите сервер с `TEST_429=1`:

```sh
TEST_429=1 cargo run --locked --bin ai-protocol
```

Первый агентный запрос получит `429` и `Retry-After: 2` до выполнения операции.
Уже запущенный сервер нужно остановить перед запуском второго на том же порту.

Подробности: [контракт протокола](docs/protocol.md).
Нагрузочные клиенты и сохранённые замеры: [benchmarks](benchmarks/README.md).

## Ограничения

Очереди хранятся в памяти. `X-Client-Type` — добровольная метка клиента,
а не аутентификация. Человеческая очередь не ограничена; строгий приоритет
может задерживать агентов при постоянной человеческой нагрузке.
Лимит клиента не является глобальной квотой сервера.

## Права

Copyright (c) 2026 Nikita Chindin. Лицензия [Apache-2.0](LICENSE).
