#!/usr/bin/env python3
"""Mirror reviewed fixture source, then atomically push commit and workflow tag.

Only the explicit owned repository is accepted; secrets and runtime output are
excluded. Run a credential scan before committing, separately in both repos.
"""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
DEST = Path.home() / ".helio-f31-fixture/repository"
REMOTE = "https://github.com/helloangular/helio-f31-ansible-fixture-20261026.git"
EXCLUDED = {".git", "__pycache__", "target", "evidence"}


def run(*args):
    return subprocess.check_output(args, cwd=DEST, text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true", required=True)
    args = parser.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    if not (DEST / ".git").exists():
        run("git", "init", "-b", "main")
        run("git", "remote", "add", "origin", REMOTE)
    if run("git", "remote", "get-url", "origin") != REMOTE:
        raise ValueError("Refusing another repository")
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if p.is_file() and not any(part in EXCLUDED for part in rel.parts) and p.name not in ("publication.json", "deployment.json"):
            dest = DEST / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    print("Prepared owned source mirror; scan before commit and atomic branch/tag push:", DEST)


if __name__ == "__main__":
    main()
