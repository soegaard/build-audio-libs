# Manual draft release

The `create draft release` workflow is separate from routine CI. It is available only after this workflow file is present on the default branch.

In GitHub, open **Actions**, select **create draft release**, choose **Run workflow**, and enter:

- `version`: the bundle version, for example `1.0.0`.
- `source_ref`: a commit SHA, tag, or branch to resolve once.

The workflow validates the version, checks out the requested source ref once, writes its full commit SHA to job output, and builds that exact SHA on all four declared targets. Each target uses `scripts/audio_build.py all`; there is no release-only build implementation.

Every target must build, stage, audit, test, and upload an archive. Only then does the small `contents: write` job run. It rejects an existing tag or release, creates a source bundle at the resolved commit, creates a `SHA256SUMS` file, and creates a GitHub **draft** release. It does not publish the release.

Review the draft’s four platform archives, source archive, checksums, runtime manifests, audit reports, provenance, and FFmpeg reports. Publish it manually only after that review. The workflow does not use Gitea credentials and does not push to Hans's repository.

If one target is unavailable or fails, the draft job does not run. Fix the failure and start a new run; do not publish a partial release as a complete release.
