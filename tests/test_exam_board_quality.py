"""Small opt-in semantic check using synthetic, text-grounded examples."""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ExamBoard import adjudicate as board, llm_api

FIXTURE = Path(__file__).with_name('fixtures') / 'exam_board_quality.json'


class QualityFixtureTests(unittest.TestCase):
    def test_controlled_examples_have_source_evidence_and_expected_alternatives(self):
        examples = json.loads(FIXTURE.read_text(encoding='utf-8'))['examples']
        self.assertEqual(len(examples), 3)
        for example in examples:
            case = example['case']
            packet = board.build_packet(case, board.prompt_type(case))
            self.assertTrue(packet['source_text'])
            self.assertIn(example['expected_selected'], packet['alternatives'])
            values = board.selected_values(case, example['expected_selected'], example['expected_correction'])
            self.assertTrue(values['approved_triples'])


@unittest.skipUnless(os.getenv('EXAMBOARD_LIVE_CHECK') == '1', 'Opt-in Ollama quality check')
class LiveQualityTests(unittest.TestCase):
    def test_model_on_controlled_examples(self):
        report = {'model': llm_api.MODEL, 'synthetic_examples': True, 'results': []}
        prompts = board.read_prompts()
        output = board.OUTPUT_DIR / 'quality_check.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        for example in json.loads(FIXTURE.read_text(encoding='utf-8'))['examples']:
            with self.subTest(example=example['name']):
                case = example['case']
                packet = board.build_packet(case, board.prompt_type(case))
                request = llm_api.build_request(prompts[board.prompt_type(case)], packet)
                entry = {'name': example['name'], 'expected_selected': example['expected_selected'],
                         'expected_correction': example['expected_correction'], 'request': request}
                report['results'].append(entry)
                board.write_json(output, report)
                raw = llm_api.complete(request)
                entry['raw'] = raw
                board.write_json(output, report)
                result = llm_api.parse_response(raw)
                entry['result'] = result
                entry['passed'] = (result['selected'] == example['expected_selected'] and
                                   result['approve_correction'] == example['expected_correction'])
                board.write_json(output, report)
                print(f"Quality check {example['name']}: {entry['passed']}", flush=True)
                self.assertTrue(entry['passed'])


if __name__ == '__main__':
    unittest.main()
