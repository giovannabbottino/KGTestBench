"""Unique JSON adjudication cases, subset membership, and overwrite safety."""
import json
import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from rdflib import Graph, URIRef

from ExamReview import export_disagreements as export


def row():
    item = {'Identifier': 'Example', 'tripla_original': 'wd:Q1 kg:be wd:Q2'}
    item.update({f'{c}_{r}': True for c in export.ALL_CRITERIA for r in ['a1','a2']})
    for r in ['a1','a2']:
        item[f'entidades_adequadas PREENCHER SE INCORRETO_{r}'] = None
        item[f'excel_row_{r}'] = 2
    return item


def graph(*triples):
    result = Graph()
    for triple in triples:
        result.add(tuple(URIRef('urn:'+term) for term in triple))
    return result


class ExportTests(unittest.TestCase):
    def payload(self):
        case = export.case_record('annotation', 'Example', 't')
        case['subset_ids'] = ['unilateral_approvals', 'unilateral_unresolved_entities']
        return {'schema_version': 2, 'cases': [case], 'subsets': export.index_subsets([case]),
                'scope': {'unique_cases': 1, 'subset_memberships': 2}}

    def test_validation_and_tables_preserve_human_decisions(self):
        payload = self.payload()
        payload['cases'][0]['adjudication'].update(status='resolved', decision='accept_a1')
        before = copy.deepcopy(payload)
        export.validate_export(payload)
        cases, subsets = export.adjudication_tables(payload)
        self.assertEqual(len(cases), 1)
        self.assertEqual(subsets.cases.sum(), 2)
        self.assertEqual(cases.iloc[0].status, 'resolved')
        self.assertEqual(payload, before)

    def test_invalid_case_references_and_counts_rejected(self):
        for mutate in [
            lambda p: p['cases'].append(copy.deepcopy(p['cases'][0])),
            lambda p: p['subsets']['unilateral_approvals']['case_ids'].append('unknown'),
            lambda p: p['scope'].update(unique_cases=2),
            lambda p: p.update(schema_version=1),
        ]:
            payload = self.payload()
            mutate(payload)
            with self.assertRaises(ValueError):
                export.validate_export(payload)

    def test_loading_stale_sources_does_not_modify_json(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/'source.xlsx', root/'review.json'
            source.write_bytes(b'original')
            payload = self.payload()
            payload['sources'] = {'a1': {'sha256': export.review.file_sha256(source)}}
            export.write_export(payload, target)
            self.assertEqual(export.load_export(target, {'a1': source})[0], payload)
            original = target.read_bytes()
            source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Stale'):
                export.load_export(target, {'a1': source})
            self.assertEqual(target.read_bytes(), original)

    def test_unilateral_is_one_case_not_one_per_criterion(self):
        item = row()
        item['predicado_correto_a2'] = False
        item['fidelidade_semantica_a2'] = False
        cases = export.annotation_cases(pd.DataFrame([item]))
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['subset_ids'], ['unilateral_approvals'])
        self.assertIs(cases[0]['evidence']['a2']['fully_correct'], False)
        self.assertEqual(len(cases[0]['differing_criteria']), 2)

    def test_rejections_include_partial_and_disjoint_diagnoses(self):
        item = row()
        item['entidade_correta_a1'] = item['predicado_correto_a2'] = False
        cases = export.annotation_cases(pd.DataFrame([item]))
        self.assertEqual(cases[0]['diagnosis_overlap'], 'none')
        item['entidade_correta_a2'] = False
        cases = export.annotation_cases(pd.DataFrame([item]))
        self.assertEqual(cases[0]['diagnosis_overlap'], 'partial')
        item['predicado_correto_a1'] = False
        self.assertEqual(export.annotation_cases(pd.DataFrame([item])), [])

    def test_overlapping_subsets_do_not_duplicate_case(self):
        item = row()
        item['entidade_correta_a1'] = False
        item['predicado_correto_a2'] = False
        item['entidades_adequadas PREENCHER SE INCORRETO_a1'] = 'objeto: não determinada'
        cases = export.annotation_cases(pd.DataFrame([item]))
        self.assertEqual(len(cases), 1)
        self.assertEqual(len(cases[0]['subset_ids']), 2)
        self.assertEqual(cases[0]['unresolved_marker_absence_means'], 'unknown')

    def test_missing_does_not_become_approval_or_rejection(self):
        item = row()
        item['fidelidade_semantica_a1'] = pd.NA
        item['predicado_correto_a2'] = False
        self.assertEqual(export.annotation_cases(pd.DataFrame([item])), [])
        item['entidades_adequadas PREENCHER SE INCORRETO_a1'] = 'não determinada'
        case = export.annotation_cases(pd.DataFrame([item]))[0]
        self.assertIsNone(case['evidence']['a1']['ratings']['fidelidade_semantica'])
        self.assertEqual(case['subset_ids'], ['unilateral_unresolved_entities'])

    def test_both_unresolved_not_unilateral(self):
        item = row()
        for r in ['a1','a2']:
            item[f'entidades_adequadas PREENCHER SE INCORRETO_{r}'] = 'não determinada'
        self.assertEqual(export.annotation_cases(pd.DataFrame([item])), [])

    def test_graph_alternatives_grouped_once_including_direction(self):
        left = {'A': graph(('s','p','o'),('s','q','o'))}
        right = {'A': graph(('o','p','s'),('s','r','o'))}
        cases, excluded = export.competing_cases(left, right)
        self.assertEqual(len(cases), 1)
        self.assertEqual(len(cases[0]['a1_triples']), 2)
        self.assertTrue(cases[0]['direction_sets_differ'])
        self.assertTrue(cases[0]['predicate_sets_differ'])
        self.assertEqual(excluded['unpaired_relation_triples_a1'], 0)

    def test_identical_and_unpaired_graph_edges_not_competitors(self):
        a = {'A': graph(('s','p','o'))}
        self.assertEqual(export.competing_cases(a, a)[0], [])
        cases, excluded = export.competing_cases(a, {'A':graph(('s','p','z'))})
        self.assertEqual(cases, [])
        self.assertEqual(excluded['unpaired_relation_triples_a1'], 1)

    def test_stable_id_and_json_types(self):
        a = export.case_record('annotation', 'São Paulo', 't')
        self.assertEqual(a['case_id'], export.case_record('annotation', 'São Paulo', 't')['case_id'])
        with TemporaryDirectory() as directory:
            p = Path(directory)/'cases.json'
            payload = {'cases': [a], 'rating': False, 'missing': None}
            export.write_export(payload, p)
            self.assertEqual(json.loads(p.read_text(encoding='utf-8')), payload)
            with self.assertRaises(FileExistsError):
                export.write_export({}, p)
            self.assertEqual(json.loads(p.read_text(encoding='utf-8')), payload)
            with self.assertRaises(ValueError):
                export.write_export({}, p.with_suffix('.csv'))


if __name__ == '__main__':
    unittest.main()
