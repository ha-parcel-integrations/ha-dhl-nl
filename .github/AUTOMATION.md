# Release automation pilot

Development is pushed directly to `main`. A successful `Validate` run examines
commits since the latest stable GitHub release: `feat:` prepares a minor release
and `fix:` a patch release. It creates or updates one `automation/release` PR;
neither a push nor a successful validation publishes a release by itself.

Merge that generated PR when a normal release is wanted. Its own successful
validation then creates the no-`v` tag and GitHub Release. `chore:`, `refactor:`,
`docs:`, `test:` and `ci:` changes never create a release PR.

For a tester build, run **Prepare pre-release** manually with an explicit
`X.Y.ZbN` version. The workflow checks that its `X.Y.Z` is the release that the
eligible commits imply, then prepares `automation/prerelease`. Its merge creates
a GitHub pre-release. Nothing infers a beta number automatically.

Before enabling a live release, add a fine-grained `RELEASE_BOT_TOKEN` Actions
secret. It needs repository `Contents: read/write` and `Pull requests: read/write`;
the bot identity must be allowed to push `automation/*`. A GitHub App token is
preferred. The token is deliberately not used for publishing tags or releases.
