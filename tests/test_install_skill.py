"""Installer behavior tests use temporary directories, never the user's skills."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_skill


class InstallSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project with spaces"
        self.project.mkdir()
        (self.project / "assistant.py").write_text("# test fixture\n", encoding="utf-8")
        self.skills = self.base / "skills"

    def tearDown(self):
        self.temp.cleanup()

    def test_dry_run_has_no_filesystem_side_effects(self):
        result = install_skill.install(self.project, self.skills, dry_run=True)
        self.assertTrue(result["ready"])
        self.assertFalse(self.skills.exists())
        self.assertEqual(result["target"], str(self.skills / "personal-assistant"))

    def test_custom_name_renders_root_name_and_invocation(self):
        result = install_skill.install(self.project, self.skills, name="daily-work")
        target = Path(result["target"])
        body = (target / "SKILL.md").read_text(encoding="utf-8")
        metadata = (target / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertTrue(body.startswith("---\nname: daily-work\n"))
        self.assertIn(json.dumps(self.project.resolve().as_posix(), ensure_ascii=False), body)
        self.assertNotIn(install_skill.ROOT_TOKEN, body)
        self.assertIn("$daily-work", metadata)
        self.assertNotIn("$personal-assistant", metadata)
        self.assertIn("allow_implicit_invocation: true", metadata)
        self.assertFalse((self.project / "config.json").exists())
        self.assertFalse((self.project / "assistant.sqlite3").exists())

    def test_existing_skill_is_preserved_without_force(self):
        target = self.skills / "personal-assistant"
        target.mkdir(parents=True)
        sentinel = target / "private-note.txt"
        sentinel.write_bytes(b"existing personal content")
        with self.assertRaises(FileExistsError):
            install_skill.install(self.project, self.skills)
        preview = install_skill.install(self.project, self.skills, dry_run=True)
        self.assertFalse(preview["ready"])
        self.assertFalse(preview["would_replace"])
        self.assertEqual(sentinel.read_bytes(), b"existing personal content")
        self.assertEqual(list(self.skills.iterdir()), [target])

    def test_force_keeps_complete_backup_before_replacement(self):
        target = self.skills / "personal-assistant"
        (target / "notes").mkdir(parents=True)
        (target / "notes" / "old.bin").write_bytes(b"\x00\x01retained\xff")
        result = install_skill.install(self.project, self.skills, force=True)
        backup = Path(result["backup"])
        self.assertEqual(backup.parent, self.skills.parent / "skill-backups")
        self.assertEqual((backup / "notes" / "old.bin").read_bytes(), b"\x00\x01retained\xff")
        self.assertTrue((target / "SKILL.md").is_file())
        self.assertFalse((target / "notes").exists())

    def test_failed_replacement_restores_existing_skill(self):
        target = self.skills / "personal-assistant"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(b"original")
        real_rename = Path.rename

        def fail_final_rename(path, destination):
            if path.name.startswith(".personal-assistant.install-"):
                raise OSError("simulated atomic-install failure")
            return real_rename(path, destination)

        with patch.object(Path, "rename", fail_final_rename):
            with self.assertRaises(OSError):
                install_skill.install(self.project, self.skills, force=True)
        self.assertEqual((target / "SKILL.md").read_bytes(), b"original")
        self.assertEqual(list(self.skills.iterdir()), [target])

    def test_names_cannot_escape_skill_parent(self):
        for invalid in ("../elsewhere", "UPPER", "-start", "end-", "two--hyphens", "two words", "a" * 64):
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                install_skill.install(self.project, self.skills, name=invalid)
        self.assertFalse(self.skills.exists())

    def test_unknown_template_files_are_not_published_into_skill(self):
        template = self.base / "template"
        template.mkdir()
        (template / "credentials.json").write_text('{"fixture":"do not copy"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            install_skill.install(self.project, self.skills, template_dir=template)
        self.assertFalse(self.skills.exists())

    def test_default_location_respects_codex_home(self):
        custom = self.base / "isolated-codex"
        with patch.dict(os.environ, {"CODEX_HOME": str(custom)}):
            self.assertEqual(install_skill.default_skills_dir(), custom / "skills")
            result = install_skill.install(self.project, dry_run=True)
        self.assertEqual(Path(result["target"]), custom / "skills" / "personal-assistant")
        self.assertFalse(custom.exists())

    def test_cli_rejects_existing_skill_without_writing(self):
        target = self.skills / "personal-assistant"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("keep", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = install_skill.main(["--project-root", str(self.project), "--skills-dir", str(self.skills)])
        self.assertEqual(exit_code, 2)
        self.assertIn("not changed", json.loads(stderr.getvalue())["error"])
        self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
