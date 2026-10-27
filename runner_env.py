#!/usr/bin/env python3
"""Start only the owner-invoked foreground runner with reproducible overrides.

No credentials are read or printed. This process does not register a runner,
create a service, or start a daemon. Ctrl-C/teardown remain in the caller script.
"""
import os
from pathlib import Path
import sys


def sanitized(environment):
    blocked = {"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "CDPATH", "LD_PRELOAD", "VIRTUAL_ENV"}
    return {key: value for key, value in environment.items()
            if key not in blocked and not key.startswith(("ANSIBLE_", "PYTHON", "DYLD_", "GH_", "GITHUB_"))}


def main():
    runner = Path.home() / ".helio-actions-runner-f31"
    if Path.cwd().resolve() != runner.resolve() or sys.argv[1:] != ["./run.sh"]:
        raise SystemExit("Refusing another runner or command")
    saved = runner / ".env"
    if saved.is_file():
        # Inspect names in-process only. Saved values are private and never logged.
        names = {line.partition("=")[0] for line in saved.read_text().splitlines() if "=" in line}
        if any(name not in sanitized({name: ""}) for name in names):
            raise SystemExit("Owned runner has a saved execution override; review it before starting")
    os.execve(str(runner / "run.sh"), ["./run.sh"], sanitized(os.environ))


if __name__ == "__main__":
    main()
