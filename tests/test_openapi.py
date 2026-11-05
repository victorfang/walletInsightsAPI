"""Keep the rewritten two-input/integer contract and runtime aliases aligned."""

import json
from pathlib import Path
import re
import unittest

from wallet_age_days.service import ADDRESS_RE, EVM_ALIASES

SPEC_PATH = Path(__file__).resolve().parents[1] / 'openapi.json'


class OpenAPIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(SPEC_PATH.read_text())

    def test_all_local_references_resolve(self):
        def visit(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    reference = value['$ref']
                    self.assertTrue(reference.startswith('#/'))
                    target = self.spec
                    for part in reference[2:].split('/'):
                        target = target[part.replace('~1', '/').replace('~0', '~')]
                    self.assertIsInstance(target, dict)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(self.spec)

    def test_only_new_route_and_support_endpoints_are_advertised(self):
        self.assertEqual(set(self.spec['paths']), {'/walletAgeDays', '/WalletInsights', '/health', '/openapi.json'})
        self.assertEqual(self.spec['paths']['/walletAgeDays']['get']['operationId'], 'walletAgeDays')

    def test_success_has_no_envelope_or_enrichment(self):
        self.assertEqual(self.spec['components']['schemas']['WalletAgeDays']['type'], 'integer')
        for method in ['get', 'post']:
            content = self.spec['paths']['/walletAgeDays'][method]['responses']['200']['content']['application/json']
            self.assertEqual(content['schema'], {'$ref': '#/components/schemas/WalletAgeDays'})
            for example in content['examples'].values():
                self.assertIs(type(example['value']), int)
                self.assertGreaterEqual(example['value'], 0)

    def test_only_blockchain_and_address_are_inputs(self):
        operation = self.spec['paths']['/walletAgeDays']['get']
        self.assertEqual({x['name'] for x in operation['parameters']}, {'blockchain', 'address'})
        self.assertTrue(all(x['required'] for x in operation['parameters']))
        request = self.spec['components']['schemas']['WalletAgeRequest']
        self.assertEqual(set(request['required']), {'blockchain', 'address'})
        self.assertEqual(set(request['properties']), {'blockchain', 'address'})
        self.assertFalse(request['additionalProperties'])

    def test_aliases_and_current_coverage_match_runtime(self):
        for method in ['get', 'post']:
            operation = self.spec['paths']['/walletAgeDays'][method]
            self.assertEqual(set(operation['x-family-aliases']), EVM_ALIASES)
            self.assertEqual(operation['x-current-coverage'], ['ethereum'])
            headers = operation['responses']['200']['headers']
            self.assertEqual(headers['X-Wallet-Age-Coverage']['schema']['const'], 'ethereum')

    def test_address_pattern_matches_regex_only_validation(self):
        pattern = self.spec['components']['schemas']['WalletAgeRequest']['properties']['address']['pattern']
        for address in ['0x' + 'a' * 40, '0x' + 'AB12' * 10, '0X' + 'a' * 40,
                        '0x' + 'a' * 39, '0x' + 'a' * 41, '0x' + 'g' * 40,
                        '0x' + 'a' * 40 + '\n', '0x' + '\uff11' * 40]:
            self.assertEqual(bool(re.fullmatch(pattern, address)), bool(ADDRESS_RE.fullmatch(address)))

    def test_error_examples_are_strings_and_not_cacheable(self):
        expected = {'400': {'invalid', 'invalid request'}, '401': {'unauthorized'},
                    '404': {'not_found'}, '405': {'method not allowed'},
                    '501': {'coming soon contact info@anchain.ai'}, '503': {'unknown'}}
        for method in ['get', 'post']:
            responses = self.spec['paths']['/walletAgeDays'][method]['responses']
            for code, messages in expected.items():
                response = responses[code]
                self.assertEqual(response['headers']['Cache-Control']['schema']['const'], 'no-store')
                examples = response['content']['application/json']['examples']
                self.assertEqual({ex['value'] for ex in examples.values()}, messages)

    def test_local_spec_and_optional_header_auth(self):
        self.assertEqual(self.spec['openapi'], '3.1.0')
        self.assertEqual([server['url'] for server in self.spec['servers']], ['http://127.0.0.1:8787'])
        self.assertEqual(self.spec['paths']['/openapi.json']['get']['security'], [])
        scheme = self.spec['components']['securitySchemes']['HeaderApiKeyAuth']
        self.assertEqual((scheme['in'], scheme['name']), ('header', 'x-api-key'))

    def test_insights_is_a_separate_object_response_with_two_inputs(self):
        for method in ['get', 'post']:
            operation = self.spec['paths']['/WalletInsights'][method]
            content = operation['responses']['200']['content']['application/json']
            self.assertNotEqual(content['schema'], {'$ref': '#/components/schemas/WalletAgeDays'})
            self.assertIn('X-Wallet-Insights-Cache', operation['responses']['200']['headers'])
            self.assertNotIn('X-Wallet-Age-Quality', operation['responses']['200']['headers'])
        inputs = self.spec['paths']['/WalletInsights']['get']['parameters']
        self.assertEqual({value['name'] for value in inputs}, {'blockchain', 'address'})


if __name__ == '__main__':
    unittest.main()
