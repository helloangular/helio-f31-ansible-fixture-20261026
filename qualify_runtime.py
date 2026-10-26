#!/usr/bin/env python3
"""Qualify only F31's controller preflight: clean / added file / restored.

No deployment, sign-in, GitHub run or acceptance execution. Logs are outside git.
The harmless owned collection canary is always removed. Use the shared test lock.
"""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent
PRIVATE = Path("/tmp/f31-runtime-find-qualification-20261026")


def main():
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    command = ["/usr/local/bin/with-gradle-lock", str(Path.home() / ".helio-f31-fixture/venv/bin/ansible-playbook"),
               "-i", "inventory.yml", "deploy-war.yml", "--limit", "test", "--tags", "runtime_preflight"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               ANSIBLE_COLLECTIONS_PATH=str(Path.home() / ".helio-f31-fixture/collections"))

    def run(name):
        start = time.monotonic()
        with open(PRIVATE / (name + ".log"), "w") as output:
            process = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                     env=env, timeout=180)
        return {"exit": process.returncode, "seconds": round(time.monotonic() - start, 3)}

    results = {"clean": run("clean")}
    if results["clean"]["exit"] != 0:
        raise RuntimeError("The clean preflight failed; inspect its private log")
    canary = Path.home() / ".helio-f31-fixture/collections/ansible_collections/.f31-unpinned-canary-20261026.txt"
    if canary.exists():
        raise RuntimeError("Refusing to replace an existing canary")
    try:
        canary.write_text("Harmless F31 exact-set test. Not executable.\n")
        results["added_file"] = run("added-file")
        text = (PRIVATE / "added-file.log").read_text()
        if (results["added_file"]["exit"] == 0
                or "TASK [Require the exact bounded runtime file set]" not in text
                or "TASK [Refuse changed file content or type]" in text
                or "TASK [Download and verify" in text):
            raise RuntimeError("The added-file refusal did not match the exact-set boundary")
    finally:
        canary.unlink(missing_ok=True)
    # Harmless package-manager metadata, never imported or executed. Preserve
    # bytes and timestamps even if the bounded preflight invocation fails.
    metadata = Path.home() / ".helio-f31-fixture/venv/lib/python3.12/site-packages/ansible_core-2.19.3.dist-info/INSTALLER"
    original, timestamps = metadata.read_bytes(), metadata.stat()
    try:
        metadata.write_bytes(b"f31-preflight-hash-canary\n")
        results["changed_file"] = run("changed-file")
        text = (PRIVATE / "changed-file.log").read_text()
        if (results["changed_file"]["exit"] == 0
                or "TASK [Refuse changed file content or type]" not in text
                or "TASK [Read pinned interpreter link targets]" in text
                or "TASK [Download and verify" in text):
            raise RuntimeError("The changed-file refusal did not match the checksum boundary")
    finally:
        metadata.write_bytes(original)
        os.utime(metadata, ns=(timestamps.st_atime_ns, timestamps.st_mtime_ns))
    results["restored"] = run("restored")
    if results["restored"]["exit"] != 0:
        raise RuntimeError("The restored preflight failed")
    (PRIVATE / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
