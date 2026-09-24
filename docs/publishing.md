# Publishing installable packages

JMO release artifacts are built and verified by GitHub Actions. The preferred
community install path is PyPI once the repository has a configured PyPI
trusted publisher:

```text
py -m pip install jellyfin-media-organizer
```

Until PyPI is configured, download the verified wheel from the GitHub Release
and install it in a clean environment:

```powershell
py -m pip install .\jellyfin_media_organizer-0.3.0-py3-none-any.whl
```

The release workflow must continue to verify the tag/package version match,
wheel and source-distribution installation, packaged schemas, and CLI smoke
commands before publication. Publishing must use a protected environment and
short-lived trusted publishing credentials; do not add a long-lived PyPI token
to repository secrets or source files.

## One-time PyPI setup

The repository contains a release-only workflow at
`.github/workflows/publish-pypi.yml`. To enable it:

1. Create a GitHub environment named `pypi` in the repository settings. Add a
   required reviewer if you want a human approval before a release can publish.
2. In PyPI, open the project publishing settings and add a GitHub Actions
   trusted publisher with:
   - owner: `akjroller`
   - repository: `jellyfin-media-organizer`
   - workflow: `publish-pypi.yml`
   - environment: `pypi`
3. Publish a new GitHub Release whose tag exactly matches the package version,
   such as `v0.3.1`. The workflow checks the tag, rebuilds both distributions,
   installs and smoke-tests the wheel, and only then exchanges a short-lived
   OIDC identity with PyPI.

There is intentionally no manual dispatch path and no PyPI token in GitHub
secrets. A release publication is the only event that can invoke the uploader.
The existing GitHub Release artifacts remain available as a fallback until the
first PyPI publication succeeds.

No package publication authorizes a media apply. The plan, review, preflight,
approval-token, journal, and rollback contracts remain unchanged.

## After publication

Once a GitHub Release has published to PyPI, verify the artifact from a
clean environment that is not a repository checkout. The steps cover
`jmo --version`, `jmo demo`, `jmo doctor`, and `jmo inspect` on both
POSIX shells and Windows PowerShell. See
[Clean PyPI release smoke test](pypi-smoke-test.md).
