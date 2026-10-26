"""F31 reference-fixture unit tests; these do not consume acceptance executions."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class FixtureContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("f31_fixture", ROOT / "fixture.py")
        cls.kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.kit)

    def test_publication_identity_cannot_be_supplied_in_inputs(self):
        c = dict(repository_id=101, workflow_id=202, source_commit="a" * 40,
                 run_id=303, run_attempt=1, job_id=404)
        result = self.kit.publication(c, "1.0.1", "https://localhost:9444/repository/helio-releases/app.war", "b" * 64)
        self.assertEqual(result["artifact_digest"], "sha256:" + "b" * 64)
        self.assertEqual(result["kind"], "catalog_publication")
        with self.assertRaises(ValueError):
            self.kit.publication(c, "latest", result["artifact_uri"], "b" * 64)

    def test_version_and_coordinate_injection_refused(self):
        for bad in ("../1", "1;echo", "1\n2", "1-SNAPSHOT", "main"):
            with self.assertRaises(ValueError):
                self.kit.version(bad)

    def test_runtime_manifest_detects_changed_installed_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pinned.py"
            p.write_text("reviewed")
            expected = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()}
            self.kit.check_files(expected)
            p.write_text("changed")
            with self.assertRaises(ValueError):
                self.kit.check_files(expected)

    def test_target_restriction(self):
        self.assertEqual(self.kit.target("uat"), "uat")
        for bad in ("localhost", "all", "prod:localhost", "uat;echo"):
            with self.assertRaises(ValueError):
                self.kit.target(bad)

    def test_preflight_enumerates_only_fixed_controller_roots(self):
        play = json.loads((ROOT / "deploy-war.yml").read_text())[0]
        finds = [t for t in play["pre_tasks"] if "ansible.builtin.find" in t]
        self.assertEqual(len(finds), 1)
        if finds:
            self.assertEqual(finds[0]["delegate_to"], "localhost")
            args = finds[0]["ansible.builtin.find"]
            self.assertEqual(args["limit"], 8193)
            self.assertFalse(args["follow"])
            self.assertTrue(args["get_checksum"])
            self.assertEqual(args["checksum_algorithm"], "sha256")
            self.assertIsInstance(args["paths"], list)
            for path in args["paths"]:
                self.assertTrue(path.startswith(str(Path.home() / ".helio-f31-fixture") + "/"))
                self.assertNotIn("{{", path)
        self.assertFalse(any("ansible.builtin.find" in t for t in play["tasks"]))

    def test_exact_set_assertion_precedes_every_download(self):
        play = json.loads((ROOT / "deploy-war.yml").read_text())[0]
        comparisons = [t for t in play["pre_tasks"]
                       if "helio_runtime_entries" in json.dumps(t.get("ansible.builtin.assert", {}))]
        self.assertEqual(len(comparisons), 1)
        if comparisons:
            assertions = json.dumps(comparisons[0]["ansible.builtin.assert"])
            self.assertIn("skipped_paths", assertions)
            self.assertIn("8192", assertions)
            self.assertIn("==", assertions)
            self.assertNotIn("difference", assertions)

    def test_virtualenv_configuration_is_in_the_hashed_set(self):
        pins = json.loads((ROOT / "recipe/runtime-files.json").read_text())["helio_runtime_files"]
        self.assertIn(str(Path.home() / ".helio-f31-fixture/venv/pyvenv.cfg"),
                      {p["path"] for p in pins})

    def test_deploy_jobs_have_only_three_steps_and_no_shell_input_interpolation(self):
        workflow = json.loads((ROOT / ".github/workflows/f31-template.yml").read_text())
        for stage in ("test", "uat", "production"):
            steps = workflow["jobs"]["deploy_" + stage]["steps"]
            self.assertEqual(len(steps), 3)
            self.assertTrue(steps[0]["uses"].startswith("actions/checkout@"))
            self.assertTrue(steps[2]["uses"].startswith("actions/upload-artifact@"))
            self.assertNotIn("${{", steps[1]["run"])
            self.assertIn("--extra-vars @recipe/inputs-catalog.json", steps[1]["run"])
            self.assertNotIn("mvn", steps[1]["run"])

    def test_catalog_caller_and_template_preserve_the_existing_targeting_contract(self):
        caller = json.loads((ROOT / ".github/workflows/f31.yml").read_text())
        template = json.loads((ROOT / ".github/workflows/f31-template.yml").read_text())
        self.assertIn("helio_target_environment", caller["on"]["workflow_dispatch"]["inputs"])
        call = caller["jobs"]["catalog"]
        self.assertEqual(call["with"]["helio_target_environment"], "${{ inputs.helio_target_environment }}")
        self.assertRegex(call["uses"], r"^helloangular/helio-f31-ansible-fixture-20261026/\.github/workflows/f31-template\.yml@[0-9a-f]{40}$")
        self.assertEqual(template["jobs"]["build"]["if"], "${{ inputs.helio_target_environment == '' }}")
        self.assertIn("helio_target_environment", template["on"]["workflow_call"]["inputs"])
        for stage in ("test", "uat", "production"):
            job = template["jobs"]["deploy_" + stage]
            self.assertNotIn("uses", job)
            self.assertEqual(job["environment"], "${{ matrix.environment }}")
            self.assertEqual(job["runs-on"], "${{ fromJSON(vars.HELIO_ENVIRONMENTS).runs_on[matrix.environment] }}")
            self.assertEqual(set(job["strategy"]["matrix"]), {"environment"})
            self.assertIn("inputs.helio_target_environment", job["strategy"]["matrix"]["environment"])
            self.assertIn("fromJSON(vars.HELIO_ENVIRONMENTS)." + stage, job["strategy"]["matrix"]["environment"])
            self.assertIn("contains(fromJSON(vars.HELIO_ENVIRONMENTS)." + stage, job["if"])
        manifest = json.loads((ROOT / "recipe/runtime-manifest.json").read_text())
        for path in (".github/workflows/f31.yml", ".github/workflows/f31-template.yml"):
            self.assertIn(path, manifest["files"])

    def test_checksum_precedes_install_and_has_no_error_escape(self):
        play = json.loads((ROOT / "deploy-war.yml").read_text())[0]
        tasks = play["tasks"]
        download = next(i for i, t in enumerate(tasks) if "ansible.builtin.get_url" in t)
        install = next(i for i, t in enumerate(tasks) if t.get("ansible.builtin.copy", {}).get("dest") == "/usr/local/tomcat/webapps/ROOT.war")
        self.assertLess(download, install)
        self.assertEqual(tasks[download]["ansible.builtin.get_url"]["checksum"], "{{ helio_artifact_digest }}")
        self.assertTrue(tasks[download]["ansible.builtin.get_url"]["validate_certs"])
        for key in ("ignore_errors", "failed_when", "rescue", "when"):
            self.assertNotIn(key, tasks[download])

    def test_download_options_are_supported_by_the_exact_installed_module(self):
        import subprocess
        import os
        document = json.loads(subprocess.check_output(
            [str(Path.home() / ".helio-f31-fixture/venv/bin/ansible-doc"), "--json", "ansible.builtin.get_url"],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1")))
        options = document["ansible.builtin.get_url"]["doc"]["options"]
        play = json.loads((ROOT / "deploy-war.yml").read_text())[0]
        task = next(t for t in play["tasks"] if "ansible.builtin.get_url" in t)
        self.assertEqual(set(task["ansible.builtin.get_url"]) - set(options), set())
        self.assertTrue(task["ansible.builtin.get_url"]["validate_certs"])
        self.assertEqual(task.get("environment", {}).get("SSL_CERT_FILE"),
                         str(Path.home() / ".helio-tomcat/tls/local-tls/cert.pem"))

    def test_manifest_covers_exact_deploy_files_and_installed_runtime(self):
        import subprocess
        manifest = json.loads((ROOT / "recipe/runtime-manifest.json").read_text())
        self.assertEqual(manifest["schema"], "helio_deploy_recipe_v1")
        self.assertEqual(manifest["ansible_core"], "2.19.3")
        for name, identity in manifest["files"].items():
            actual = subprocess.check_output(["git", "hash-object", str(ROOT / name)], text=True).strip()
            self.assertEqual(actual, identity["git_blob_sha"])
        pins = json.loads((ROOT / "recipe/runtime-files.json").read_text())["helio_runtime_files"]
        self.kit.check_files({p["path"]: p["sha256"] for p in pins})


if __name__ == "__main__":
    unittest.main()
