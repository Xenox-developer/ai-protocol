"""Reusable offline schema validation and opt-in HTTP conformance checks."""
import argparse
import json
import os
from functools import lru_cache
from importlib.resources import files

import httpx
import jsonschema
from referencing import Registry, Resource

from .client import validate_policy


@lru_cache(maxsize=1)
def _contracts():
    schemas = {name: json.loads(files("ai_protocol").joinpath(f"schemas/{name}.schema.json").read_text())
               for name in ("operation", "policy", "error", "manifest")}
    registry = Registry().with_resources((value["$id"], Resource.from_contents(value)) for value in schemas.values())
    return schemas, registry


def validate_contract(name, document):
    """Raise ValidationError for a contract violation; never fetch remote schemas."""
    schemas, registry = _contracts()
    jsonschema.Draft202012Validator(schemas[name], registry=registry).validate(document)
    if name == "policy":
        validate_policy(document)


def check_service(origin, token, *, operation=None, params=None):
    """Check discovery/authentication and optionally execute one read-only call."""
    from .protocol import validate_origin
    import re
    origin = validate_origin(origin)
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
        raise ValueError("Invalid bearer credential")
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as http:
        unauthenticated = http.get(origin + "/agent-policy")
        if unauthenticated.status_code != 401:
            raise ValueError("Discovery must reject unauthenticated requests with 401")
        validate_contract("error", unauthenticated.json())
        if unauthenticated.json()["error"]["execution"] != "not_started":
            raise ValueError("Authentication failure cannot start an operation")
        headers = {"Authorization": f"Bearer {token}"}
        response = http.get(origin + "/agent-policy", headers=headers)
        response.raise_for_status()
        if response.headers.get("cache-control") != "no-store":
            raise ValueError("Discovery must be uncacheable")
        policy = response.json()
        validate_contract("policy", policy)
        if operation is not None:
            descriptor = next((op for op in policy["operations"] if op["name"] == operation), None)
            if descriptor is None:
                raise ValueError("Requested operation is absent from discovery")
            jsonschema.Draft202012Validator(descriptor["input_schema"]).validate(params)
            result = http.post(origin + descriptor["path"], headers=headers, json=params)
            result.raise_for_status()
            result.json()
        return {"service_id": policy.get("service_id"), "version": policy["version"], "operations": len(policy["operations"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", help="Validate a manifest file offline")
    parser.add_argument("--origin", help="Explicit HTTP(S) service origin")
    parser.add_argument("--token-env", default="AGENT_TOKEN_1", help="Name of the token environment variable")
    parser.add_argument("--operation", help="Opt in to one read-only operation call")
    parser.add_argument("--params", help="JSON parameters, required with --operation")
    args = parser.parse_args()
    if bool(args.operation) != (args.params is not None):
        parser.error("--operation and --params must be supplied together")
    try:
        if args.manifest:
            with open(args.manifest) as source:
                validate_contract("manifest", json.load(source))
        if args.origin:
            result = check_service(args.origin, os.environ[args.token_env], operation=args.operation,
                                   params=json.loads(args.params) if args.params is not None else None)
            print(json.dumps(result))
        if not args.manifest and not args.origin:
            parser.error("Supply --manifest or --origin")
    except (ValueError, KeyError, OSError, httpx.HTTPError, jsonschema.ValidationError):
        # Third-party responses and validation errors can contain submitted secrets.
        parser.exit(1, "Conformance check failed; verify configuration and contract fields.\n")
    print("PASS: requested conformance checks.")


if __name__ == "__main__":
    main()
