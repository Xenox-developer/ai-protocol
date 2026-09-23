"""Shared v3 discovery, authorization, and bounded retry handling."""

import os
import re
import time

import httpx
import jsonschema

BASE_URL = os.environ.get("SERVICE_URL", f"http://127.0.0.1:{int(os.environ.get('GATEWAY_PORT', '3000'))}").rstrip("/")


def service_client(token=None):
    token = token or os.environ.get("AGENT_TOKEN_1")
    if not token:
        raise ValueError("Set AGENT_TOKEN_1 before running the client")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
        raise ValueError("AGENT_TOKEN_1 is not a valid bearer token")
    return httpx.Client(
        headers={"Authorization": f"Bearer {token}"},
        trust_env=False,
        timeout=30,
        follow_redirects=False,
    )


def validate_operation(operation):
    if not isinstance(operation, dict):
        raise ValueError("Invalid operation descriptor")
    if not isinstance(operation.get("name"), str) or not operation["name"]:
        raise ValueError("Invalid operation name")
    if not isinstance(operation.get("description"), str):
        raise ValueError("Invalid operation description")
    if operation.get("method") != "POST":
        raise ValueError("This client currently supports only POST")
    path = operation.get("path")
    if not isinstance(path, str) or not re.fullmatch(
        r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", path
    ):
        raise ValueError("Unsupported operation path")
    schema = operation.get("input_schema")
    if not isinstance(schema, dict):
        raise ValueError("Invalid operation schema")

    # Remote schema references must not cause requests to arbitrary origins.
    def check_references(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("$ref", "$dynamicRef") and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise ValueError("External schema references are not supported")
                check_references(item)
        elif isinstance(value, list):
            for item in value:
                check_references(item)

    check_references(schema)
    jsonschema.Draft202012Validator.check_schema(schema)


def discover(client, *, base_url=None):
    response = client.get((base_url or BASE_URL).rstrip("/") + "/agent-policy")
    response.raise_for_status()
    policy = response.json()
    if (
        not isinstance(policy, dict)
        or type(policy.get("version")) is not int
        or policy["version"] != 3
    ):
        raise ValueError("Unsupported protocol version; this client requires v3")
    if not isinstance(policy.get("principal_id"), str) or not policy["principal_id"]:
        raise ValueError("Invalid principal ID")
    if policy.get("client_class") not in ("agent", "interactive"):
        raise ValueError("Invalid client class")
    limits = policy.get("limits")
    if not isinstance(limits, dict) or limits.get("scope") != "principal":
        raise ValueError("Unsupported budget scope")
    maximum = limits.get("max_outstanding")
    if type(maximum) is not int or maximum < 1:
        raise ValueError("Invalid outstanding limit")
    operations = policy.get("operations")
    if not isinstance(operations, list):
        raise ValueError("Invalid operation list")
    names = set()
    for operation in operations:
        validate_operation(operation)
        if operation["name"] in names:
            raise ValueError("Duplicate operation name")
        names.add(operation["name"])
    return operations


def safe_to_retry(response):
    if response.status_code == 429:
        return True
    if response.status_code != 503:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    error = body.get("error") if isinstance(body, dict) else None
    return (
        isinstance(error, dict)
        and error.get("code") == "queue_timeout"
        and error.get("execution") == "not_started"
    )


def execute(client, operation, params, *, task_timeout=120, base_url=None):
    validate_operation(operation)
    jsonschema.Draft202012Validator(operation["input_schema"]).validate(params)
    deadline = time.monotonic() + task_timeout
    for attempt in range(1, 6):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Operation deadline exceeded")
        response = client.request(
            operation["method"], (base_url or BASE_URL).rstrip("/") + operation["path"], json=params,
            timeout=min(30, remaining),
        )
        if time.monotonic() >= deadline:
            raise TimeoutError("Operation deadline exceeded")
        if safe_to_retry(response) and attempt < 5:
            raw = response.headers.get("Retry-After", "1").strip()
            seconds = int(raw) if raw.isascii() and raw.isdigit() else 1
            if seconds >= deadline - time.monotonic():
                raise TimeoutError("Retry-After exceeds the operation deadline")
            print(f"The service asks to wait {seconds} seconds.")
            time.sleep(seconds)
            continue
        response.raise_for_status()
        return response.json()
