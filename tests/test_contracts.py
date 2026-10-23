"""Machine-readable contracts must describe the shipped fixtures and reject drift."""
import copy
import json
from pathlib import Path
import unittest

import jsonschema
from ai_protocol.conformance import _contracts, validate_contract

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_schemas_are_valid_and_manifests_match(self):
        for schema in _contracts()[0].values():
            jsonschema.Draft202012Validator.check_schema(schema)
        for path in (ROOT / "services").glob("*.json"):
            validate_contract("manifest", json.loads(path.read_text()))

    def test_versions_and_unknown_manifest_fields_are_rejected(self):
        original = json.loads((ROOT / "services/catalog.json").read_text())
        for field, value in [("format_version", 2), ("secret_token", "must-not-be-in-a-manifest")]:
            manifest = copy.deepcopy(original)
            manifest[field] = value
            with self.assertRaises(jsonschema.ValidationError):
                validate_contract("manifest", manifest)

    def test_policy_cross_schema_and_error_shape(self):
        policy = dict(version=3, service_id="records", principal_id="reader", client_class="agent",
                      policy_revision=1, refresh_after_ms=1000, outstanding=0,
                      limits=dict(scope="principal", max_outstanding=2), queue=dict(max_wait_ms=1000),
                      operations=[dict(name="read", description="Read records", method="POST", path="/records", input_schema={"type": "object"})])
        validate_contract("policy", policy)
        policy["operations"][0]["method"] = "DELETE"
        with self.assertRaises(jsonschema.ValidationError):
            validate_contract("policy", policy)
        validate_contract("error", {"error": {"code": "execution_timeout", "execution": "unknown"}})
        with self.assertRaises(jsonschema.ValidationError):
            validate_contract("error", {"error": {"code": "execution_timeout", "execution": "not_started", "token": "forbidden"}})
