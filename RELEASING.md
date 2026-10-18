# Making a release

Manual steps to cut a release

With the workflows now in place, the pipeline is almost fully automated. The manual work falls into two groups: 
a one-time setup, and the per-release steps.

## One-time setup (before the first release)

1. Create the release environment in GitHub: repository Settings > Environments > New environment, name it release. release.yaml targets this environment; optionally add required reviewers so a release waits for approval before publishing.
2. Register a PyPI trusted publisher: on PyPI, go to Your projects > Publishing and add a pending publisher with owner hardes-bv, repository python-fairland-modbus, workflow release.yaml, environment release. This is what lets uv publish --trusted-publishing always upload without an API token.
3. Sync the labels: run the Sync labels workflow once via Actions > Sync labels > Run workflow. This creates the labels from .github/labels.yml that Release Drafter and the PR label gate rely on.

## Per-release steps

1. Label every pull request with one of the release-category labels (breaking-change, new-feature, bugfix, enhancement, refactor, performance, maintenance, ci, dependencies, documentation). The PR Labels workflow blocks merging without one. Add major or minor if you want to override the version bump; by default breaking-change bumps major, new-feature bumps minor, everything else bumps patch.
2. Merge to main. Release Drafter then automatically creates or updates a draft release named vX.Y.Z with the changes grouped by category. Nothing to do here.
3. Review the draft under Releases: check the resolved version, adjust the tag if needed (it must start with v, since the workflow strips that prefix), and tidy the notes.
4. Publish the release by selecting Publish release on the draft. This is the only real trigger: it creates the git tag, which fires release.yaml.
From there the Release workflow runs on its own: it stamps the version from the tag into pyproject.toml with uv version --frozen, builds the sdist and wheel, generates and attests the SBOM, and publishes to PyPI. If you configured required reviewers on the release environment, approve the pending deployment in the workflow run.

What you never do by hand:
- Editing version in pyproject.toml (it stays 0.0.0 in git; the tag is the source of truth).
- Running uv build or uv publish locally.
- Writing release notes from scratch.