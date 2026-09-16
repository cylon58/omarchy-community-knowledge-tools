"""Explicit, reversible exposure of the bundled portable skills."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile


SKILL_NAMES = (
    "omarchy-knowledge-research",
    "omarchy-knowledge-contribution",
)
AGENT_PATHS = {
    "generic": Path(".agents/skills"),
    "codex": Path(".codex/skills"),
    "claude": Path(".claude/skills"),
    "pi": Path(".pi/agent/skills"),
    "hermes": Path(".hermes/skills"),
    "antigravity": Path(".gemini/config/skills"),
}
HERMES_PROFILE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
MAX_OWNED_LINKS = 24


class ExposureConflict(ValueError):
    """An exposure destination is already occupied or tracking is unsafe."""


def bundled_skills() -> dict[str, Path]:
    root = Path(__file__).resolve().parent / "skills"
    result = {name: root / name for name in SKILL_NAMES}
    if any(not (source / "SKILL.md").is_file() for source in result.values()):
        raise FileNotFoundError("Bundled Omarchy knowledge skills are unavailable; reinstall the toolkit")
    return result


def receipt_path(home: str | Path) -> Path:
    return Path(home) / ".local/state/omarchy-community-knowledge/skill-exposure.json"


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _load_receipt(home: Path) -> dict:
    path = receipt_path(home)
    if not _lexists(path):
        return {"version": 1, "links": []}
    if path.is_symlink() or not path.is_file():
        raise ExposureConflict(f"Unsafe exposure receipt: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExposureConflict(f"Invalid exposure receipt: {path}") from error
    if (type(value) is not dict or value.get("version") != 1
            or type(value.get("links")) is not list or len(value["links"]) > MAX_OWNED_LINKS):
        raise ExposureConflict(f"Invalid exposure receipt: {path}")
    for item in value["links"]:
        if (type(item) is not dict
                or set(item) not in ({"agent", "name", "path", "target"},
                                     {"agent", "name", "path", "target", "profile"})
                or item["agent"] not in AGENT_PATHS or item["name"] not in SKILL_NAMES
                or not all(type(item[key]) is str for key in item)):
            raise ExposureConflict(f"Invalid exposure receipt: {path}")
        profile = item.get("profile")
        if profile is not None:
            if (item["agent"] != "hermes" or type(profile) is not str
                    or HERMES_PROFILE_PATTERN.fullmatch(profile) is None):
                raise ExposureConflict(f"Invalid exposure receipt: {path}")
            expected_path = home / ".hermes/profiles" / profile / "skills" / item["name"]
        else:
            expected_path = home / AGENT_PATHS[item["agent"]] / item["name"]
        target = Path(item["target"])
        if (Path(item["path"]) != expected_path or not target.is_absolute()
                or target.parts[-3:] != ("omarchy_knowledge", "skills", item["name"])):
            raise ExposureConflict(f"Invalid exposure receipt: {path}")
    return value


def _write_receipt(home: Path, receipt: dict) -> None:
    path = receipt_path(home)
    if not receipt["links"]:
        if _lexists(path):
            if path.is_symlink() or not path.is_file():
                raise ExposureConflict(f"Unsafe exposure receipt: {path}")
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ExposureConflict(f"Unsafe exposure receipt directory: {path.parent}")
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


def _hermes_profile_root(home: Path, profile: str) -> Path:
    if type(profile) is not str or HERMES_PROFILE_PATTERN.fullmatch(profile) is None:
        raise ValueError(f"Invalid Hermes profile name: {profile!r}")
    hermes = home / ".hermes"
    profiles = hermes / "profiles"
    selected = profiles / profile
    if any(path.is_symlink() for path in (hermes, profiles, selected)):
        raise ExposureConflict(f"Hermes profile path may not contain symlinks: {selected}")
    if not selected.is_dir():
        raise ExposureConflict(f"Hermes profile does not exist: {selected}")
    skills = selected / "skills"
    if _lexists(skills) and (skills.is_symlink() or not skills.is_dir()):
        raise ExposureConflict(f"Hermes profile skills path is not a real directory: {skills}")
    return skills


def _planned(home: Path, agents: tuple[str, ...],
             hermes_profiles: tuple[str, ...]) -> list[dict[str, str]]:
    if not agents and not hermes_profiles:
        raise ValueError("Select at least one agent explicitly")
    unknown = set(agents) - set(AGENT_PATHS)
    if unknown:
        raise ValueError(f"Unsupported agent selection: {', '.join(sorted(unknown))}")
    if len(set(hermes_profiles)) > 8:
        raise ValueError("At most eight Hermes profiles may be selected")
    if 2 * (len(set(agents)) + len(set(hermes_profiles))) > MAX_OWNED_LINKS:
        raise ValueError("Too many agent profiles selected for one exposure receipt")
    skills = bundled_skills()
    actions = []
    for agent in dict.fromkeys(agents):
        for name, source in skills.items():
            actions.append({
                "agent": agent,
                "name": name,
                "path": str(home / AGENT_PATHS[agent] / name),
                "target": str(source),
            })
    for profile in dict.fromkeys(hermes_profiles):
        profile_skills = _hermes_profile_root(home, profile)
        for name, source in skills.items():
            actions.append({
                "agent": "hermes",
                "profile": profile,
                "name": name,
                "path": str(profile_skills / name),
                "target": str(source),
            })
    return actions


def expose_skills(home: str | Path, *, agents: tuple[str, ...] = (),
                  hermes_profiles: tuple[str, ...] = (), mode: str = "dry-run") -> dict:
    """Plan, install, or remove only selected skill links.

    A receipt records the exact symlink path and target. Removal preserves an
    artifact if either value no longer matches what this installer created.
    """
    if mode not in {"dry-run", "install", "remove"}:
        raise ValueError("Mode must be dry-run, install, or remove")
    home = Path(home).absolute()
    actions = _planned(home, agents, hermes_profiles)
    receipt = _load_receipt(home)
    owned = {(item["path"], item["target"]): item for item in receipt["links"]}

    if mode == "dry-run":
        for item in actions:
            path = Path(item["path"])
            key = (item["path"], item["target"])
            item["result"] = ("already-installed" if key in owned and path.is_symlink()
                              and os.readlink(path) == item["target"] else
                              "occupied" if _lexists(path) else "would-install")
        return {"mode": mode, "actions": actions}

    if mode == "install":
        prospective_links = []
        prospective_keys = set()
        for entry in receipt["links"]:
            key = (entry["path"], entry["target"])
            if key not in prospective_keys:
                prospective_links.append(dict(entry))
                prospective_keys.add(key)
        for item in actions:
            key = (item["path"], item["target"])
            if key not in prospective_keys:
                prospective_links.append(dict(item))
                prospective_keys.add(key)
        if len(prospective_links) > MAX_OWNED_LINKS:
            raise ExposureConflict("Selected exposures exceed the owned-link receipt limit")
        for item in actions:
            path = Path(item["path"])
            key = (item["path"], item["target"])
            if _lexists(path):
                if key in owned and path.is_symlink() and os.readlink(path) == item["target"]:
                    continue
                raise ExposureConflict(f"Refusing to overwrite existing skill destination: {path}")
        created = []
        try:
            for item in actions:
                path = Path(item["path"])
                key = (item["path"], item["target"])
                if key in owned:
                    item["result"] = "already-installed"
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(item["target"], target_is_directory=True)
                created.append(path)
                item["result"] = "installed"
            receipt["links"] = prospective_links
            _write_receipt(home, receipt)
        except Exception:
            for path in reversed(created):
                if path.is_symlink():
                    path.unlink()
            raise
        return {"mode": mode, "actions": actions}

    retained = []
    selected = {(agent, None) for agent in agents}
    selected.update(("hermes", profile) for profile in hermes_profiles)
    removal_actions = []
    recorded_paths = set()
    for entry in receipt["links"]:
        if (entry["agent"], entry.get("profile")) not in selected:
            retained.append(entry)
            continue
        item = dict(entry)
        removal_actions.append(item)
        recorded_paths.add(entry["path"])
        path = Path(entry["path"])
        if not _lexists(path):
            item["result"] = "already-absent"
        elif path.is_symlink() and os.readlink(path) == entry["target"]:
            path.unlink()
            item["result"] = "removed"
        else:
            item["result"] = "preserved-modified"
            retained.append(entry)
    for item in actions:
        if item["path"] not in recorded_paths:
            item["result"] = "not-owned"
            removal_actions.append(item)
    receipt["links"] = retained
    _write_receipt(home, receipt)
    return {"mode": mode, "actions": removal_actions}
