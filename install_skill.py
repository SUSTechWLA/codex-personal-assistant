"""Install the portable Codex skill without changing accounts or schedules.

Only Python's standard library is used. Existing skills require --force and
are backed up before replacement. Use --dry-run to inspect paths first.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid


SOURCE_NAME = "personal-assistant"
ROOT_TOKEN = "__ASSISTANT_ROOT__"
ALLOWED_SUFFIXES = {".md", ".yaml"}


def default_skills_dir():
    codex_dir = os.environ.get("CODEX_HOME")
    return (Path(codex_dir).expanduser() if codex_dir else Path.home() / ".codex") / "skills"


def valid_name(value):
    if not isinstance(value, str) or "--" in value or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("Skill name must contain 1-63 lowercase letters, digits or hyphens; start and end with a letter or digit.")
    return value


def render_files(template_dir, project_root, name):
    """Render only the project's reviewed text resources, never local data."""
    template_dir, project_root = Path(template_dir), Path(project_root).resolve()
    if not template_dir.is_dir() or template_dir.is_symlink():
        raise ValueError("Skill template directory is missing or is a symlink.")
    if not (project_root / "assistant.py").is_file():
        raise ValueError("Project root must contain assistant.py.")
    valid_name(name)
    files = {}
    for source in sorted(template_dir.rglob("*")):
        if source.is_symlink():
            raise ValueError("Skill template symlinks are not supported.")
        if not source.is_file():
            continue
        if source.suffix not in ALLOWED_SUFFIXES:
            raise ValueError("Unexpected file in skill template: " + source.name)
        relative = source.relative_to(template_dir)
        content = source.read_text(encoding="utf-8-sig")
        content = content.replace(ROOT_TOKEN, json.dumps(project_root.as_posix(), ensure_ascii=False))
        content = content.replace("$" + SOURCE_NAME, "$" + name)
        if relative.as_posix() == "SKILL.md":
            pattern = r"\A---\r?\nname: personal-assistant\r?\n"
            content, count = re.subn(pattern, "---\nname: " + name + "\n", content, count=1)
            if count != 1:
                raise ValueError("Skill template has an unexpected frontmatter name.")
        if ROOT_TOKEN in content:
            raise ValueError("A project-root template token was not rendered.")
        files[relative] = content
    if Path("SKILL.md") not in files or Path("agents/openai.yaml") not in files:
        raise ValueError("Skill template requires SKILL.md and agents/openai.yaml.")
    return files


def install(project_root, skills_dir=None, name=SOURCE_NAME, *, dry_run=False, force=False, template_dir=None):
    project_root = Path(project_root).expanduser().resolve()
    skills_dir = (Path(skills_dir).expanduser() if skills_dir is not None else default_skills_dir()).resolve()
    name = valid_name(name)
    target = skills_dir / name
    # Validate the absolute target before any move or recursive cleanup.
    if target.parent != skills_dir:
        raise ValueError("Skill target must be a direct child of the skills directory.")
    if target.is_symlink():
        raise ValueError("Refusing to replace a symlinked skill; choose another name.")
    if target.exists() and not target.is_dir():
        raise ValueError("Skill target exists and is not a directory.")
    template_dir = Path(template_dir) if template_dir is not None else Path(__file__).resolve().parent / "skill" / SOURCE_NAME
    files = render_files(template_dir, project_root, name)
    exists = target.exists()
    result = {
        "mode": "dry-run" if dry_run else "install",
        "project_root": str(project_root),
        "skill_name": name,
        "target": str(target),
        "files": [relative.as_posix() for relative in files],
        "existing_skill": exists,
        "would_replace": exists and force,
        "backup": None,
    }
    if dry_run:
        result["ready"] = not exists or force
        if exists and not force:
            result["note"] = "Existing skill will be preserved. Choose --name or explicitly use --force to back up and replace it."
        return result
    if exists and not force:
        raise FileExistsError("Skill already exists; it was not changed. Choose --name or inspect --dry-run before using --force.")
    skills_dir.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix="." + name + ".install-", dir=skills_dir))
    backup = None
    try:
        for relative, content in files.items():
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        # Check again so a target created during staging is never silently lost.
        if target.is_symlink():
            raise ValueError("Skill target became a symlink; installation stopped.")
        if target.exists():
            if not force or not target.is_dir():
                raise FileExistsError("Skill target appeared or changed during installation; it was not overwritten.")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            # Keep backups outside skills discovery, so an old SKILL.md cannot
            # be loaded as a duplicate installed skill.
            backup_dir = skills_dir.parent / "skill-backups"
            if backup_dir.is_symlink():
                raise ValueError("Refusing a symlinked skill-backups directory.")
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / (name + "-" + stamp + "-" + uuid.uuid4().hex[:8])
            if backup.parent.resolve() != backup_dir.resolve() or backup.exists():
                raise ValueError("Backup location is not safe.")
            target.rename(backup)
        staged.rename(target)
        result["backup"] = str(backup) if backup is not None else None
        result["installed"] = True
        return result
    except Exception:
        if backup is not None and backup.exists() and not target.exists():
            backup.rename(target)
        raise
    finally:
        if staged.exists():
            resolved_stage = staged.resolve()
            if resolved_stage.parent != skills_dir or not resolved_stage.name.startswith("." + name + ".install-"):
                raise ValueError("Refusing cleanup outside the installer staging directory.")
            shutil.rmtree(resolved_stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent, help="Assistant project directory containing assistant.py.")
    parser.add_argument("--skills-dir", type=Path, help="Parent directory for skills; default: $CODEX_HOME/skills or ~/.codex/skills.")
    parser.add_argument("--name", default=SOURCE_NAME, help="Installed skill name; use a different name to preserve an existing personal skill.")
    parser.add_argument("--dry-run", action="store_true", help="Show the rendered destination and file list without writing anything.")
    parser.add_argument("--force", action="store_true", help="Explicitly replace an existing directory after preserving a backup.")
    args = parser.parse_args(argv)
    try:
        result = install(args.project_root, args.skills_dir, args.name, dry_run=args.dry_run, force=args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
