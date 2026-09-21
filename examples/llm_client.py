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
        sys.exit("Unsupported protocol version")

    limit = policy.get("max_in_flight")
    if type(limit) is not int or limit < 1:
        sys.exit("Invalid concurrency limit")

    operations = policy.get("operations")
    if not isinstance(operations, list) or not operations:
        sys.exit("The service did not provide a list of operations")

    task = input("What would you like to do: ")

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
        api_key = getpass("OpenAI API key: ")

    with OpenAI(
        api_key=api_key,
        timeout=30,
        max_retries=0,
    ) as llm:
            decision = llm.responses.create(
                model="gpt-4.1-mini",
                instructions=(
                    "Complete the user's task using the available tools. "
                    "Choose at most one tool. "
                    "If you can fill in the required parameters from the user's "
                    "request, call the appropriate tool. "
                    "Do not ask for additional preferences "
                    "that are not required to call the operation. "
                    "Consider the operation description and parameter schema. "
                    "Ask for clarification only when a required parameter "
                    "cannot be determined without inventing it. "
                    "If no suitable tool is available, say so."
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
        print(decision.output_text or "The model did not select an operation.")
        sys.exit(0)

    if len(calls) != 1:
        sys.exit("Expected a single operation call")

    call = calls[0]

    # Allow only an operation from the published list.
    operation = next(
        (item for item in operations if item["name"] == call.name),
        None,
    )

    if operation is None:
        sys.exit("The model selected an unknown operation")

    params = json.loads(call.arguments)

    print("The model selected:", operation["name"])
    print(
        "Parameters:",
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
        sys.exit("This client currently supports only POST")

    # For this demo, allow only simple paths within the same service.
    if not re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", path):
        sys.exit("Unsupported operation path")

    for attempt in range(1, 6):
        print(f"Sending {method} {path}, attempt {attempt}")

        response = client.request(
            method,
            BASE_URL + path,
            headers={"X-Client-Type": "agent"},
            json=params,
        )

        if response.status_code == 429 and attempt < 5:
            raw = response.headers.get("Retry-After", "1").strip()
            seconds = int(raw) if raw.isascii() and raw.isdigit() else 1

            print(f"The service asks to wait {seconds} seconds.")
            time.sleep(seconds)
            continue

        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        break
