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

No package publication authorizes a media apply. The plan, review, preflight,
approval-token, journal, and rollback contracts remain unchanged.
