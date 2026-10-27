#!/usr/bin/env python3
"""Build F31's pinned executable/input graph and static three-step deploy jobs.

Run after the dedicated venv/collection is installed. No credentials are read.
The manifest excludes its own blob identity to avoid a self-reference; registration
must pin the manifest blob from the externally selected immutable commit.
"""
import hashlib
import configparser
import argparse
import re
import compileall
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
VENV = HOME / ".helio-f31-fixture/venv"
COLLECTIONS = HOME / ".helio-f31-fixture/collections"
CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
UPLOAD = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"


def save(path, value):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def task(name, module, args, **rest):
    return dict(name=name, **{module: args}, **rest)


def generate(template_commit):
    if not re.fullmatch(r"[0-9a-f]{40}", template_commit):
        raise ValueError("An immutable template commit is required")
    import ansible
    collection = COLLECTIONS / "ansible_collections/community/docker"
    collection_roots = sorted((COLLECTIONS / "ansible_collections").glob("*/*/MANIFEST.json"))
    runtime_roots = [VENV, COLLECTIONS / "ansible_collections"]
    # Include bytecode rather than excluding executable caches. Compile it once
    # during fixture construction; deployment jobs cannot write new caches.
    for root in runtime_roots[:2]:
        if not compileall.compile_dir(str(root), quiet=2):
            raise ValueError("The isolated runtime could not be precompiled")
    entries = sorted(p for root in runtime_roots for p in root.rglob("*"))
    if len(entries) > 8192:
        raise ValueError("The reviewed runtime exceeds its fixed entry budget")
    kinds = {str(p): "link" if p.is_symlink() else "directory" if p.is_dir() else "file" for p in entries}
    links = [{"path": str(p), "target": os.readlink(p)} for p in entries if p.is_symlink()]
    paths = [p for p in entries if p.is_file() and not p.is_symlink()]
    docker = shutil.which("docker")
    if not docker:
        raise ValueError("The pinned Docker CLI must be installed")
    external_set = {Path(sys.executable).resolve(), Path(docker).resolve(), HOME / ".helio-tomcat/tls/local-tls/cert.pem"}
    entry_set = {str(p) for p in entries}
    for link in links:
        target = Path(os.path.normpath(str(Path(link["path"]).parent / link["target"])))
        if str(target) not in entry_set:
            if not target.is_file():
                raise ValueError("An external runtime link has no hashable file target")
            external_set.add(target)
    external = sorted(external_set)
    paths = sorted(set(paths + external))
    pins = [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
    save("recipe/runtime-files.json", {"helio_runtime_files": pins, "helio_runtime_entries": [str(p) for p in entries],
                                     "helio_runtime_kinds": kinds,
                                     "helio_runtime_regular_entries": [p for p, kind in kinds.items() if kind == "file"],
                                     "helio_runtime_directory_entries": [p for p, kind in kinds.items() if kind == "directory"],
                                     "helio_runtime_links": links, "helio_runtime_external": [p for p in pins if Path(p["path"]) in external]})
    inventory = {"all": {"children": {"helio_target": {"hosts": {
        t: {"ansible_host": "helio-f31-" + t, "ansible_connection": "community.docker.docker",
            "ansible_python_interpreter": "/usr/bin/python3"} for t in ("test", "uat", "prod")}}}}}
    save("inventory.yml", inventory)
    # The declared runtime roots must also be the effective plugin search set.
    # A pinned playbook alone does not close user/system Ansible configuration.
    package = Path(ansible.__file__).resolve().parent
    plugin_kinds = ("action", "become", "cache", "callback", "cliconf", "connection", "filter", "httpapi", "inventory", "lookup", "netconf", "strategy", "terminal", "test", "vars")
    plugin_paths = {kind: str(package / "plugins" / kind) for kind in plugin_kinds}
    defaults = {kind + "_plugins": path for kind, path in plugin_paths.items()}
    defaults.update(library=str(package / "modules"), module_utils=str(package / "module_utils"),
                    roles_path=str(VENV / "NO_ROLES"), collections_path=str(COLLECTIONS),
                    collections_scan_sys_path="False", stdout_callback="default", callbacks_enabled="ansible.builtin.default")
    config = configparser.ConfigParser(interpolation=None)
    config["defaults"] = defaults
    with (ROOT / "recipe/ansible.cfg").open("w") as output:
        config.write(output)
    runtime_env = {"ANSIBLE_" + kind.upper() + "_PLUGINS": path for kind, path in plugin_paths.items()}
    runtime_env.update(ANSIBLE_CONFIG="${{ github.workspace }}/recipe/ansible.cfg",
                       ANSIBLE_LIBRARY=defaults["library"], ANSIBLE_MODULE_UTILS=defaults["module_utils"],
                       ANSIBLE_ROLES_PATH=defaults["roles_path"], ANSIBLE_COLLECTIONS_PATH=str(COLLECTIONS),
                       ANSIBLE_COLLECTIONS_SCAN_SYS_PATH="false", ANSIBLE_STDOUT_CALLBACK="default", ANSIBLE_CALLBACKS_ENABLED="ansible.builtin.default",
                       PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="",
                       PATH=str(Path(docker).resolve().parent) + ":/usr/bin:/bin")
    pre = [task("Verify exact runtime versions", "ansible.builtin.assert", {"that": ["ansible_version.full == '2.19.3'"]}, delegate_to="localhost", run_once=True),
           task("Enumerate and hash only the reviewed controller roots", "ansible.builtin.find", {"paths": [str(p) for p in runtime_roots], "file_type": "any", "hidden": True, "recurse": True, "follow": False, "get_checksum": True, "checksum_algorithm": "sha256", "limit": 8193}, register="helio_runtime_found", delegate_to="localhost", run_once=True, no_log=True),
           task("Require the exact bounded runtime file set", "ansible.builtin.assert", {"that": ["helio_runtime_found.skipped_paths | length == 0", "helio_runtime_found.matched <= 8192", "helio_runtime_found.files | map(attribute='path') | sort == helio_runtime_entries | sort"]}, delegate_to="localhost", run_once=True, no_log=True),
           task("Refuse changed file content or type", "ansible.builtin.assert", {"that": [
               "helio_runtime_found.files | selectattr('isreg') | map(attribute='path') | sort == helio_runtime_regular_entries | sort",
               "helio_runtime_found.files | selectattr('isdir') | map(attribute='path') | sort == helio_runtime_directory_entries | sort",
               "helio_runtime_found.files | selectattr('islnk') | map(attribute='path') | sort == helio_runtime_links | map(attribute='path') | sort",
               "helio_runtime_found.files | selectattr('isreg') | map(attribute='checksum') | list == helio_runtime_found.files | selectattr('isreg') | map(attribute='path') | map('extract', helio_runtime_checksums) | list"]}, delegate_to="localhost", run_once=True, no_log=True),
           task("Read pinned interpreter link targets", "ansible.builtin.stat", {"path": "{{ item.path }}", "follow": False, "get_checksum": False, "get_mime": False, "get_attributes": False}, loop="{{ helio_runtime_links }}", register="helio_runtime_link_checks", delegate_to="localhost", run_once=True, no_log=True),
           task("Refuse changed interpreter links", "ansible.builtin.assert", {"that": ["item.stat.islnk", "item.stat.lnk_target == item.item.target"]}, loop="{{ helio_runtime_link_checks.results }}", delegate_to="localhost", run_once=True, no_log=True),
           task("Hash the external Python and Docker executables", "ansible.builtin.stat", {"path": "{{ item.path }}", "follow": True, "checksum_algorithm": "sha256", "get_mime": False, "get_attributes": False}, loop="{{ helio_runtime_external }}", register="helio_runtime_checks", delegate_to="localhost", run_once=True, no_log=True),
           task("Refuse external executable drift", "ansible.builtin.assert", {"that": ["item.stat.isreg", "item.stat.checksum == item.item.sha256"]}, loop="{{ helio_runtime_checks.results }}", delegate_to="localhost", run_once=True, no_log=True)]
    for item in pre:
        item["tags"] = ["runtime_preflight"]
    tasks = [
        task("Validate target and artifact inputs", "ansible.builtin.assert", {"that": ["inventory_hostname == helio_target", "helio_target in ['test', 'uat', 'prod']", "helio_artifact_digest is match('^sha256:[0-9a-f]{64}$')", "helio_artifact_uri is match('^https://localhost:9444/repository/helio-releases/[A-Za-z0-9/._-]+[.]war$')"]}),
        task("Prepare isolated controller download directory", "ansible.builtin.file", {"path": "{{ playbook_dir }}/evidence/{{ helio_target }}", "state": "directory", "mode": "0700"}, delegate_to="localhost"),
        task("Download and verify before copying to Tomcat", "ansible.builtin.get_url", {"url": "{{ helio_artifact_uri }}", "dest": "{{ playbook_dir }}/evidence/{{ helio_target }}/app.war", "checksum": "{{ helio_artifact_digest }}", "url_username": "{{ lookup('ansible.builtin.env', 'NEXUS_USERNAME') }}", "url_password": "{{ lookup('ansible.builtin.env', 'NEXUS_PASSWORD') }}", "validate_certs": True, "unredirected_headers": ["Authorization"], "mode": "0600", "timeout": 20}, delegate_to="localhost", no_log=True, environment={"SSL_CERT_FILE": str(HOME / ".helio-tomcat/tls/local-tls/cert.pem")}, tags=["fixture_download"]),
        task("Install only the verified WAR", "ansible.builtin.copy", {"src": "{{ playbook_dir }}/evidence/{{ helio_target }}/app.war", "dest": "/usr/local/tomcat/webapps/ROOT.war", "mode": "0644"}),
        task("Wait for the installed digest", "ansible.builtin.uri", {"url": "http://127.0.0.1:8080/artifact-digest", "return_content": True, "timeout": 5}, register="helio_digest_read", until="helio_digest_read.status == 200 and helio_digest_read.content == helio_artifact_digest", retries=45, delay=2),
        task("Verify the installed version", "ansible.builtin.uri", {"url": "http://127.0.0.1:8080/version", "return_content": True, "timeout": 5}, register="helio_version_read", until="helio_version_read.status == 200 and helio_version_read.content == helio_version", retries=15, delay=2),
        task("Read exact GitHub run identity", "ansible.builtin.uri", {"url": "https://api.github.com/repos/helloangular/helio-f31-ansible-fixture-20261026/actions/runs/{{ lookup('ansible.builtin.env', 'GITHUB_RUN_ID') }}", "headers": {"Authorization": "Bearer {{ lookup('ansible.builtin.env', 'GITHUB_TOKEN') }}"}, "timeout": 15}, register="helio_run", delegate_to="localhost", no_log=True),
        task("Read exact GitHub attempt jobs", "ansible.builtin.uri", {"url": "https://api.github.com/repos/helloangular/helio-f31-ansible-fixture-20261026/actions/runs/{{ lookup('ansible.builtin.env', 'GITHUB_RUN_ID') }}/attempts/{{ lookup('ansible.builtin.env', 'GITHUB_RUN_ATTEMPT') }}/jobs?per_page=100", "headers": {"Authorization": "Bearer {{ lookup('ansible.builtin.env', 'GITHUB_TOKEN') }}"}, "timeout": 15}, register="helio_jobs", delegate_to="localhost", no_log=True),
        task("Select this one deployment job", "ansible.builtin.set_fact", {"helio_matching_jobs": "{{ helio_jobs.json.jobs | selectattr('name', 'equalto', 'F31 catalog / ' ~ helio_target) | list }}"}),
        task("Require a unique job identity", "ansible.builtin.assert", {"that": ["helio_matching_jobs | length == 1"]}),
        task("Write deployment evidence on the controller", "ansible.builtin.copy", {"dest": "{{ playbook_dir }}/evidence/{{ helio_target }}/deployment.json", "mode": "0600", "content": "{{ helio_deployment_report | to_json }}"}, delegate_to="localhost", vars={"helio_deployment_report": {
            "schema_version": 1, "kind": "catalog_deployment", "evidence_profile": "reported_not_pinned", "provider_origin": "https://api.github.com", "repository_id": "{{ helio_run.json.repository.id }}", "workflow_id": "{{ helio_run.json.workflow_id }}", "workflow_path": ".github/workflows/f31.yml", "workflow_commit": "{{ helio_run.json.head_sha }}", "run_id": "{{ helio_run.json.id }}", "run_attempt": "{{ helio_run.json.run_attempt }}", "job_id": "{{ helio_matching_jobs[0].id }}", "environment_name": "{{ helio_target }}", "artifact_uri": "{{ helio_artifact_uri }}", "artifact_digest": "{{ helio_digest_read.content }}", "version": "{{ helio_version_read.content }}"}})
    ]
    save("deploy-war.yml", [{"name": "F31 pinned WAR deployment", "hosts": "helio_target", "gather_facts": False,
                            "vars_files": ["recipe/runtime-files.json"],
                            "vars": {"helio_runtime_checksums": "{{ dict(helio_runtime_files | map(attribute='path') | zip(helio_runtime_files | map(attribute='sha256'))) }}"},
                            "pre_tasks": pre, "tasks": tasks}])
    input_files = []
    for t in ("test", "uat", "prod"):
        name = "recipe/inputs-" + t + ".json"
        save(name, {"helio_target": t, "helio_version": "{{ lookup('ansible.builtin.env', 'HELIO_VERSION') }}", "helio_artifact_uri": "{{ lookup('ansible.builtin.env', 'HELIO_ARTIFACT_URI') }}", "helio_artifact_digest": "{{ lookup('ansible.builtin.env', 'HELIO_ARTIFACT_DIGEST') }}"})
        input_files.append(ROOT / name)
    save("recipe/inputs-catalog.json", {
        "helio_target": "{{ lookup('ansible.builtin.env', 'HELIO_TARGET') }}",
        "helio_version": "{{ lookup('ansible.builtin.env', 'HELIO_VERSION') }}",
        "helio_artifact_uri": "{{ lookup('ansible.builtin.env', 'HELIO_ARTIFACT_URI') }}",
        "helio_artifact_digest": "{{ lookup('ansible.builtin.env', 'HELIO_ARTIFACT_DIGEST') }}"})
    input_files.append(ROOT / "recipe/inputs-catalog.json")
    collection_sources = []
    for source in collection_roots:
        info = json.loads(source.read_text())["collection_info"]
        if not all(re.fullmatch(r"[a-z][a-z0-9_]*", info[key]) for key in ("namespace", "name")):
            raise ValueError("Invalid collection identity")
        dest = ROOT / "recipe/collections" / (info["namespace"] + "." + info["name"])
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("MANIFEST.json", "FILES.json"):
            shutil.copy2(source.parent / name, dest / name)
            collection_sources.append(dest / name)
    files = {str(p.relative_to(ROOT)): {"git_blob_sha": subprocess.check_output(["git", "hash-object", str(p)], cwd=ROOT, text=True).strip()} for p in [ROOT / "deploy-war.yml", ROOT / "inventory.yml", ROOT / "recipe/runtime-files.json", ROOT / "recipe/ansible.cfg"] + input_files + collection_sources}
    collections = []
    for p in collection_roots:
        info = json.loads(p.read_text())["collection_info"]
        collections.append({"fqcn": info["namespace"] + "." + info["name"], "version": info["version"], "manifest_sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "files_sha256": hashlib.sha256((p.parent / "FILES.json").read_bytes()).hexdigest()})
    manifest = {"schema": "helio_deploy_recipe_v1", "ansible_core": ansible.__version__, "python": {"version": sys.version.split()[0], "binary_sha256": hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest()},
                "collections": collections,
                "playbook": "deploy-war.yml", "inventory": "inventory.yml", "files": files, "connection_plugin": "community.docker.docker", "ansible_playbook": str(VENV / "bin/ansible-playbook"),
                "modules_used": ["ansible.builtin.assert", "ansible.builtin.copy", "ansible.builtin.file", "ansible.builtin.find", "ansible.builtin.get_url", "ansible.builtin.set_fact", "ansible.builtin.stat", "ansible.builtin.uri"],
                "runtime_preflight_modules": ["ansible.builtin.assert", "ansible.builtin.find", "ansible.builtin.stat"], "runtime_preflight_entry_limit": 8192}
    save("recipe/runtime-manifest.json", manifest)
    shared_inputs = {
        "version": {"required": True, "type": "string"},
        "helio_target_environment": {"type": "string", "default": ""},
        "artifact_uri": {"type": "string", "default": ""},
        "artifact_digest": {"type": "string", "default": ""}}
    secret_inputs = {"NEXUS_USERNAME": {"required": True}, "NEXUS_PASSWORD": {"required": True}}
    build = {
        "name": "build", "if": "${{ inputs.helio_target_environment == '' }}",
        "runs-on": ["self-hosted", "helio-f31"], "env": {"PYTHONDONTWRITEBYTECODE": "1"},
        "outputs": {"artifact_uri": "${{ steps.publish.outputs.artifact_uri }}",
                    "artifact_digest": "${{ steps.publish.outputs.artifact_digest }}"},
        "steps": [
            {"uses": CHECKOUT, "with": {"ref": "${{ github.sha }}", "persist-credentials": False,
                                               "sparse-checkout": "/fixture.py\n/app", "sparse-checkout-cone-mode": False}},
            {"name": "Package and publish immutable WAR", "id": "publish",
             "run": str(VENV / "bin/python") + ' fixture.py build --version "$F31_VERSION"',
             "env": {"F31_VERSION": "${{ inputs.version }}", "NEXUS_USERNAME": "${{ secrets.NEXUS_USERNAME }}",
                     "NEXUS_PASSWORD": "${{ secrets.NEXUS_PASSWORD }}", "GITHUB_TOKEN": "${{ github.token }}",
                     "F31_BUILD_JOB_NAME": "F31 catalog / build"}},
            {"uses": UPLOAD, "with": {"name": "helio-catalog-publication-${{ github.run_attempt }}",
                                      "path": "publication.json", "if-no-files-found": "error"}}]}
    jobs = {"build": build}
    for stage in ("test", "uat", "production"):
        matrix = ("${{ inputs.helio_target_environment != '' && "
                  "fromJSON(format('[\"{0}\"]', inputs.helio_target_environment)) || "
                  "fromJSON(vars.HELIO_ENVIRONMENTS)." + stage + " }}")
        guard = ("${{ !cancelled() && (inputs.helio_target_environment == '' || "
                 "contains(fromJSON(vars.HELIO_ENVIRONMENTS)." + stage + ", inputs.helio_target_environment)) && "
                 "(needs.build.result == 'success' || inputs.helio_target_environment != '') }}")
        jobs["deploy_" + stage] = {
            "name": "${{ matrix.environment }}", "needs": ["build"], "if": guard,
            "strategy": {"fail-fast": False, "matrix": {"environment": matrix}},
            "environment": "${{ matrix.environment }}",
            "runs-on": "${{ fromJSON(vars.HELIO_ENVIRONMENTS).runs_on[matrix.environment] }}",
            "env": {"PYTHONDONTWRITEBYTECODE": "1"},
            "steps": [
                {"uses": CHECKOUT, "with": {"ref": "${{ github.sha }}", "persist-credentials": False, "clean": True,
                     "sparse-checkout-cone-mode": False,
                     "sparse-checkout": "\n".join("/" + path for path in sorted(set(files) | {".github/workflows/f31.yml", ".github/workflows/f31-template.yml", "recipe/runtime-manifest.json"}))}},
                {"name": "Install and verify pinned WAR",
                 "run": str(VENV / "bin/ansible-playbook") + ' -i inventory.yml deploy-war.yml --limit "$HELIO_TARGET" --extra-vars @recipe/inputs-catalog.json',
                 "env": {**runtime_env, "NEXUS_USERNAME": "${{ secrets.NEXUS_USERNAME }}",
                         "NEXUS_PASSWORD": "${{ secrets.NEXUS_PASSWORD }}", "GITHUB_TOKEN": "${{ github.token }}",
                         "HELIO_TARGET": "${{ matrix.environment }}", "HELIO_VERSION": "${{ inputs.version }}",
                         "HELIO_ARTIFACT_URI": "${{ needs.build.outputs.artifact_uri || inputs.artifact_uri }}",
                         "HELIO_ARTIFACT_DIGEST": "${{ needs.build.outputs.artifact_digest || inputs.artifact_digest }}"}},
                {"uses": UPLOAD, "with": {"name": "helio-catalog-deployment-${{ matrix.environment }}-${{ github.run_attempt }}",
                                          "path": "evidence/${{ matrix.environment }}/deployment.json", "if-no-files-found": "error"}}]}
    template = {"name": "F31 catalog Ansible WAR template",
                "on": {"workflow_call": {"inputs": shared_inputs, "secrets": secret_inputs}},
                "permissions": {"contents": "read", "actions": "read"}, "jobs": jobs}
    caller = {"name": "F31 catalog reference",
              "run-name": "${{ inputs.helio_execution_id || format('F31 manual {0}', github.run_id) }}",
              "on": {"workflow_dispatch": {"inputs": dict(shared_inputs, helio_execution_id={"type": "string", "default": ""})}},
              "permissions": {"contents": "read", "actions": "read"},
              "concurrency": {"group": "f31-local-targets", "cancel-in-progress": False},
              "jobs": {"catalog": {"name": "F31 catalog",
                        "uses": "helloangular/helio-f31-ansible-fixture-20261026/.github/workflows/f31-template.yml@" + template_commit,
                        "with": {k: "${{ inputs." + k + " }}" for k in shared_inputs},
                        "secrets": {k: "${{ secrets." + k + " }}" for k in secret_inputs}}}}
    save(".github/workflows/f31-template.yml", template)
    save(".github/workflows/f31.yml", caller)
    for path in (".github/workflows/f31.yml", ".github/workflows/f31-template.yml"):
        manifest["files"][path] = {"git_blob_sha": subprocess.check_output(
            ["git", "hash-object", str(ROOT / path)], cwd=ROOT, text=True).strip()}
    save("recipe/runtime-manifest.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--template-commit", required=True)
    generate(parser.parse_args().template_commit)
