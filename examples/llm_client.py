import json
import re
import sys
import time

import httpx
import jsonschema

import os
from getpass import getpass

from openai import OpenAI

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

    task = input("Что сделать: ")

    # Convert the service's operation descriptions into tools for the LLM.
    tools = []

    for item in operations:
        schema = item["input_schema"].copy()
        schema.pop("$schema", None)

        tools.append({
            "type": "function",
            "name": item["name"],
            "description": item["description"],
            "parameters": schema,
            "strict": False,
        })

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        api_key = getpass("API-ключ OpenAI: ")

    with OpenAI(
        api_key=api_key,
        timeout=30,
        max_retries=0,
    ) as llm:
            decision = llm.responses.create(
                model="gpt-4.1-mini",
                instructions=(
                    "Выполняй задачу пользователя через доступные инструменты. "
                    "Выбери не более одного инструмента. "
                    "Если можешь заполнить обязательные параметры по запросу "
                    "пользователя, вызови подходящий инструмент. "
                    "Не запрашивай дополнительные предпочтения, "
                    "которые не требуются для вызова операции. "
                    "Учитывай описание операции и схему параметров. "
                    "Уточняй только тогда, когда обязательный параметр "
                    "невозможно определить без выдумывания. "
                    "Если подходящего инструмента нет, сообщи об этом."
                ),
                input=task,
                tools=tools,
                tool_choice="auto",
                parallel_tool_calls=False,
            )

    calls = [
        item
        for item in decision.output
        if item.type == "function_call"
    ]

    # The model may respond with text instead of a tool call.
    if not calls:
        print(decision.output_text or "Модель не выбрала опера операцию.")
        sys.exit(0)

    if len(calls) != 1:
        sys.exit("Ожидался один вызов операции")

    call = calls[0]

    # Allow only an operation from the published list.
    operation = next(
        (item for item in operations if item["name"] == call.name),
        None,
    )

    if operation is None:
        sys.exit("Модель выбрала неизвестную операцию")

    params = json.loads(call.arguments)

    print("Модель выбрала:", operation["name"])
    print(
        "Параметры:",
        json.dumps(params, ensure_ascii=False),
    )

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
