# F31 owned reference fixture

Builds: an immutable Java WAR in local Nexus and three isolated Tomcat targets.
Feature/case: F31 artifact publication and deploy-only qualification. These runs
are development qualification, not BlueMatrix acceptance verdicts.

Repository: `helloangular/helio-f31-ansible-fixture-20261026`, public, `main`.
The source in this directory is mirrored to that repository's root. Every push
uses an atomic branch + `helio/workflow/<full commit>` tag push.

## Private inputs (paths only)

- Existing Nexus CI credential: `~/.helio-release-secrets/nexus-ci.token`.
  The setup helper supplies its two fields to this repository's `NEXUS_USERNAME`
  and `NEXUS_PASSWORD` GitHub secrets using stdin. Neither is committed or logged.
- Existing Nexus HTTPS trust certificate:
  `~/.helio-tomcat/tls/local-tls/cert.pem`. Verification stays enabled.
- Dedicated venv: `~/.helio-f31-fixture/venv`.
- Dedicated collections: `~/.helio-f31-fixture/collections`.
- Repo-only runner: `~/.helio-actions-runner-f31`, label `helio-f31`.
- No customer, VPS or lab credentials or services are used.

## Prepare and check

Use Python 3.12.12; create the dedicated venv, install `requirements.lock.txt`,
then install `community.docker:4.8.1` and its pinned dependency
`community.library_inventory_filtering_v1:1.1.5` into the dedicated collections
directory. Run the venv's `python prepare_recipe.py`. It records every file, directory and symlink in the dedicated venv/collection roots
(including bytecode and pyvenv.cfg), plus the external Python and Docker binary
hashes. It pins every deployed
playbook, inventory and extra-vars file by Git blob identity. The manifest's own
blob must be pinned externally at registration, avoiding a self-referential hash.
Changes to this Mac's interpreter or installed dependencies require re-qualification.

Run `python -m unittest discover -p test_fixture.py`, `actionlint -config-file
.github/actionlint.yaml -shellcheck='' .github/workflows/f31.yml`, and the venv's
`ansible-playbook -i inventory.yml deploy-war.yml --syntax-check`.

After the product test JVM stops and swap is below 8 GiB:

1. From the existing product checkout, without editing it:
   `docker compose up -d nexus local-tls`.
2. Here: `docker compose -f compose.yml build` then
   `docker compose -f compose.yml up -d`. The three targets share one image built
   FROM the pinned Tomcat digest. Python is installed during target construction,
   never by a deploy job. Record the resulting local image ID and Python version
   before calling the reference qualified.
3. Register the runner for this repository only. **The owner starts it**; no
   daemon or background runner may be started by the agent.

Owner start command (foreground; leave open for the three proof runs):

```sh
bash "$HOME/.helio-f31-fixture/repository/start-owned-runner.sh"
```

The owner command starts only the fixture services, waits for readiness, and then
runs the repo-only runner in the foreground. Ctrl-C removes the owned targets and
stops only Nexus-front services started by that invocation.

The deployment jobs call the venv's absolute `ansible-playbook` path. They use
only checkout, that command, and upload-artifact. Untrusted workflow input values
are environment values read by fixed extra-vars documents, never shell or JSON
source interpolation. The owner approved `ansible.builtin.find` **only in the controller runtime preflight**. It uses two fixed owned roots, does not follow links, includes hidden files and bytecode, and stops at 8,193 entries; more than 8,192 refuses. Skipped reads, any path/type difference, changed file bytes or retargeted interpreter links refuse before download. `find` remains forbidden in deployment tasks. `qualify_runtime.py` proves clean / added-file refusal / changed-file refusal / restored behavior using a harmless canary that is removed in a finally block. All preflight tasks have the `runtime_preflight` tag; this qualification starts no containers and performs no provider/deployment calls. Download
uses a SHA-256 checksum and TLS verification; a failed download cannot reach the
copy to Tomcat. `/artifact-digest` hashes installed `ROOT.war` bytes rather than an
expected digest supplied by the workflow.

## Required proof (pending owner-started runner)

Record exact source SHA, manifest blob, image ID, run IDs, attempts and evidence:

1. Unique immutable version, normal build + test/uat/prod deployment. All three
   served digests must equal the published WAR digest.
2. `deployment_only=true`, target `uat`, the same version/URI/digest. Build job
   must be skipped and served digest must remain identical.
3. Same UAT input but a deliberately wrong SHA-256. `get_url` must fail before
   the install task; compare installed WAR hash before/after and preserve task
   output. Do not use `continue-on-error` or weaken the checksum predicate.

Normal-release evidence is `reported_not_pinned`, consistent with the owner's
first-release ruling. This fixture does not claim that normal approvals pin bytes.
It is not yet a registered, approved production recipe: the product's initial
structural prototype still needs explicit support/tests for the manifest schema,
controller download/authentication, fixed env lookups, runtime preflight and
report generation used here. No permissive fallback is authorized.

## Teardown

Stop only this foreground runner (Ctrl-C) and this compose project's targets:
`docker compose -f compose.yml down`. Remove only its generated `evidence/` and
local WAR output. Stop Nexus/local-tls only if this block started them and no
other work needs them; never stop another session's containers. Keep the named
Nexus volume and immutable published version. Never use `down -v` in the product
checkout. Leave runner registration and public source available for the next
qualification; runner credentials remain only in its private directory.

Current state and immutable identities are in `Qualification.json`. A fixture is
not DONE until all three proof runs pass; an owner-started runner is a prerequisite.
