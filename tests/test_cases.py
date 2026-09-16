"""Synthetic fixtures. Tests catch unsafe coercion, leakage and preference promotion."""
import json
import unittest
import subprocess
import sys
import tempfile
from pathlib import Path
from knowledge import parse_case, build_index, search

def case(intent='corrective', identifier='bd3e61da-3b3c-4e4e-9fba-970ef606672f'):
    return {'schema_version': 1, 'id': identifier, 'type': 'case',
            'created_at': '2026-09-16T12:00:00Z', 'provenance': {'kind': 'external-source'},
            'payload': {'title': 'Dock keyboard stops after suspend', 'intent': intent,
                        'domains': ['input', 'dock'],
                        'expectation': {'basis': 'previously-working', 'text': 'Input works after resume'},
                        'observed': 'Input stops after resume'}}

class Cases(unittest.TestCase):
    def test_schema_exists(self):
        self.assertTrue(Path('schemas/v1/case.schema.json').exists())

    def test_accepts_case_without_changing_it(self):
        record = case()
        self.assertEqual(parse_case(json.dumps(record)), record)

    def test_rejects_duplicate_keys(self):
        with self.assertRaises(ValueError):
            parse_case('{"schema_version":1,"schema_version":1}')

    def test_rejects_future_schema(self):
        record = case(); record['schema_version'] = 2
        with self.assertRaises(ValueError): parse_case(json.dumps(record))

    def test_rejects_forged_authority(self):
        record = case(); record['verified'] = True
        with self.assertRaises(ValueError): parse_case(json.dumps(record))

    def test_rejects_invalid_timestamp(self):
        record = case(); record['created_at'] = 'yesterday'
        with self.assertRaises(ValueError): parse_case(json.dumps(record))

    def test_rejects_sensitive_free_text(self):
        for text in ['mail me at person@example.org', 'host 192.168.1.4',
                     'device aa:bb:cc:dd:ee:ff', '/home/person/.config/hypr',
                     'sk-or-v1-' + 'x' * 40, 'serial number: UNIQUE123']:
            with self.subTest(text=text):
                record = case(); record['payload']['observed'] = text
                with self.assertRaises(ValueError): parse_case(json.dumps(record))

    def test_rejects_hidden_control_text(self):
        record = case(); record['payload']['observed'] = 'safe\u202eevil'
        with self.assertRaises(ValueError): parse_case(json.dumps(record))

    def test_rejects_oversized_record(self):
        with self.assertRaises(ValueError): parse_case(' ' * 65537)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError): build_index([case(), case()])

    def test_index_is_order_independent(self):
        a = case(); b = case('optional', '0d94c6f4-284c-41ec-9d03-754131ca66ea')
        self.assertEqual(build_index([a, b]), build_index([b, a]))

    def test_optional_hidden_unless_requested(self):
        rows = build_index([case('optional')])
        self.assertEqual(search(rows, 'keyboard'), [])
        self.assertEqual(len(search(rows, 'keyboard', include_optional=True)), 1)

    def test_search_preserves_classification_and_no_execution(self):
        rows = build_index([case()])
        result = search(rows, 'DOCK keyboard')
        self.assertEqual(result[0]['intent'], 'corrective')
        self.assertEqual(search(rows, 'graphics'), [])

    def test_cli_searches_validated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'case.json'
            path.write_text(json.dumps(case()))
            result = subprocess.run([sys.executable, 'knowledge.py', 'search',
                                     '--query', 'keyboard', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)[0]['intent'], 'corrective')

    def test_cli_rejects_sensitive_record_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'case.json'
            record = case(); record['payload']['observed'] = 'person@example.org'
            path.write_text(json.dumps(record))
            result = subprocess.run([sys.executable, 'knowledge.py', 'validate', str(path)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('person@example.org', result.stdout + result.stderr)

if __name__ == '__main__': unittest.main()
