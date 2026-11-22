"""CSV grouping, evidence preservation, selection rules, and overwrite safety."""
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from ExamReview import export_disagreements as export


def row(identifier='Example', triple='wd:Q1 kg:be wd:Q2 .'):
    item = {'Identifier': identifier, 'tripla_original': triple}
    for rater in ['a1', 'a2']:
        item[f'Texto_Original_{rater}'] = 'Original text, with "quotes".\nSecond line: São Paulo.'
        item.update({f'{criterion}_{rater}': True for criterion in export.CRITERIA})
        item.update({f'{column}_{rater}': None for column in export.CORRECTION_FIELDS.values()})
    return item


class ExportTests(unittest.TestCase):
    def test_approval_is_true_and_missing_correction_is_blank(self):
        item = row()
        item['predicado_correto_a2'] = item['fidelidade_semantica_a2'] = False
        blocks = export.row_disagreements(pd.Series(item))
        self.assertEqual(blocks, [('Predicate', 'TRUE', ''), ('Semantic fidelity', 'TRUE', 'FALSE')])

    def test_semantic_fidelity_rejection_is_false_for_either_rater(self):
        for rater in ['a1', 'a2']:
            item = row()
            item[f'fidelidade_semantica_{rater}'] = False
            expected = ('FALSE', 'TRUE') if rater == 'a1' else ('TRUE', 'FALSE')
            self.assertEqual(export.row_disagreements(pd.Series(item)), [('Semantic fidelity', *expected)])

    def test_different_judgments_have_one_block_per_criterion(self):
        item = row()
        item['entidade_correta_a1'] = item['predicado_correto_a2'] = False
        self.assertEqual(export.row_disagreements(pd.Series(item)),
                         [('Entity', 'remove', 'TRUE'), ('Predicate', 'TRUE', '')])

    def test_empty_entity_correction_propagates_remove_only_for_that_rater(self):
        item = row()
        item['entidade_correta_a1'] = False
        item['predicado_correto_a1'] = False
        item['fidelidade_semantica_a1'] = False
        self.assertEqual(export.row_disagreements(pd.Series(item)), [
            ('Entity', 'remove', 'TRUE'), ('Predicate', 'remove', 'TRUE'),
            ('Semantic fidelity', 'remove', 'TRUE')])

    def test_remove_preserves_written_corrections_and_true_approvals(self):
        item = row()
        item['entidade_correta_a2'] = False
        item['predicado_correto_a2'] = False
        item['direcao_relacao_correta_a1'] = False
        column = export.CORRECTION_FIELDS['predicado_correto']
        item[f'{column}_a2'] = 'kg:locatedIn'
        self.assertEqual(export.row_disagreements(pd.Series(item)), [
            ('Entity', 'TRUE', 'remove'), ('Predicate', 'TRUE', 'kg:locatedIn'),
            ('Direction', '', 'TRUE')])

    def test_provided_entity_correction_does_not_fill_other_empty_decisions(self):
        item = row()
        item['entidade_correta_a1'] = item['predicado_correto_a1'] = False
        column = export.CORRECTION_FIELDS['entidade_correta']
        item[f'{column}_a1'] = 'wd:Q42'
        self.assertEqual(export.row_disagreements(pd.Series(item)),
                         [('Entity', 'wd:Q42', 'TRUE'), ('Predicate', '', 'TRUE')])

    def test_same_judgments_but_different_corrections_are_exported(self):
        item = row()
        column = export.CORRECTION_FIELDS['entidade_correta']
        for rater in ['a1', 'a2']:
            item[f'entidade_correta_{rater}'] = False
        item[f'{column}_a1'], item[f'{column}_a2'] = 'wd:Q1', 'wd:Q2'
        self.assertEqual(export.row_disagreements(pd.Series(item)),
                         [('Entity', 'wd:Q1', 'wd:Q2')])

    def test_correct_rater_uses_true_and_rejecting_rater_keeps_source_text(self):
        item = row()
        column = export.CORRECTION_FIELDS['predicado_correto']
        item['predicado_correto_a1'] = False
        item[f'{column}_a1'] = '  kg:locatedIn\n'
        item[f'{column}_a2'] = 'unused suggestion'
        self.assertEqual(export.row_disagreements(pd.Series(item)),
                         [('Predicate', '  kg:locatedIn\n', 'TRUE')])

    def test_multiple_disagreements_stay_on_one_row_and_unused_blocks_are_blank(self):
        first = row('A')
        first['entidade_correta_a1'] = first['entidade_correta_a2'] = False
        first['predicado_correto_a1'] = False
        column = export.CORRECTION_FIELDS['entidade_correta']
        first[f'{column}_a1'] = 'objeto: não determinada'
        first[f'{column}_a2'] = 'wd:Q2'
        second = row('B')
        second['predicado_correto_a2'] = False
        result = export.export_rows(pd.DataFrame([second, first]))
        self.assertEqual(len(result), 2)
        self.assertEqual(result.identifier.tolist(), ['A', 'B'])
        self.assertEqual(result.columns.tolist(), export.BASE_COLUMNS + [
            f'{column}_{number}' for number in range(1, 3) for column in export.BLOCK_COLUMNS])
        self.assertEqual(result.iloc[0].disagreement_type_1, 'Entity')
        self.assertEqual(result.iloc[0].disagreement_type_2, 'Predicate')
        self.assertEqual(result.iloc[0].a1_decision_1, 'objeto: não determinada')
        self.assertEqual(result.iloc[0].a2_decision_2, 'TRUE')
        self.assertTrue(result.iloc[1, 6:].eq('').all())
        self.assertEqual(result.iloc[0].original_text, first['Texto_Original_a1'])

    def test_each_different_correction_has_its_own_decisions(self):
        item = row()
        for criterion, column in export.CORRECTION_FIELDS.items():
            for rater in ['a1', 'a2']:
                item[f'{criterion}_{rater}'] = False
                item[f'{column}_{rater}'] = f'{rater}: {criterion}'
        blocks = export.row_disagreements(pd.Series(item))
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[2][0], 'Direction')
        self.assertEqual(blocks[2][1], 'a1: direcao_relacao_correta')

    def test_whitespace_and_unicode_equivalence_do_not_create_disagreement(self):
        item = row()
        column = export.CORRECTION_FIELDS['entidade_correta']
        item['entidade_correta_a1'] = item['entidade_correta_a2'] = False
        item[f'{column}_a1'], item[f'{column}_a2'] = '  São\nPaulo ', 'Sa\u0303o Paulo'
        self.assertEqual(export.row_disagreements(pd.Series(item)), [])
        item[f'{column}_a2'] = 'são paulo'
        self.assertEqual(export.correction_comparisons(pd.Series(item))[0]['status'], 'different')

    def test_missing_suggestions_are_not_competing_corrections(self):
        item = row()
        column = export.CORRECTION_FIELDS['predicado_correto']
        item['predicado_correto_a1'] = item['predicado_correto_a2'] = False
        item[f'{column}_a1'] = 'kg:locatedIn'
        self.assertEqual(export.row_disagreements(pd.Series(item)), [])
        self.assertEqual(export.correction_comparisons(pd.Series(item))[0]['status'], 'only_a1')

    def test_incomplete_ratings_do_not_become_approvals(self):
        item = row()
        item['fidelidade_semantica_a1'] = pd.NA
        item['predicado_correto_a2'] = False
        self.assertEqual(export.row_disagreements(pd.Series(item)), [])
        self.assertIsNone(export.ratings(pd.Series(item), 'a1')['fidelidade_semantica'])

    def test_unresolved_marker_preserves_a_blank_other_decision(self):
        item = row()
        column = export.CORRECTION_FIELDS['entidade_correta']
        item[f'{column}_a1'] = 'não determinada'
        self.assertEqual(export.row_disagreements(pd.Series(item)),
                         [('Entity', 'não determinada', '')])
        item[f'{column}_a2'] = 'não determinada'
        self.assertEqual(export.row_disagreements(pd.Series(item)), [])

    def test_empty_selection_still_writes_the_required_headers(self):
        result = export.export_rows(pd.DataFrame([row()]))
        self.assertTrue(result.empty)
        self.assertEqual(result.columns.tolist(), export.BASE_COLUMNS + [f'{c}_1' for c in export.BLOCK_COLUMNS])

    def test_each_block_has_an_empty_manual_decision_after_a2(self):
        item = row()
        item['predicado_correto_a2'] = item['fidelidade_semantica_a2'] = False
        result = export.export_rows(pd.DataFrame([item]))
        self.assertEqual(result.columns.tolist(), export.BASE_COLUMNS + [
            'disagreement_type_1', 'a1_decision_1', 'a2_decision_1', 'decision_1',
            'disagreement_type_2', 'a1_decision_2', 'a2_decision_2', 'decision_2'])
        self.assertEqual(result.loc[0, 'decision_1'], '')
        self.assertEqual(result.loc[0, 'decision_2'], '')
        self.assertEqual(result.loc[0, 'a2_decision_2'], 'FALSE')

    def test_csv_roundtrip_preserves_commas_quotes_newlines_and_unicode(self):
        item = row()
        item['predicado_correto_a2'] = False
        expected = export.export_rows(pd.DataFrame([item]))
        with TemporaryDirectory() as directory:
            output = Path(directory)/'disagreements.csv'
            export.write_export(expected, output)
            actual = pd.read_csv(output, encoding='utf-8-sig', dtype=str, keep_default_na=False)
            pd.testing.assert_frame_equal(expected, actual, check_dtype=False)
            self.assertTrue(output.read_bytes().startswith(b'\xef\xbb\xbf'))
            original = output.read_bytes()
            with self.assertRaises(FileExistsError):
                export.write_export(expected.iloc[:0], output)
            self.assertEqual(output.read_bytes(), original)
            with self.assertRaises(ValueError):
                export.write_export(expected, output.with_suffix('.json'))

    def test_cli_rejects_bad_output_before_loading_sources(self):
        with patch.object(export, 'build_export') as build, redirect_stderr(io.StringIO()):
            self.assertEqual(export.main(['--output', 'invalid.json']), 1)
            build.assert_not_called()

    def test_build_aligns_by_key_excludes_labels_and_preserves_input(self):
        columns = ['Identifier', 'tripla_original', 'Texto_Original', *export.CRITERIA,
                   *export.CORRECTION_FIELDS.values()]
        original = row()
        frames = []
        for rater in ['a1', 'a2']:
            record = {c: original[c] if c in export.KEY else original[f'{c}_{rater}'] for c in columns}
            record['predicado_correto'] = rater == 'a1'
            label = {**record, 'tripla_original': 'wd:Q1 rdfs:label "Name" .'}
            frames.append(pd.DataFrame([record, label] if rater == 'a1' else [label, record]))
        before = [frame.copy(deep=True) for frame in frames]
        with patch.object(export.review, 'load_review', side_effect=[(f,{}) for f in frames]), \
             patch.object(export.review, 'file_sha256', return_value='unchanged'):
            result = export.build_export(Path('a1.xlsx'), Path('a2.xlsx'))
        self.assertEqual(len(result), 1)
        for a, b in zip(frames, before):
            pd.testing.assert_frame_equal(a, b)


if __name__ == '__main__':
    unittest.main()
