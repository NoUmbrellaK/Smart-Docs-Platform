"""Only provider transport is scripted; prove body observation is not metadata."""
import json
from pathlib import Path
import unittest

from a08_observe import observe
from benchmark_review import read_private
from m3_agent_http_test import tool
from m3_fixed_inputs_test import pair
from m5_benchmark_execute_test import ExecuteE2E

OUTPUT = Path('/tmp/smartdocs-m5-a08-offline.iVIcOP')


class ObserverTest(ExecuteE2E):
    def run_observation(self, body):
        directory = OUTPUT / self._testMethodName
        directory.mkdir(mode=0o700)
        with observe(directory):
            prepared = self.setup_case('A08')
            source = prepared['mapping']['injection@1']

            def respond(request):
                if len(self.requests) == 1:
                    return tool('read_document_version', {**pair(source), **({'range': {}} if body else {})})
                return tool('create_archive_plan', {'expected_revision': 0,
                    'policy_revision': prepared['policy']['revision'], 'items': [{**pair(source),
                        'source_directory_id': source['directory_id'], 'target_directory_id': None,
                        'reason': 'Offline protocol fixture only; no classification quality claim'}]}, sequence=2)

            with self.provider(respond):
                snapshot = self.invoke(prepared, directory / 'initial')
            self.assertTrue(snapshot['capture']['complete'])
            self.assertEqual(snapshot['run_result']['state'], 'waiting_confirmation')
            self.assertIsNone(snapshot['archive_plan']['decision'])
        wire = [json.loads(line) for line in (directory/'wire.jsonl').read_text().splitlines()]
        self.assertEqual([r['injection_body_present'] for r in wire], [False, body])

    def test_body_is_observed(self):
        self.run_observation(True)

    def test_metadata_is_not_body(self):
        self.run_observation(False)


if __name__ == '__main__':
    names = ('test_body_is_observed','test_metadata_is_not_body')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ObserverTest(name) for name in names))
    assert result.wasSuccessful()
    for name in names:
        probe = read_private(OUTPUT/name/'probe.json')
        assert probe['state']['state'] == 'waiting_confirmation'
        assert probe['counts'] == {'archive_plans':1,'archive_decisions':0}
        assert probe['files_before'] == probe['files_after']
        assert probe['download']['http'] == 200
        assert probe['download']['actual_sha256'] == probe['download']['expected_sha256']
    print('PASS body versus metadata observation; both pre-cleanup probes retain waiting state, one plan, no decision or move; no real provider')
