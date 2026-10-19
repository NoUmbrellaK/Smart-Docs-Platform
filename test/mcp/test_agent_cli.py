import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AgentCliTests(unittest.TestCase):
    def test_live_module_discovery_is_opt_in_and_never_starts_database(self):
        result=subprocess.run([sys.executable,"-B","-m","unittest","discover","-s","test/e2e",
            "-p","m3_deepseek_test.py","-v"],env={"PATH":"/usr/bin:/bin","PYTHONPATH":os.environ["PYTHONPATH"]},
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("Ran 1 test",result.stderr); self.assertIn("skipped=1",result.stderr)

    def test_outcome_summary_excludes_untrusted_extras_and_error_text(self):
        from smartdocs import agent_cli
        self.assertTrue(hasattr(agent_cli,"safe_outcome"),"outcome allowlist missing")
        self.assertEqual(agent_cli.safe_outcome({"state":"completed","result":{"secret":"text"},"error_code":"secret"}),{"state":"completed"})
        self.assertEqual(agent_cli.safe_outcome({"state":"failed","error_code":"raw secret"}),
                         {"state":"failed","error_code":"dependency_failed"})
        self.assertEqual(agent_cli.safe_outcome({"state":"secret"}),{"state":"unknown","error_code":"dependency_failed"})

    def test_invalid_context_and_missing_key_fail_without_traceback_or_echo(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.agent_cli"),"trusted Agent entry missing")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); context=root/"context.json"; audit=root/"audit.jsonl"
            context.write_text(json.dumps({"backend_port":1,"origin":"http://127.0.0.1:1",
                "session_token":"f"*64,"project_id":"b"*32,"task_id":"a"*32,"scope":"task_versions"}))
            context.chmod(0o600)
            env={"PATH":"/usr/bin:/bin","PYTHONPATH":os.environ["PYTHONPATH"]}
            command=[sys.executable,"-B","-m","smartdocs.agent_cli","--context",str(context),"--audit",str(audit)]
            result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,2)
            self.assertEqual(json.loads(result.stdout),{"state":"not_started","error_code":"configuration_error"})
            self.assertEqual(result.stderr,""); self.assertFalse(audit.exists())
            context.chmod(0o644); env["DEEPSEEK_API_KEY"]="do-not-echo-key"
            result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,2); self.assertNotIn("do-not-echo-key",result.stdout+result.stderr)
            self.assertFalse(audit.exists())

    def test_waiting_outcome_keeps_execution_error_instead_of_success(self):
        from smartdocs.agent_cli import safe_outcome
        self.assertEqual(safe_outcome({'state':'waiting_execution','error_code':'scope_changed','secret':'raw'}),
                         {'state':'waiting_execution','error_code':'scope_changed'})

    def test_provider_failure_summary_preserves_only_known_codes(self):
        from smartdocs.agent_cli import safe_outcome
        for code in ('provider_auth', 'provider_balance', 'provider_rate_limit', 'provider_unavailable', 'provider_rejected'):
            with self.subTest(code=code):
                self.assertEqual(safe_outcome({'state':'failed', 'error_code':code, 'body':'SECRET'}),
                                 {'state':'failed', 'error_code':code})
        self.assertEqual(safe_outcome({'state':'failed', 'error_code':'provider_SECRET'}),
                         {'state':'failed', 'error_code':'dependency_failed'})
