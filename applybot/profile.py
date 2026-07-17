"""Load and validate the user's profile YAML."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def load_profile(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(
            f"Profile not found: {p}\n"
            "Run `python -m applybot init` to create one from the template, "
            "then edit profile.yaml with your info."
        )
    with p.open(encoding="utf-8") as f:
        profile = yaml.safe_load(f) or {}

    resume = profile.get("documents", {}).get("resume", "")
    if resume and not Path(resume).exists():
        print(f"[warn] Resume file not found at: {resume} — file-upload fields will be skipped.")

    return profile


def profile_as_yaml(profile: dict) -> str:
    """Deterministic serialization so the prompt prefix stays cache-friendly."""
    return yaml.safe_dump(profile, sort_keys=True, allow_unicode=True)
