"""Supported agent names, skill locations, and Omarchy default detection."""
from __future__ import annotations

from pathlib import Path
import subprocess


AGENT_SKILL_PATHS = {
    "codex": Path(".agents/skills"),
    "claude": Path(".claude/skills"),
    "opencode": Path(".agents/skills"),
    "gemini": Path(".agents/skills"),
    "agy": Path(".gemini/antigravity-cli/skills"),
}
AGENT_CHOICES = ("auto", *AGENT_SKILL_PATHS)


def _default_detection_command(argv):
    return subprocess.run(
        argv, check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=10,
    ).stdout


def resolve_agent(agent: str, *, command=_default_detection_command) -> str:
    """Resolve ``auto`` without changing Omarchy's configured default."""
    if agent != "auto":
        if agent not in AGENT_SKILL_PATHS:
            raise ValueError(f"Unsupported agent {agent!r}; pass --agent with a supported target")
        return agent
    try:
        detected = command(["omarchy-default-agent"])
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "Omarchy's default agent could not be detected; pass --agent "
            "codex, claude, opencode, gemini, or agy"
        ) from error
    if hasattr(detected, "stdout"):
        detected = detected.stdout
    detected = str(detected).strip().casefold()
    if not detected:
        raise ValueError(
            "Omarchy's default agent could not be detected; pass --agent "
            "codex, claude, opencode, gemini, or agy"
        )
    if detected not in AGENT_SKILL_PATHS:
        raise ValueError(
            f"Omarchy's default agent {detected!r} is not supported; pass --agent "
            "codex, claude, opencode, gemini, or agy, or install the skills manually"
        )
    return detected
