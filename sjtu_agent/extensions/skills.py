"""Prompt-only skill loading."""

from __future__ import annotations

import os
from pathlib import Path

from sjtu_agent.paths import DATA_DIR, PACKAGE_ROOT, PROJECT_ROOT, CONFIG_PATH, read_json_safe


def _skill_config() -> dict:
    cfg = read_json_safe(CONFIG_PATH, {})
    raw = cfg.get("skills", {})
    return raw if isinstance(raw, dict) else {}


def _expand_path(raw: str) -> Path:
    raw = os.path.expandvars(os.path.expanduser(raw))
    return Path(raw)


def _skill_dirs() -> list[Path]:
    cfg = _skill_config()
    dirs = [
        PACKAGE_ROOT / "skills",
        PROJECT_ROOT / "skills",
        DATA_DIR / "skills",
    ]
    for item in cfg.get("dirs", []) if isinstance(cfg.get("dirs", []), list) else []:
        if isinstance(item, str) and item.strip():
            dirs.append(_expand_path(item.strip()))
    seen: set[str] = set()
    result: list[Path] = []
    for path in dirs:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def enabled_skill_names() -> list[str]:
    enabled = _skill_config().get("enabled", [])
    if isinstance(enabled, str):
        return [enabled]
    if isinstance(enabled, list):
        return [str(x) for x in enabled if str(x).strip()]
    return []


def _find_skill_file(name: str) -> Path | None:
    for base in _skill_dirs():
        direct = base / name / "SKILL.md"
        if direct.exists():
            return direct
        lower = base / name.lower() / "SKILL.md"
        if lower.exists():
            return lower
    return None


def _all_skill_files() -> list[Path]:
    files: list[Path] = []
    for base in _skill_dirs():
        if not base.exists():
            continue
        files.extend(sorted(base.glob("*/SKILL.md")))
    return files


def build_skill_prompt() -> str:
    names = enabled_skill_names()
    if not names:
        return ""

    skill_files = _all_skill_files() if "*" in names else [p for n in names if (p := _find_skill_file(n))]
    parts: list[str] = []
    for path in skill_files:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        parts.append(f"### Skill: {path.parent.name}\n{text}")

    if not parts:
        return ""
    return "\n\n## Enabled Skills\n" + "\n\n".join(parts)
