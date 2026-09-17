"""Local retrieval finds candidates, never certifies their applicability."""
import unittest
from test_records import case, change, report


class RankedRetrieval(unittest.TestCase):
    def rank(self, records, query, **kwargs):
        from omarchy_knowledge.retrieval import rank_cases
        return rank_cases(records, query, **kwargs)

    def test_aliases_match_number_pad_without_exact_wording(self):
        record = case(); record['payload']['title'] = 'Numpad shortcuts lost after reload'
        found = self.rank([record], 'number pad hotkeys')
        self.assertEqual([r['case_id'] for r in found], [record['id']])

    def test_shared_component_alone_is_not_enough(self):
        self.assertEqual(self.rank([case()], 'dock Ethernet network loses packets'), [])
        self.assertEqual(self.rank([case()], 'Bluetooth headphones disconnect after resume'), [])

    def test_exact_id_lookup_resolves_change_to_case(self):
        self.assertEqual(self.rank([case(), change()], change()['id'])[0]['case_id'], case()['id'])

    def test_searches_relevant_environment_identifiers(self):
        self.assertEqual(self.rank([case(), change(), report()], '1234 abcd')[0]['case_id'], case()['id'])

    def test_boundaries_punctuation_and_sql_syntax_are_inert(self):
        self.assertEqual(self.rank([case()], '" OR * ; --'), [])
        for query in ('x' * 513, ' '.join('term'+str(i) for i in range(65))):
            with self.assertRaises(ValueError):
                self.rank([case()], query)

    def test_intent_and_empty_query_are_explicit(self):
        record = case(); record['payload']['intent'] = 'optional'
        self.assertEqual(self.rank([record], 'dock'), [])
        self.assertEqual(len(self.rank([record], '', intent='optional')), 1)

    def test_broad_search_can_recover_partial_candidates_but_labels_coverage(self):
        found = self.rank([case()], 'dock Ethernet network loses packets', broad=True)
        self.assertEqual(len(found), 1)
        self.assertLess(found[0]['coverage'], 0.5)

    def test_title_match_ranks_above_body_only_match(self):
        first = case('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        first['payload']['title'] = 'Unrelated title'
        first['payload']['observed'] = 'keypad shortcut absent'
        second = case(); second['payload']['title'] = 'Keypad shortcut absent'
        self.assertEqual(self.rank([first, second], 'keypad shortcut')[0]['case_id'], second['id'])
