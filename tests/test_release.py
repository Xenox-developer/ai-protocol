"""Publication boundaries: exact versions, canonical bytes and safe retry after partial uploads."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release

VERSION = "0.1.0"
COMMIT = "a" * 40
TAG = "v0.1.0"


def bundle(directory):
    directory.mkdir()
    for name in release.names(VERSION):
        (directory / name).write_bytes(name.encode())
    checksums = {name: release.digest(directory / name) for name in release.names(VERSION)}
    (directory / "verification.json").write_text(json.dumps({
        "version": VERSION, "source_commit": COMMIT, "working_tree_dirty": False, "sha256": checksums}))
    (directory / "SHA256SUMS").write_text("".join(
        f"{release.digest(directory / name)}  {name}\n" for name in sorted([*checksums, "verification.json"])))
    return checksums


class ReleaseTests(unittest.TestCase):
    def test_only_matching_stable_tag_and_both_package_versions_are_accepted(self):
        cargo = {"package": {"name": "ai-protocol", "version": VERSION}}
        python = {"project": {"name": "ai-protocol-client", "version": VERSION}}
        self.assertEqual(release.version_for(TAG, cargo, python), VERSION)
        for tag in ["main", "v01.0.0", "v0.1.0;echo secret", "v0.1.0\nvalue=x", "v0.1.0-alpha.1", "v0.2.0"]:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.version_for(tag, cargo, python)
        python["project"]["version"] = "0.2.0"
        with self.assertRaises(ValueError):
            release.version_for(TAG, cargo, python)

    def test_bundle_rejects_modified_bytes_wrong_source_dirty_reports_and_extra_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "assets"
            checksums = bundle(directory)
            self.assertEqual(release.validate_bundle(directory, VERSION, COMMIT), checksums)
            with self.assertRaises(ValueError):
                release.validate_bundle(directory, VERSION, "b" * 40)
            (directory / ".env").write_text("must-not-be-uploaded")
            with self.assertRaises(ValueError):
                release.validate_bundle(directory, VERSION, COMMIT)
            (directory / ".env").unlink()
            report = json.loads((directory / "verification.json").read_text())
            report["working_tree_dirty"] = True
            (directory / "verification.json").write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                release.validate_bundle(directory, VERSION, COMMIT)
            report["working_tree_dirty"] = False
            (directory / "verification.json").write_text(json.dumps(report))
            (directory / release.names(VERSION)[0]).write_bytes(b"changed")
            with self.assertRaises(ValueError):
                release.validate_bundle(directory, VERSION, COMMIT)

    def test_registry_state_resumes_only_missing_files_and_rejects_different_published_bytes(self):
        crate, sdist, wheel = release.names(VERSION)
        hashes = {name: "a" * 64 for name in (crate, sdist, wheel)}
        rust = {"version": {"num": VERSION, "checksum": hashes[crate]}}
        python = {"info": {"version": VERSION}, "urls": [{"filename": wheel, "digests": {"sha256": hashes[wheel]}}]}
        with patch.object(release, "registry_json", side_effect=[None, None]):
            self.assertEqual(release.registry_state(VERSION, hashes), (False, [sdist, wheel]))
        with patch.object(release, "registry_json", side_effect=[rust, python]):
            self.assertEqual(release.registry_state(VERSION, hashes), (True, [sdist]))
        python["urls"][0]["digests"]["sha256"] = "b" * 64
        with patch.object(release, "registry_json", side_effect=[rust, python]), self.assertRaises(ValueError):
            release.registry_state(VERSION, hashes)
        rust["version"]["checksum"] = "b" * 64
        with patch.object(release, "registry_json", return_value=rust), self.assertRaises(ValueError):
            release.registry_state(VERSION, hashes)

    def test_only_http_404_means_absent_and_http_failure_cannot_authorize_publication(self):
        for status in [404, 403, 500]:
            client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
            with patch.object(release.httpx, "Client", return_value=client):
                if status == 404:
                    self.assertIsNone(release.registry_json("https://registry.test/version"))
                else:
                    with self.assertRaises(httpx.HTTPStatusError):
                        release.registry_json("https://registry.test/version")

    def test_preparation_preserves_existing_complete_bundle_instead_of_rebuilding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hashes = bundle(root / "canonical")
            destination = root / "selected"
            existing = {"assets": [{"name": path.name} for path in (root / "canonical").iterdir()]}
            def download(*args, **_kwargs):
                target = Path(args[args.index("--dir") + 1])
                for source in (root / "canonical").iterdir():
                    shutil.copy2(source, target / source.name)
            with patch.object(release, "source_info", return_value=(VERSION, COMMIT)), patch.object(release, "release_info", return_value=existing), patch.object(release, "command", side_effect=download):
                release.prepare(root, TAG, root / "missing-build", destination)
            self.assertEqual(release.validate_bundle(destination, VERSION, COMMIT), hashes)
            existing["assets"].pop()
            with patch.object(release, "source_info", return_value=(VERSION, COMMIT)), patch.object(release, "release_info", return_value=existing), self.assertRaises(ValueError):
                release.prepare(root, TAG, root, root / "partial")

    def test_asset_mismatch_stops_before_upload_and_never_overwrites_existing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle(root / "assets")
            existing = {"assets": [{"name": "verification.json"}]}
            def download(*args, **_kwargs):
                (Path(args[args.index("--dir") + 1]) / "verification.json").write_text("different")
            with patch.object(release, "command", side_effect=download) as command, self.assertRaises(ValueError):
                release.attach_assets(TAG, root / "assets", existing)
            self.assertEqual(command.call_count, 1)

    def test_publish_skips_matching_registries_and_resumes_partial_python_upload(self):
        for missing in [[], [release.names(VERSION)[1]]]:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle(root / "assets")
                metadata = {"body": "Maintainer's release notes", "assets": []}
                with patch.object(release, "source_info", return_value=(VERSION, COMMIT)), patch.object(release, "release_info", return_value=metadata), patch.object(release, "registry_state", side_effect=[(True, missing), (True, [])]), patch.object(release, "attach_assets"), patch.object(release, "command") as command, patch.dict(os.environ, {"CARGO_REGISTRY_TOKEN": "test-cargo", "PYPI_API_TOKEN": "test-pypi", "GH_TOKEN": "test-github", "GITHUB_RUN_ID": "123"}, clear=True):
                    release.publish(root, TAG, root / "assets")
                self.assertFalse(any(call.args[0] == "cargo" for call in command.call_args_list))
                uploads = [call for call in command.call_args_list if "upload" in call.args]
                self.assertEqual(len(uploads), bool(missing))
                if missing:
                    self.assertTrue(uploads[0].args[-1].endswith(missing[0]))
                    self.assertNotIn("CARGO_REGISTRY_TOKEN", uploads[0].kwargs["env"])
                    self.assertNotIn("GH_TOKEN", uploads[0].kwargs["env"])
                    self.assertEqual(uploads[0].kwargs["env"]["TWINE_PASSWORD"], "test-pypi")
                notes = (root / "publication-notes.md").read_text()
                self.assertIn("Maintainer's release notes", notes)
                self.assertEqual(notes.count("<!-- ai-protocol-publication:start -->"), 1)

    def test_first_publication_checks_crate_bytes_and_isolates_each_registry_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            package = source / "target/package"
            package.mkdir(parents=True)
            bundle(root / "assets")
            crate = release.names(VERSION)[0]
            shutil.copy2(root / "assets" / crate, package / crate)
            metadata = {"body": "Release notes", "assets": []}
            tokens = {"CARGO_REGISTRY_TOKEN": "test-cargo", "PYPI_API_TOKEN": "test-pypi", "GH_TOKEN": "test-github", "GITHUB_RUN_ID": "123"}
            with patch.object(release, "source_info", return_value=(VERSION, COMMIT)), patch.object(release, "release_info", return_value=metadata), patch.object(release, "registry_state", side_effect=[(False, list(release.names(VERSION)[1:])), (True, [])]), patch.object(release, "attach_assets"), patch.object(release, "command") as command, patch.dict(os.environ, tokens, clear=True):
                release.publish(source, TAG, root / "assets")
            cargo = [call for call in command.call_args_list if call.args[:2] == ("cargo", "publish")]
            self.assertEqual(len(cargo), 1)
            self.assertEqual(cargo[0].kwargs["env"]["CARGO_REGISTRY_TOKEN"], "test-cargo")
            self.assertNotIn("PYPI_API_TOKEN", cargo[0].kwargs["env"])
            self.assertNotIn("GH_TOKEN", cargo[0].kwargs["env"])
            prepare = next(call for call in command.call_args_list if call.args[:2] == ("cargo", "package"))
            self.assertNotIn("CARGO_REGISTRY_TOKEN", prepare.kwargs["env"])
            python = next(call for call in command.call_args_list if "upload" in call.args)
            self.assertNotIn("CARGO_REGISTRY_TOKEN", python.kwargs["env"])
            self.assertEqual(python.kwargs["env"]["TWINE_PASSWORD"], "test-pypi")
            (package / crate).write_bytes(b"different Cargo payload")
            with patch.object(release, "source_info", return_value=(VERSION, COMMIT)), patch.object(release, "release_info", return_value=metadata), patch.object(release, "registry_state", return_value=(False, [])), patch.object(release, "attach_assets"), patch.object(release, "command") as command, patch.dict(os.environ, tokens, clear=True), self.assertRaises(ValueError):
                release.publish(source, TAG, root / "assets")
            self.assertFalse(any(call.args[:2] == ("cargo", "publish") for call in command.call_args_list))
