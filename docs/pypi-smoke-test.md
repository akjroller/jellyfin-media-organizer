# Clean PyPI release smoke test

Use this checklist after a published release to confirm the package works
outside a repository checkout. The commands install from PyPI into a fresh
virtual environment. They do not mutate a real library.

The current published package version is `0.4.0`. Tags and GitHub Releases
use the `v<version>` form, for example `v0.4.0`. That tag must match
`jellyfin_show_organizer.__version__` before the release workflow publishes.
See [Release and versioning policy](releasing.md) and
[Package publishing](publishing.md).

## What the smoke test covers

- A clean virtual environment, not the project checkout.
- `jmo --version`
- `jmo demo`
- `jmo doctor`
- `jmo inspect`

`jmo demo` writes fabricated media and planner state only. It never runs
apply and never touches a real library.

## POSIX shell

```bash
python3.12 -m venv /tmp/jmo-pypi-smoke
/tmp/jmo-pypi-smoke/bin/python -m pip install --upgrade pip
/tmp/jmo-pypi-smoke/bin/python -m pip install jellyfin-media-organizer==0.4.0
/tmp/jmo-pypi-smoke/bin/jmo --version
```

Expect `Jellyfin Media Organizer 0.4.0`. Then:

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

## Windows PowerShell

```powershell
py -3.12 -m venv $env:TEMP\jmo-pypi-smoke
& "$env:TEMP\jmo-pypi-smoke\Scripts\python.exe" -m pip install --upgrade pip
& "$env:TEMP\jmo-pypi-smoke\Scripts\python.exe" -m pip install jellyfin-media-organizer==0.4.0
& "$env:TEMP\jmo-pypi-smoke\Scripts\jmo.exe" --version
```

Expect `Jellyfin Media Organizer 0.4.0`. Then:

```powershell
& "$env:TEMP\jmo-pypi-smoke\Scripts\jmo.exe" demo --output "$env:TEMP\jmo-pypi-demo"
& "$env:TEMP\jmo-pypi-smoke\Scripts\jmo.exe" doctor "$env:TEMP\jmo-pypi-demo\Shows" --destination-root "$env:TEMP\jmo-pypi-demo\OrganizedShows" --output-dir "$env:TEMP\jmo-pypi-demo\State\runs\initial" --cache-dir "$env:TEMP\jmo-pypi-demo\State\cache"
& "$env:TEMP\jmo-pypi-smoke\Scripts\jmo.exe" inspect "$env:TEMP\jmo-pypi-demo\State\runs\demo-run"
```

Remove the environment and demo output when finished:

```powershell
Remove-Item -Recurse -Force "$env:TEMP\jmo-pypi-smoke", "$env:TEMP\jmo-pypi-demo"
```

## Version strategy

Pin the installed package to the release you are checking
(`jellyfin-media-organizer==0.4.0` above). After a later release, update the
pin and the expected `jmo --version` string to the new
`jellyfin_show_organizer.__version__` value. Do not smoke-test a checkout
install (`pip install .`) when the goal is to verify the published artifact.

A passing smoke test confirms installation and the non-mutating CLI entry
points. It does not authorize apply.
