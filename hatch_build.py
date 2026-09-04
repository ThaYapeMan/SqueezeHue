"""Hatchling build hook: write current git commit hash into the package.

Runs automatically during `pip install .` (editable or wheel).  If git is not
available the file is written with COMMIT = "unknown", which is safe — the
version string in /api/status will just show "0.2.0+unknown".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.root,
            )
            git_hash = result.stdout.strip()
        except Exception:
            git_hash = "unknown"

        commit_file = Path(self.root) / "src" / "huesync" / "_commit.py"
        commit_file.write_text(f'COMMIT = "{git_hash}"\n')
