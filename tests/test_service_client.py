"""The same direct client discovers unrelated APIs, accepting additive v3 fields."""
import json
from pathlib import Path
import sys
import unittest

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'examples'))
from protocol_client import discover, execute, service_client


class GenericClient(unittest.TestCase):
    def test_origin_credentials_and_unknown_operation_name_are_not_catalog_bound(self):
        calls = []
        operation = dict(name='lookup_case', description='Read a case', method='POST', path='/cases/read',
                         input_schema={'type':'object', 'properties':{'key':{'type':'string'}}, 'required':['key'], 'additionalProperties':False})
        def handle(request):
            calls.append(request)
            self.assertEqual(request.headers['authorization'], 'Bearer test-restricted')
            self.assertEqual(request.url.host, 'support.test')
            if request.url.path == '/agent-policy':
                return httpx.Response(200, json=dict(version=3, service_id='support', principal_id='demo-owner', client_class='agent', limits={'scope':'principal','max_outstanding':5}, operations=[operation]))
            self.assertEqual(request.url.path, '/cases/read')
            self.assertEqual(json.loads(request.content), {'key':'example'})
            return httpx.Response(200, json={'case':{'key':'example'}})
        with httpx.Client(transport=httpx.MockTransport(handle), headers={'Authorization':'Bearer test-restricted'}) as http:
            operations = discover(http, base_url='https://support.test/')
            self.assertEqual(execute(http, operations[0], {'key':'example'}, base_url='https://support.test/'), {'case':{'key':'example'}})
        self.assertEqual(len(calls), 2)
        with service_client('test-restricted') as http:
            self.assertEqual(http.headers['authorization'], 'Bearer test-restricted')
