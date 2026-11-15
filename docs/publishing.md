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
POSIX shells and Windows PowerShell; see
[the smoke test below](#clean-pypi-release-smoke-test).

## Clean PyPI release smoke test

Use this checklist after a published release to confirm the package works
outside a repository checkout. The commands install from PyPI into a fresh
virtual environment. They do not mutate a real library.

The current published package version is `0.4.1`. Tags and GitHub Releases
use the `v<version>` form, for example `v0.4.1`. That tag must match
`jellyfin_show_organizer.__version__` before the release workflow publishes.
See [Release and versioning policy](releasing.md) and
[Package publishing](publishing.md).

### What the smoke test covers

- A clean virtual environment, not the project checkout.
- `jmo --version`
- `jmo demo`
- `jmo doctor`
- `jmo inspect`

`jmo demo` writes fabricated media and planner state only. It never runs
apply and never touches a real library.

### POSIX shell

```bash
python3.12 -m venv /tmp/jmo-pypi-smoke
/tmp/jmo-pypi-smoke/bin/python -m pip install --upgrade pip
/tmp/jmo-pypi-smoke/bin/python -m pip install jellyfin-media-organizer==0.4.1
/tmp/jmo-pypi-smoke/bin/jmo --version
```

Expect `Jellyfin Media Organizer 0.4.1`. Then:

```bash
/tmp/jmo-pypi-smoke/bin/jmo demo --output /tmp/jmo-pypi-demo
/tmp/jmo-pypi-smoke/bin/jmo doctor /tmp/jmo-pypi-demo/Shows \
  --destination-root /tmp/jmo-pypi-demo/OrganizedShows \
  --output-dir /tmp/jmo-pypi-demo/State/runs/initial \
  --cache-dir /tmp/jmo-pypi-demo/State/cache
/tmp/jmo-pypi-smoke/bin/jmo inspect /tmp/jmo-pypi-demo/State/runs/demo-run
```

Remove the environment and demo output when finished:

```bash
rm -rf /tmp/jmo-pypi-smoke /tmp/jmo-pypi-demo
```

### Windows PowerShell

```powershell
py -3.12 -m venv $env:TEMP\jmo-pypi-smoke
& "$env:TEMP\jmo-pypi-smoke\Scripts\python.exe" -m pip install --upgrade pip
& "$env:TEMP\jmo-pypi-smoke\Scripts\python.exe" -m pip install jellyfin-media-organizer==0.4.1
& "$env:TEMP\jmo-pypi-smoke\Scripts\jmo.exe" --version
```

Expect `Jellyfin Media Organizer 0.4.1`. Then:

```powershell
& "$env:TEMP\jmo-pypi-smoke\Scripts\jmo.exe" demo --output "$env:TEMP\jmo-pypi-demo"
& "$env:TEMP\jmo-pypi-smoke\Scripts\jmo.exe" doctor "$env:TEMP\jmo-pypi-demo\Shows" --destination-root "$env:TEMP\jmo-pypi-demo\OrganizedShows" --output-dir "$env:TEMP\jmo-pypi-demo\State\runs\initial" --cache-dir "$env:TEMP\jmo-pypi-demo\State\cache"
& "$env:TEMP\jmo-pypi-smoke\Scripts\jmo.exe" inspect "$env:TEMP\jmo-pypi-demo\State\runs\demo-run"
```

Remove the environment and demo output when finished:

```powershell
Remove-Item -Recurse -Force "$env:TEMP\jmo-pypi-smoke", "$env:TEMP\jmo-pypi-demo"
```

### Version strategy

Pin the installed package to the release you are checking
(`jellyfin-media-organizer==0.4.1` above). After a later release, update the
pin and the expected `jmo --version` string to the new
`jellyfin_show_organizer.__version__` value. Do not smoke-test a checkout
install (`pip install .`) when the goal is to verify the published artifact.

A passing smoke test confirms installation and the non-mutating CLI entry
points. It does not authorize apply.
