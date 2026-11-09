"""Offline review regression tests with synthetic, explicitly scoped inputs."""
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from rdflib import Graph, Literal, URIRef, RDFS

from ExamReview import review_helpers as review
from ExamReview import rdf_comparison as rdf

KEY = ['Identifier', 'tripla_original']


def ratings(a, b):
    return pd.DataFrame({'x_a1': pd.array(a, dtype='boolean'),
                         'x_a2': pd.array(b, dtype='boolean')})


def graph(*triples):
    result = Graph()
    for triple in triples:
        result.add(tuple(URIRef('urn:' + value) for value in triple))
    return result


class ReviewTests(unittest.TestCase):
    def test_binary_missing_and_invalid(self):
        for value in [None, '', float('nan')]:
            self.assertTrue(pd.isna(review.binary(value)))
        self.assertFalse(review.binary('não'))
        self.assertFalse(review.binary(0))
        for value in ['maybe', 'NA', 2]:
            with self.assertRaises(ValueError):
                review.binary(value)

    def test_preparation_preserves_input_and_missingness(self):
        raw = pd.DataFrame({'Identifier': ['A', '', ''], 'tripla_original': ['t1', 't2', ''],
                            'x': [True, None, False]})
        before = raw.copy(deep=True)
        rows = review.prepare_review(raw, KEY, ['x'])
        pd.testing.assert_frame_equal(raw, before)
        self.assertEqual(rows.Identifier.tolist(), ['A', 'A'])
        self.assertTrue(pd.isna(rows.iloc[1].x))
        self.assertEqual(rows.excel_row.tolist(), [2, 3])

    def test_duplicate_keys_and_missing_columns_rejected(self):
        raw = pd.DataFrame({'Identifier': ['A', 'A'], 'tripla_original': ['t', 't'], 'x': [1, 0]})
        with self.assertRaisesRegex(ValueError, 'duplicate keys'):
            review.prepare_review(raw, KEY, ['x'])
        with self.assertRaisesRegex(ValueError, 'missing columns'):
            review.prepare_review(raw.drop(columns='x'), KEY, ['x'])

    def test_pairing_ignores_row_order_and_keeps_unmatched(self):
        raw = pd.DataFrame({'Identifier': ['A', 'A'], 'tripla_original': ['t1', 't2'], 'x': [1, 0]})
        a = review.prepare_review(raw, KEY, ['x'])
        b = a.iloc[::-1].copy()
        shared = review.shared_items(review.align_reviews(a, b, KEY), KEY)
        self.assertEqual(review.metrics(shared, 'x')['agreement_pct'], 100)
        paired = review.align_reviews(a, b.iloc[:1], KEY)
        self.assertEqual((paired['_merge'] == 'left_only').sum(), 1)

    def test_context_mismatch_rejected(self):
        frame = pd.DataFrame({'Identifier': ['A'], 'tripla_original': ['t'],
                              'Texto_Original_a1': ['left'], 'Texto_Original_a2': ['right']})
        with self.assertRaisesRegex(ValueError, 'contexts differ'):
            review.validate_context(frame, KEY)

    def test_metrics_and_category_denominators(self):
        frame = ratings([1, 1, 0, 0, None], [1, 0, 0, 0, 1])
        result = review.metrics(frame, 'x')
        self.assertEqual(result['n_pairs'], 4)
        self.assertEqual(result['agreement_pct'], 75)
        self.assertAlmostEqual(result['kappa_cohen'], 0.5)
        categories = review.category_agreement(frame, 'x', 'X')
        self.assertAlmostEqual(categories['positive_agreement_pct'], 200/3)
        self.assertEqual(categories['negative_agreement_pct'], 80)
        self.assertEqual(categories['both_correct_among_any_correct_pct'], 50)

    def test_kappa_degenerate_and_empty(self):
        self.assertTrue(math.isnan(review.metrics(ratings([1], [1]), 'x')['kappa_cohen']))
        self.assertEqual(review.metrics(ratings([None], [1]), 'x')['status'], 'no pairs')
        self.assertEqual(review.agreement_matrix(ratings([], []), 'x').sum(), 0)

    def test_whole_triples_distinguish_different_errors(self):
        frame = ratings([0, 1, None], [1, 1, 0]).assign(
            y_a1=pd.array([1, 1, 0], dtype='boolean'), y_a2=pd.array([0, 1, 0], dtype='boolean'))
        before = frame.copy(deep=True)
        result = review.whole_triple_agreement(frame, ['x', 'y'])
        self.assertEqual(len(result), 2)
        self.assertEqual(result.both_incorrect.sum(), 1)
        self.assertEqual(result.same_error_criterion.sum(), 0)
        self.assertEqual(result.both_correct.sum(), 1)
        pd.testing.assert_frame_equal(before, frame)
        with self.assertRaises(ValueError):
            review.whole_triple_agreement(frame, [])

    def test_unresolved_marker_is_not_missing_correction(self):
        frame = pd.DataFrame({'entidades_adequadas PREENCHER SE INCORRETO_a1':
                              ['objeto: NÃO determinada', None, 'wd:Q42', 'text suggestion']})
        self.assertEqual(review.unresolved_flags(frame, 'a1').tolist(), [True, False, False, False])

    def test_joint_errors_separate_shared_from_different_reasons(self):
        frame = ratings([0, 0, 1, None], [0, 1, 1, 0]).assign(
            y_a1=pd.array([0, 1, 1, 0], dtype='boolean'),
            y_a2=pd.array([1, 0, 1, 0], dtype='boolean'))
        before = frame.copy(deep=True)
        labels = {'x': 'Entity', 'y': 'Predicate'}
        details = review.joint_error_details(frame, labels)
        self.assertEqual(len(details), 2)
        self.assertEqual(details.shared_error_pattern.tolist(), ['Entity', 'No shared criterion'])
        self.assertEqual(details.same_error_set.sum(), 0)
        summary = review.joint_error_summary(details, labels)
        self.assertEqual(summary.both_flag_error.tolist(), [1, 0])
        self.assertEqual(summary.both_flag_error_pct.tolist(), [50, 0])
        self.assertEqual(review.joint_error_patterns(details).triples.sum(), 2)
        pd.testing.assert_frame_equal(before, frame)

    def test_no_joint_errors_keeps_summary_schema(self):
        frame = ratings([1], [1])
        details = review.joint_error_details(frame, {'x': 'Entity'})
        self.assertTrue(details.empty)
        summary = review.joint_error_summary(details, {'x': 'Entity'})
        self.assertEqual(summary.iloc[0].both_flag_error, 0)
        self.assertTrue(math.isnan(summary.iloc[0].both_flag_error_pct))
        self.assertTrue(review.joint_error_patterns(details).empty)


