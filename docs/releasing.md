# Automated releases

Publishing a normal GitHub Release for a tag `vMAJOR.MINOR.PATCH` starts
[Publish release](../.github/workflows/release.yml). Drafts, prereleases, pushes
and pull requests do not publish packages. The workflow does not choose or bump
versions: the maintainer still decides when a version is ready.

## Publish a new version

1. Update the version in `Cargo.toml`, `Cargo.lock` and `pyproject.toml`, and add
   the changelog entry. Both packages must have exactly the same stable version.
   Follow [compatibility rules](compatibility.md).
2. Commit and push the release source, including `release.yml` and its scripts.
   Create a matching tag, for example `v0.1.1` for package version `0.1.1`.
3. Create and publish the GitHub Release for that tag. Do not mark it as a draft
   or prerelease. No local registry upload is needed.
4. Watch the workflow. It checks the exact tagged source on Ubuntu and macOS,
   builds compact packages, runs isolated Rust/Python consumers, attaches the
   verified files and checksums, publishes to crates.io/PyPI, and updates an
   automated status block in the release notes. Existing maintainer notes remain.

The tag, both package versions, source commit and artifact report must agree.
The workflow never publishes the current `main` checkout as a substitute for the
release tag. Only the fixed `ai-protocol` and `ai-protocol-client` packages and
production registry URLs are supported. The optional dispatcher remains Unix-only.

## Credentials and permissions

The GitHub environment **release** holds two encrypted environment secrets:

| Secret | Purpose |
| --- | --- |
| `CARGO_REGISTRY_TOKEN` | Publish `ai-protocol` to crates.io |
| `PYPI_API_TOKEN` | Publish `ai-protocol-client` to PyPI |

The existing local tokens are used for initial setup; values are never committed
or logged. Scope tokens to their package and publishing permissions where the
registry permits it. Update these secrets when credentials expire or rotate:

```sh
gh secret set CARGO_REGISTRY_TOKEN --env release
gh secret set PYPI_API_TOKEN --env release
```

The environment permits release tags matching `v*` and the `main` branch for
explicit recovery runs. It has no manual approval requirement. Build/test jobs
have read-only repository access and no registry secrets. Only the publication
step receives registry credentials; its job can also attach release assets and
update notes. Third-party build scripts run before credentials are provided.
The official GitHub actions are pinned to reviewed commit hashes.

This setup uses API tokens, not OIDC Trusted Publishing. It requires no additional
registry-side publisher registration. GitHub repository write access remains a
publishing trust boundary: maintainers can create releases and edit workflows.

## Retry and recovery

A version cannot be replaced in either registry. Before uploading anything, the
workflow checks both registries. Already published files are accepted only when
their SHA-256 values match the selected release artifacts. A mismatch fails the
run; it is never hidden by `--skip-existing` or an overwritten tag/asset.

A complete existing GitHub bundle (crate, sdist, wheel, verification report and
`SHA256SUMS`) is preserved byte-for-byte on reruns. This matters because rebuilding
Python archives can change timestamps. Otherwise the newly tested bundle is used.
All files are attached before either registry upload. Existing assets are checked
and never overwritten. An incomplete initial asset upload should be recovered
by rerunning the failed publication job from its original run, retaining the
original Actions artifact (kept for 90 days).

If one registry succeeds and the other fails, fix the cause and rerun the failed
publication job. Matching files are skipped and only missing files are uploaded.
Registry/network errors do not mean a version is absent: only HTTP 404 does.
Timeout after upload can mean the registry accepted the files; the next run
checks their hashes before attempting any upload.

Manual verification without publication is available from the Actions UI or:

```sh
gh workflow run release.yml --ref main -f tag=v0.1.0 -F publish=false
```

For explicit recovery/publication of an already published GitHub Release:

```sh
gh workflow run release.yml --ref main -f tag=v0.1.0 -F publish=true
```

These commands use the workflow from `main` but build and publish only the
selected tag. They do not create or move tags and cannot publish an unpublished
GitHub draft. For an existing version with matching artifacts the publication
run verifies the registries without re-uploading packages.

## Local checks and limits

`bash scripts/verify.sh` remains the local/Unix CI entry point. Set
`PACKAGE_OUTPUT_DIR` to a new directory to retain its verified artifacts. The
package checker validates extracted crate targets and external native HTTP calls,
and installs a wheel built from the sdist in a fresh environment without OpenAI.
The helper's tests cover version mismatches, corrupted bundles, registry failures,
partial uploads and safe retries. Cargo publication uses `--no-verify` only after
these build/tests and a byte-for-byte comparison of the crate Cargo would upload,
so publishing does not run project build scripts with registry credentials.

Cross-registry publication is not atomic. The status block is written only after
both registries expose the expected checksums. A failure between services can
leave one package published until a successful retry. Historical benchmark series,
public deployments and independent security/usability audits are not run by this
workflow. The external developer trial remains deliberately skipped.
