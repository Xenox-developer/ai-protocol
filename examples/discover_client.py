import json
import re
import sys
import time

import httpx
import jsonschema


# The client knows the service URL and discovery endpoint in advance.
BASE_URL = "http://127.0.0.1:3000"

with httpx.Client(
    trust_env=False,
    timeout=30,
    follow_redirects=False,
) as client:
    response = client.get(BASE_URL + "/agent-policy")
    response.raise_for_status()
    policy = response.json()

    if type(policy.get("version")) is not int or policy["version"] != 2:
        sys.exit("Неподдерживаемая версия протокола")

    limit = policy.get("max_in_flight")
    if type(limit) is not int or limit < 1:
        sys.exit("Некорректный лимит")

    operations = policy.get("operations")
    if not isinstance(operations, list) or not operations:
        sys.exit("Сервис не предоставил список операций")

    print("Доступные операции:")

    for number, operation in enumerate(operations, start=1):
        print(
            f"{number}. {operation['name']} — "
            f"{operation['description']}"
        )

    number = int(input("Номер операции: "))
    if not 1 <= number <= len(operations):
        sys.exit("Нет такой операции")

    operation = operations[number - 1]

    print("Схема параметров:")
    print(json.dumps(operation["input_schema"], ensure_ascii=False, indent=2))

    params = json.loads(input("Параметры в формате JSON: "))

    # Validate the parameters before sending the request.
    jsonschema.validate(
        instance=params,
        schema=operation["input_schema"],
    )

    method = operation["method"]
    path = operation["path"]

    # This prototype supports POST operations with a JSON body.
    if method != "POST":
        sys.exit("Этот клиент пока поддерживает только POST")

    # For this demo, allow only simple paths within the same service.
    if not re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", path):
        sys.exit("Неподдерживаемый путь операции")

    for attempt in range(1, 6):
        print(f"Отправляем {method} {path}, попытка {attempt}")

        response = client.request(
            method,
            BASE_URL + path,
            headers={"X-Client-Type": "agent"},
            json=params,
        )

        if response.status_code == 429 and attempt < 5:
            raw = response.headers.get("Retry-After", "1").strip()
            seconds = int(raw) if raw.isascii() and raw.isdigit() else 1

            print(f"Сервис просит подождать {seconds} сек.")
            time.sleep(seconds)
            continue

        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        break