class GraphTests(unittest.TestCase):
    def test_prefix_order_and_layout_do_not_change_graph(self):
        a, _ = rdf.parse_graph('@prefix a: <urn:> . a:s a:p a:o .', 'a')
        b, _ = rdf.parse_graph('@prefix b: <urn:> .\\nb:s b:p b:o .', 'b')
        self.assertEqual(set(a), set(b))
        self.assertEqual(rdf.decode_layout('"a\\nb"\\n'), '"a\\nb"\n')

    def test_invalid_turtle_and_blank_nodes_rejected(self):
        for text in ['not turtle', '[] <urn:p> <urn:o> .']:
            with self.assertRaises(ValueError):
                rdf.parse_graph(text, 'test')

    def test_identical_graphs_have_empty_diff_schema(self):
        a = {'A': graph(('s', 'p', 'o'))}
        self.assertEqual(rdf.compare_graphs(a, a)[1].iloc[0].jaccard_pct, 100)
        diff = rdf.exclusive_triples(a, a)
        self.assertTrue(diff.empty)
        self.assertIn('source', diff.columns)
        self.assertTrue(rdf.mismatch_candidates(a, a).empty)

    def test_group_scoping_and_labels(self):
        g = graph(('s', 'p', 'o'))
        g.add((URIRef('urn:s'), RDFS.label, Literal('Subject', lang='en')))
        _, totals = rdf.compare_graphs({'A': g, 'B': g}, {'A': g, 'B': g})
        table = totals.set_index('dimension')
        self.assertEqual(table.loc['All RDF triples', 'shared'], 4)
        self.assertEqual(table.loc['Relation triples', 'shared'], 2)
        self.assertEqual(table.loc['Label triples', 'shared'], 2)

    def test_candidate_types_and_overlap(self):
        a = {'A': graph(('s', 'p', 'o'))}
        for triple, kind in [(('s', 'q', 'o'), 'Predicate only'),
                             (('o', 'p', 's'), 'Direction only'),
                             (('s', 'p', 'z'), 'One endpoint only'),
                             (('o', 'q', 's'), 'Direction and predicate')]:
            self.assertIn(kind, set(rdf.mismatch_candidates(a, {'A':graph(triple)}).kind))

    def test_loader_rejects_missing_duplicate_and_blank_identifiers(self):
        text = '<urn:s> <urn:p> <urn:o> .'
        for raw, error in [
            (pd.DataFrame({'Identifier':['A']}), 'missing columns'),
            (pd.DataFrame({'Identifier':['A','B'], 'rdf':[text,'']}), 'missing final graphs'),
            (pd.DataFrame({'Identifier':['A','A'], 'rdf':[text,text]}), 'duplicate'),
            (pd.DataFrame({'Identifier':[''], 'rdf':[text]}), 'missing or duplicate')]:
            with patch.object(rdf.pd, 'read_excel', return_value=raw):
                with self.assertRaisesRegex(ValueError, error):
                    rdf.load_final_graphs(Path('test.xlsx'), 'rdf')

    def test_graph_keys_and_deterministic_labels(self):
        with self.assertRaisesRegex(ValueError, 'Unmatched'):
            rdf.compare_graphs({'A': Graph()}, {'B': Graph()})
        with self.assertRaises(ValueError):
            rdf.compare_graphs({}, {})
        g = Graph()
        subject = URIRef('urn:s')
        for label in [Literal('Z', lang='en'), Literal('A', lang='en'), Literal('0', lang='pt')]:
            g.add((subject, RDFS.label, label))
        self.assertTrue(rdf.display_term(subject, g).startswith('A ['))


if __name__ == '__main__':
    unittest.main()
