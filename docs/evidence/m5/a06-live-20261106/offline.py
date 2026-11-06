"""Exercise test-operator rejection against real HTTP/SQL, without a model."""
from pathlib import Path
import sys
import unittest

from a06_observe import observe
from benchmark_review import read_private
from m5_benchmark_capture_test import CaptureE2E

output = Path(sys.argv[1])
with observe(output):
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        CaptureE2E('test_capture_archive_preview_never_confirms_or_moves')]))
assert result.wasSuccessful()
probe = read_private(output)
# Removing the operator's rejection must leave zero decisions and fail here.
assert probe['counts'] == {'archive_plans': 1, 'archive_decisions': 1}, probe['counts']
assert probe['plan_before']['state'] == 'preview' and probe['plan_before']['decision'] is None
assert probe['plan_after']['state'] == 'rejected' and probe['plan_after']['decision'] == 'reject'
assert probe['plan_after']['operation_id'] is None and not probe['plan_after']['executable']
assert probe['decision_http'] == 200
assert probe['confirm_count'] == probe['execute_count'] == 0
assert probe['files_before'] == probe['files_before_reject'] == probe['files_after']
assert len(probe['downloads']) == 2
assert all(x['http'] == 200 and x['expected_sha256'] == x['actual_sha256'] for x in probe['downloads'])
print('PASS A06 test-operator rejection: one reject, no confirm/execute/move, original content unchanged; no provider')
