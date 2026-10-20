"""Offline verification of documented units; never installs or starts services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def blocks(path):
    return re.findall(r"^```ini\n(.*?)^```", (ROOT / path).read_text(), re.M | re.S)


def option(unit, key, value):
    pattern = rf"^{re.escape(key)}=.*$"
    if re.search(pattern, unit, re.M):
        updated, count = re.subn(pattern, f"{key}={value}", unit, flags=re.M)
        assert count == 1, f"ambiguous replacement: {key}"
        return updated
    return unit.replace("[Service]\n", f"[Service]\n{key}={value}\n", 1)


def documented_units(resident):
    cpp, = blocks("docs/deployment/m1-single-host.md")
    _, parser = blocks("docs/deployment/m2-local-worker.md")
    socket, encoder, slice_unit, vector_patch, resident_unit = blocks("docs/deployment/m2-vector-worker.md")
    search, = blocks("docs/deployment/m2-hybrid-search.md")
    agent, = blocks("docs/api/m3-agent-http.md")
    # The vector manual explicitly instructs both workers to share this slice.
    parser = option(parser, "Slice", "smartdocs-processing.slice")
    vector = parser
    for line in vector_patch.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "Environment":
            vector = vector.replace("[Service]\n", "[Service]\n" + line + "\n", 1)
        else:
            vector = option(vector, key, value)
    units = {"smart-docs.service": cpp, "smartdocs-worker.service": parser,
             "smartdocs-vector.service": vector, "smartdocs-search.service": search,
             "smartdocs-agent.service": agent, "smartdocs-processing.slice": slice_unit}
    if resident:
        for name in ("smartdocs-vector.service", "smartdocs-search.service"):
            units[name] = units[name].replace("smartdocs-encoder.socket", "smartdocs-encoder-resident.service")
            units[name] = units[name].replace("/run/smartdocs-encoder/encoder.sock",
                                            "/run/smartdocs-encoder-resident/encoder.sock")
        units["smartdocs-encoder-resident.service"] = resident_unit
    else:
        units.update({"smartdocs-encoder.socket": socket, "smartdocs-encoder@.service": encoder})
    return units


def verify(units):
    with tempfile.TemporaryDirectory(prefix="smartdocs-unit-verify-") as temporary:
        directory = Path(temporary)
        for name, content in units.items():
            (directory / name).write_text(content)
        result = subprocess.run(
            ["systemd-analyze", "--man=no", "--generators=no", "verify",
             *[str(directory / name) for name in units]],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "SYSTEMD_UNIT_PATH": temporary + ":"},
            capture_output=True, text=True, timeout=30)
        diagnostic = (result.stdout + result.stderr).replace(temporary, "<temporary-unit-directory>")
        # Keep host warnings visible, but do not confuse unrelated installed units with these templates.
        external = re.compile(r"^(?:/(?:usr/)?lib/systemd/system/|/etc/systemd/system/|"
                              r"Configuration file /etc/systemd/system/)")
        project_diagnostic = "\n".join(line for line in diagnostic.splitlines() if not external.match(line))
        return {"exit_code": result.returncode, "diagnostic": diagnostic,
                "project_diagnostic": project_diagnostic}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--parser-python", required=True, type=Path)
    parser.add_argument("--mcp-python", required=True, type=Path)
    args = parser.parse_args()
    replacements = {"/opt/smart-docs/bin/server": args.server,
                    "/opt/smart-docs-venv/bin/python": args.parser_python,
                    "/opt/smart-docs-mcp-venv/bin/python": args.mcp_python}
    for target in replacements.values():
        assert target.is_absolute() and os.access(target, os.X_OK), "local executable required"
        assert not re.search(r"[\s%\"\\]", str(target)), "simple absolute executable path required"
    print(subprocess.check_output(["systemd-analyze", "--version"], text=True).splitlines()[0])
    results = []
    for resident in (False, True):
        units = documented_units(resident)
        raw = verify(units)
        relocated = {}
        for name, content in units.items():
            for source, target in replacements.items():
                # Only the executable token changes. Arguments and sandbox/dependency settings stay intact.
                content = content.replace("ExecStart=" + source, "ExecStart=" + str(target))
            relocated[name] = content
        checked = verify(relocated)
        record = {"mode": "resident" if resident else "socket", "units": len(units),
                  "original_paths": raw, "relocated_executables": checked,
                  "prepared_sha256": {name: hashlib.sha256(text.encode()).hexdigest()
                                      for name, text in relocated.items()}}
        print(json.dumps(record, sort_keys=True), flush=True)
        assert checked["exit_code"] == 0 and not checked["project_diagnostic"], "prepared unit verification failed"
        # A missing dependency must not become a false green in this verification harness.
        broken = dict(relocated)
        broken["smartdocs-search.service"] = broken["smartdocs-search.service"].replace(
            "Requires=smart-docs.service", "Requires=smartdocs-deliberately-missing.service")
        denied = verify(broken)
        assert denied["exit_code"] != 0 and "smartdocs-deliberately-missing.service" in denied["diagnostic"]
        print(json.dumps({"mode": record["mode"], "negative_missing_dependency": denied}), flush=True)
        broken = dict(relocated)
        broken["smartdocs-search.service"] = broken["smartdocs-search.service"].replace(
            "[Service]\n", "[Service]\nSmartDocsInvalidSetting=yes\n")
        denied = verify(broken)
        assert "SmartDocsInvalidSetting" in denied["project_diagnostic"], "project warning was hidden"
        print(json.dumps({"mode": record["mode"], "negative_unknown_setting": denied}), flush=True)
        results.append(record["mode"])
    print(json.dumps({"verified_configurations": results, "services_started": 0,
                      "credentials_read": False, "production_deployment": "not_run"}))


if __name__ == "__main__":
    main()
