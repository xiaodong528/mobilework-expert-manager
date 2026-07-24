from __future__ import annotations

import os
from pathlib import Path


def managed_generator_env(output_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["MOBILEWORK_EXPERT_MANAGER_HOST"] = "mobilework"
    env["MOBILEWORK_MY_EXPERTS_DIR"] = str(output_root.resolve())
    return env
