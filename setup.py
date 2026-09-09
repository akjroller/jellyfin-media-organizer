"""Stamp wheels and sdists without modifying the source checkout."""

import json
import runpy
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = Path(__file__).resolve().parent
identity_tools = runpy.run_path(
    str(ROOT / "jellyfin_show_organizer" / "artifact_identity.py")
)
BUILD_IDENTITY = identity_tools["build_identity"](ROOT)


def stamp(package):
    # Capture before setuptools creates its temporary sdist tree in the checkout.
    identity = BUILD_IDENTITY
    if identity is not None:
        (package / "_build_identity.json").write_text(
            json.dumps(identity, sort_keys=True) + "\n", encoding="utf-8"
        )


class BuildPy(build_py):
    def run(self):
        super().run()
        stamp(Path(self.build_lib) / "jellyfin_show_organizer")


class Sdist(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        stamp(Path(base_dir) / "jellyfin_show_organizer")


setup(cmdclass={"build_py": BuildPy, "sdist": Sdist})
