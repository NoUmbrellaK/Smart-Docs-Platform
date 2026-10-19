"""One-off reproduction inside the disposable test-with-mysql database only."""
import os
import subprocess

socket = os.environ["SMARTDOCS_MYSQL_SOCKET"]
assert socket.startswith("/tmp/smartdocs-mysql.")
sql = ("CREATE TRIGGER checklist_audit_failure BEFORE INSERT ON audit_records FOR EACH ROW BEGIN "
       "IF NEW.action='handover.checklist.set' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test fault'; END IF; END")
result = subprocess.run(["mysql", "--protocol=SOCKET", "--socket="+socket, "--user=root",
                         "smart_docs_test", "--execute", sql], capture_output=True, text=True, timeout=10)
print(result.stderr, flush=True)
assert result.returncode != 0 and "ERROR 1064" in result.stderr
print("PASS reproduced missing DELIMITER syntax error; no trigger created", flush=True)
