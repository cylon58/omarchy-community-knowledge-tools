"""Explicit, reversible exposure of bundled skills to supported agents."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .agents import AGENT_SKILL_PATHS, resolve_agent


SKILL_NAMES = (
    "omarchy-knowledge-research",
    "omarchy-knowledge-contribution",
)
CODEX_SKILLS = Path(".agents/skills")
LEGACY_CODEX_SKILLS = Path(".codex/skills")


class ExposureConflict(ValueError):
    """A destination or ownership record cannot be changed safely."""


def bundled_skills() -> dict[str, Path]:
    root = Path(__file__).resolve().parent / "skills"
    skills = {name: root / name for name in SKILL_NAMES}
    if any(not (source / "SKILL.md").is_file() for source in skills.values()):
        raise FileNotFoundError("Bundled Omarchy knowledge skills are unavailable; reinstall the toolkit")
    return skills


def receipt_path(home: str | Path) -> Path:
    return Path(home).absolute() / ".local/state/omarchy-knowledge-next/skill-exposure.json"


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _safe_parent(home: Path, parent: Path, *, create: bool) -> None:
    if not home.is_absolute() or not home.is_dir() or home.is_symlink():
        raise ExposureConflict(f"Home must be a real directory: {home}")
    try:
        relative = parent.relative_to(home)
    except ValueError as error:
        raise ExposureConflict(f"Skill destination escapes home: {parent}") from error
    current = home
    for part in relative.parts:
        current = current / part
        if _lexists(current):
            if current.is_symlink() or not current.is_dir():
                raise ExposureConflict(f"Skill parent must be a real directory: {current}")
        elif create:
            current.mkdir()


def _expected(home: Path, agent: str) -> list[dict[str, str]]:
    return [
        {
            "agent": agent,
            "name": name,
            "path": str(home / AGENT_SKILL_PATHS[agent] / name),
            "target": str(source),
        }
        for name, source in bundled_skills().items()
    ]


def _load_receipt(home: Path) -> dict:
    path = receipt_path(home)
    _safe_parent(home, path.parent, create=False)
    if not _lexists(path):
        return {"version": 1, "links": []}
    if path.is_symlink() or not path.is_file():
        raise ExposureConflict(f"Unsafe exposure receipt: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExposureConflict(f"Invalid exposure receipt: {path}") from error
    if type(value) is not dict or value.get("version") != 1 or type(value.get("links")) is not list:
        raise ExposureConflict(f"Invalid exposure receipt: {path}")
    if len(value["links"]) > len(SKILL_NAMES) * len(set(AGENT_SKILL_PATHS.values())):
        raise ExposureConflict(f"Invalid exposure receipt: {path}")
    recorded_paths = set()
    for item in value["links"]:
        if (
            type(item) is not dict
            or set(item) != {"agent", "name", "path", "target"}
            or item.get("agent") not in AGENT_SKILL_PATHS
            or item.get("name") not in SKILL_NAMES
            or not all(type(item[key]) is str for key in item)
        ):
            raise ExposureConflict(f"Invalid exposure receipt: {path}")
        expected_path = home / AGENT_SKILL_PATHS[item["agent"]] / item["name"]
        target = Path(item["target"])
        if (
            Path(item["path"]) != expected_path
            or not target.is_absolute()
            or target.parts[-3:] != ("omarchy_knowledge", "skills", item["name"])
        ):
            raise ExposureConflict(f"Invalid exposure receipt: {path}")
        if item["path"] in recorded_paths:
            raise ExposureConflict(f"Invalid exposure receipt: {path}")
        recorded_paths.add(item["path"])
    return value


def _write_receipt(home: Path, receipt: dict) -> None:
    path = receipt_path(home)
    _safe_parent(home, path.parent, create=bool(receipt["links"]))
    if not receipt["links"]:
        if _lexists(path):
            if path.is_symlink() or not path.is_file():
                raise ExposureConflict(f"Unsafe exposure receipt: {path}")
            path.unlink()
        return
    payload = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".skill-exposure-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if _lexists(temporary):
            temporary.unlink()


def _classify(home: Path, item: dict, owned: dict[tuple[str, str], dict]) -> str:
    path = Path(item["path"])
    key = (item["path"], item["target"])
    legacy = home / LEGACY_CODEX_SKILLS / item["name"]
    _safe_parent(home, path.parent, create=False)
    if item["agent"] == "codex":
        _safe_parent(home, legacy.parent, create=False)
        if _lexists(legacy):
            return "legacy-occupied"
    if key in owned and path.is_symlink() and os.readlink(path) == item["target"]:
        return "already-installed"
    if _lexists(path):
        return "occupied"
    return "would-install"


def plan_for_codex(home: Path) -> dict:
    return plan_for_agent(home, "codex")


def plan_for_agent(home: Path, agent: str, *, detect_command=None) -> dict:
    home = Path(home).absolute()
    try:
        agent = resolve_agent(agent, **({"command": detect_command} if detect_command else {}))
    except ValueError as error:
        raise ExposureConflict(str(error)) from error
    receipt = _load_receipt(home)
    owned = {(item["path"], item["target"]): item for item in receipt["links"]}
    actions = _expected(home, agent)
    for item in actions:
        item["result"] = _classify(home, item, owned)
    return {"mode": "dry-run", "actions": actions}


def install_for_codex(home: Path) -> dict:
    return install_for_agent(home, "codex")


def install_for_agent(home: Path, agent: str, *, detect_command=None) -> dict:
    home = Path(home).absolute()
    try:
        agent = resolve_agent(agent, **({"command": detect_command} if detect_command else {}))
    except ValueError as error:
        raise ExposureConflict(str(error)) from error
    receipt = _load_receipt(home)
    owned = {(item["path"], item["target"]): item for item in receipt["links"]}
    actions = _expected(home, agent)
    for item in actions:
        result = _classify(home, item, owned)
        if result not in {"would-install", "already-installed"}:
            conflict = home / LEGACY_CODEX_SKILLS / item["name"] if result == "legacy-occupied" else Path(item["path"])
            raise ExposureConflict(f"Refusing to overwrite or duplicate existing skill: {conflict}")
    _safe_parent(home, home / AGENT_SKILL_PATHS[agent], create=False)
    created: list[Path] = []
    try:
        for item in actions:
            path = Path(item["path"])
            key = (item["path"], item["target"])
            _safe_parent(home, path.parent, create=False)
            if key in owned and path.is_symlink() and os.readlink(path) == item["target"]:
                item["result"] = "already-installed"
                continue
            _safe_parent(home, path.parent, create=True)
            path.symlink_to(item["target"], target_is_directory=True)
            created.append(path)
            item["result"] = "installed"
            if key not in owned:
                receipt["links"].append({field: item[field] for field in ("agent", "name", "path", "target")})
        _write_receipt(home, receipt)
    except Exception:
        for path in reversed(created):
            _safe_parent(home, path.parent, create=False)
            if path.is_symlink():
                path.unlink()
        raise
    return {"mode": "install", "actions": actions}


def remove_for_codex(home: Path) -> dict:
    return remove_for_agent(home, "codex")


def remove_for_agent(home: Path, agent: str, *, detect_command=None) -> dict:
    home = Path(home).absolute()
    try:
        agent = resolve_agent(agent, **({"command": detect_command} if detect_command else {}))
    except ValueError as error:
        raise ExposureConflict(str(error)) from error
    receipt = _load_receipt(home)
    retained = []
    actions = []
    recorded = set()
    for entry in receipt["links"]:
        item = dict(entry)
        path = Path(item["path"])
        recorded.add(item["path"])
        _safe_parent(home, path.parent, create=False)
        if not _lexists(path):
            item["result"] = "already-absent"
        elif path.is_symlink() and os.readlink(path) == item["target"]:
            path.unlink()
            item["result"] = "removed"
        else:
            item["result"] = "preserved-modified"
            retained.append(entry)
        actions.append(item)
    for item in _expected(home, agent):
        if item["path"] not in recorded:
            item["result"] = "not-owned"
            actions.append(item)
    receipt["links"] = retained
    _write_receipt(home, receipt)
    return {"mode": "remove", "actions": actions}
