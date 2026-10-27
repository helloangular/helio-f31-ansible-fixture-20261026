#!/usr/bin/env python3
"""Builds the owned F31 WAR/publication fixture. Secrets: NEXUS_* env only.

No acceptance results are produced. Teardown is documented in README.md.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parent
REPOSITORY = "helloangular/helio-f31-ansible-fixture-20261026"
WORKFLOW = ".github/workflows/f31.yml"


def version(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) or re.search(
            r"(?i)SNAPSHOT|^(latest|release|head|main|master)$", value):
        raise ValueError("An immutable artifact version is required")
    return value


def target(value):
    if value not in ("test", "uat", "prod"):
        raise ValueError("The target must be test, uat or prod")
    return value


def check_files(expected, *, followed_paths=()):
    for name, digest in expected.items():
        p = Path(name)
        if (p.is_symlink() and name not in followed_paths) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError("Installed runtime differs from its pinned files")


def publication(context, artifact_version, uri, digest):
    version(artifact_version)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("SHA-256 required")
    if not uri.startswith("https://localhost:9444/repository/helio-releases/"):
        raise ValueError("Owned Nexus boundary required")
    return dict(schema_version=1, kind="catalog_publication", provider_origin="https://api.github.com",
                workflow_path=WORKFLOW, version=artifact_version, artifact_uri=uri,
                artifact_digest="sha256:" + digest, **context)


def github(path):
    req = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + path,
                                 headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def build(artifact_version):
    version(artifact_version)
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("This builder is restricted to its owned repository")
    subprocess.run(["mvn", "--batch-mode", "--no-transfer-progress", "-f", str(ROOT / "app/pom.xml"),
                    "-Drevision=" + artifact_version, "package"], check=True,
                   env=dict(os.environ, MAVEN_OPTS="-Xmx256m"))
    war = ROOT / "app/target/app.war"
    payload = war.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    uri = "https://localhost:9444/repository/helio-releases/io/helio/f31/app/" + artifact_version + "/app-" + artifact_version + ".war"
    auth = base64.b64encode((os.environ["NEXUS_USERNAME"] + ":" + os.environ["NEXUS_PASSWORD"]).encode()).decode()
    request = urllib.request.Request(uri, data=payload, method="PUT",
                                     headers={"Authorization": "Basic " + auth,
                                              "Content-Type": "application/java-archive"})
    ctx = ssl.create_default_context(cafile=str(Path.home() / ".helio-tomcat/tls/local-tls/cert.pem"))
    with urllib.request.urlopen(request, context=ctx, timeout=30) as response:
        if response.status not in (200, 201, 204):
            raise ValueError("Nexus publication refused")
    run_id, attempt = int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"])
    run = github("/actions/runs/" + str(run_id))
    jobs = github("/actions/runs/" + str(run_id) + "/attempts/" + str(attempt) + "/jobs?per_page=100")["jobs"]
    matches = [j for j in jobs if j["name"] == os.environ.get("F31_BUILD_JOB_NAME", "build")]
    if len(matches) != 1:
        raise ValueError("A unique build job is required")
    document = publication(dict(repository_id=run["repository"]["id"], workflow_id=run["workflow_id"],
                                source_commit=run["head_sha"], run_id=run_id, run_attempt=attempt,
                                job_id=matches[0]["id"]), artifact_version, uri, digest)
    (ROOT / "publication.json").write_text(json.dumps(document, sort_keys=True, separators=(",", ":")))
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("artifact_uri=" + uri + "\nartifact_digest=sha256:" + digest + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    build(args.version)


if __name__ == "__main__":
    main()
