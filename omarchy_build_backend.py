"""Tiny dependency-free wheel backend for this deliberately small Python project."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import zipfile


NAME = "omarchy_community_knowledge_tools"
VERSION = "0.1.0"
DIST_INFO = f"{NAME}-{VERSION}.dist-info"
WHEEL = f"{NAME}-{VERSION}-py3-none-any.whl"


def _metadata():
    return {
        f"{DIST_INFO}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: omarchy-community-knowledge-tools\n"
            f"Version: {VERSION}\n"
            "Summary: Local inert Omarchy community knowledge toolkit\n"
            "Requires-Python: >=3.11\n"
            "Requires-Dist: jsonschema==4.26.0\n\n"
        ).encode(),
        f"{DIST_INFO}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: omarchy-build-backend\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n\n"
        ).encode(),
        f"{DIST_INFO}/entry_points.txt": (
            "[console_scripts]\n"
            "omarchy-knowledge = omarchy_knowledge.cli:main\n"
            "omarchy-knowledge-panel = omarchy_knowledge.companion:panel_main\n"
        ).encode(),
    }


def _contents():
    root = Path(__file__).parent
    members = [root / "knowledge.py", root / "projections.py"]
    members.extend(sorted((root / "omarchy_knowledge").glob("*.py")))
    members.extend(sorted((root / "omarchy_knowledge" / "skills").glob("*/SKILL.md")))
    members.extend(sorted((root / "schemas" / "v1").glob("*.json")))
    result = {path.relative_to(root).as_posix(): path.read_bytes() for path in members}
    result.update(_metadata())
    return result


def get_requires_for_build_wheel(config_settings=None):
    return []


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    files = _contents()
    record_rows = []
    for name, content in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        record_rows.append(f"{name},sha256={digest},{len(content)}")
    record_rows.append(f"{DIST_INFO}/RECORD,,")
    files[f"{DIST_INFO}/RECORD"] = ("\n".join(record_rows) + "\n").encode()
    destination = Path(wheel_directory) / WHEEL
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    return WHEEL
