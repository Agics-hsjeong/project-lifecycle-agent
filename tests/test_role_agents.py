import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import lifecycle, package_plugin, role_agents, starter, storage

try:
    import tomllib
except ImportError:
    tomllib = None


class RoleAgentTests(unittest.TestCase):
    def setUp(self):
        self.base = starter.ROOT / ".test-output"
        self.base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.base)
        self.workspace = Path(self.temp.name)
        self.project = starter.init_project(self.workspace, "role-demo", "역할 검증", "si")

    def tearDown(self):
        self.assertTrue(self.workspace.resolve().is_relative_to(self.base.resolve()))
        self.temp.cleanup()

    def test_contracts_cover_requested_roles_and_old_proposal_flow(self):
        roles = role_agents.catalog()
        self.assertEqual({r["id"] for r in roles}, {"po", "spec", "ia", "design-system", "ui", "ux-writer",
            "architect", "database", "backend", "frontend", "devops", "qa", "security", "technical-writer",
            "orchestrator", "reviewer", "proposal-strategist", "proposal-producer"})
        self.assertEqual(role_agents.resolve("PO")["id"], "po")
        self.assertEqual(role_agents.resolve("DB")["id"], "database")
        self.assertEqual(role_agents.resolve("ieum-backend")["id"], "backend")
        self.assertEqual(role_agents.resolve("보안")["id"], "security")
        with self.assertRaises(ValueError):
            role_agents.resolve("../../private")
        for role in roles:
            self.assertTrue(all(role[k] for k in ("inputs", "outputs", "checks", "boundaries", "next_roles")))

    def test_catalog_rejects_cross_role_alias_and_dangling_handoff(self):
        catalog = json.loads(role_agents.CATALOG.read_text(encoding="utf-8"))
        duplicate = copy.deepcopy(catalog)
        duplicate["roles"][1]["aliases"].append("PO")
        with patch.object(Path, "read_text", return_value=json.dumps(duplicate)), self.assertRaisesRegex(ValueError, "별칭"):
            role_agents.catalog()
        catalog["roles"][0]["next_roles"].append("missing")
        with patch.object(Path, "read_text", return_value=json.dumps(catalog)), self.assertRaisesRegex(ValueError, "인수인계"):
            role_agents.catalog()

    def test_native_definitions_are_synced_self_contained_and_inherit_models(self):
        self.assertTrue(role_agents.build(check=True)["ready"])
        for role in role_agents.catalog():
            claude = role_agents.render_claude(role)
            header, body = claude.split("---\n", 2)[1:]
            metadata = {key: json.loads(value) for key, value in (line.split(": ", 1) for line in header.strip().splitlines())}
            self.assertEqual(metadata["name"], "ieum-" + role["id"])
            self.assertEqual(metadata["model"], "inherit")
            self.assertNotIn("permissionMode", metadata)
            self.assertNotIn("hooks", metadata)
            self.assertEqual(body.strip(), role_agents.prompt(role["id"]).strip())
            self.assertIn("주 에이전트", body)
            self.assertIn("accept/revise/blocked", body)
            if role["review_only"]:
                self.assertEqual(metadata["tools"], "Read, Glob, Grep")
            if tomllib:
                config = tomllib.loads(role_agents.render_codex(role))
                self.assertEqual(config["developer_instructions"], role_agents.prompt(role["id"]))
                self.assertEqual(set(config), {"name", "description", "developer_instructions"})

    def test_codex_install_preserves_existing_settings_and_is_idempotent(self):
        config = self.project / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text('# existing settings\n', encoding="utf-8")
        other = config.parent / "agents/other.toml"
        other.parent.mkdir()
        other.write_text('# unrelated role\n', encoding="utf-8")
        preview = role_agents.codex_preview(self.project)
        self.assertEqual(len(preview["changes"]), 18)
        self.assertEqual(preview["conflicts"], [])
        self.assertFalse((self.project / ".codex/agents/ieum-po.toml").exists())
        result = role_agents.codex_install(self.project, preview)
        self.assertFalse(result["host_loaded"])
        self.assertEqual(len(result["written"]), 18)
        self.assertEqual(config.read_text(encoding="utf-8"), '# existing settings\n')
        self.assertEqual(other.read_text(encoding="utf-8"), '# unrelated role\n')
        self.assertEqual(lifecycle.events(self.project)[-1]["action"], "ieum-agents-install")
        before = lifecycle.events(self.project)
        rerun = role_agents.codex_install(self.project, role_agents.codex_preview(self.project))
        self.assertTrue(rerun["unchanged"])
        self.assertEqual(before, lifecycle.events(self.project))

    def test_conflicting_file_prevents_all_install_writes(self):
        path = self.project / ".codex/agents/ieum-po.toml"
        path.parent.mkdir(parents=True)
        path.write_text("user customization", encoding="utf-8")
        preview = role_agents.codex_preview(self.project)
        self.assertEqual(preview["conflicts"], [".codex/agents/ieum-po.toml"])
        with self.assertRaisesRegex(ValueError, "기존 역할"):
            role_agents.codex_install(self.project, preview)
        self.assertEqual(path.read_text(encoding="utf-8"), "user customization")
        self.assertEqual(len(list(path.parent.glob("*.toml"))), 1)
        self.assertEqual(lifecycle.events(self.project), [])

    def test_stale_project_or_target_file_is_rejected(self):
        preview = role_agents.codex_preview(self.project)
        lifecycle.apply_patch(self.project, {"expected_revision": None, "project": {"objective": "new scope"}})
        with self.assertRaisesRegex(ValueError, "미리보기"):
            role_agents.codex_install(self.project, preview)
        fresh = role_agents.codex_preview(self.project)
        path = self.project / ".codex/agents/ieum-po.toml"
        path.parent.mkdir(parents=True)
        path.write_text("modified after preview", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "미리보기"):
            role_agents.codex_install(self.project, fresh)
        self.assertEqual(len(list(path.parent.glob("*.toml"))), 1)

    def test_native_source_changes_invalidate_preview(self):
        preview = role_agents.codex_preview(self.project)
        original = role_agents.render_codex
        with patch.object(role_agents, "render_codex", side_effect=lambda role: original(role) + "# changed\n"):
            with self.assertRaisesRegex(ValueError, "미리보기"):
                role_agents.codex_install(self.project, preview)
        self.assertFalse((self.project / ".codex").exists())

    def test_source_change_during_install_is_rejected_without_writes(self):
        preview = role_agents.codex_preview(self.project)
        original = role_agents.render_codex
        calls = 0
        def changed_later(role):
            nonlocal calls
            calls += 1
            return original(role) + ("# changed during install\n" if calls > 18 else "")
        with patch.object(role_agents, "render_codex", side_effect=changed_later), self.assertRaisesRegex(ValueError, "설치 중"):
            role_agents.codex_install(self.project, preview)
        self.assertFalse((self.project / ".codex").exists())
        self.assertEqual(lifecycle.events(self.project), [])

    def test_install_rolls_back_if_a_file_write_fails(self):
        preview = role_agents.codex_preview(self.project)
        original = storage.atomic_write
        failed = False
        def fail_one(path, content):
            nonlocal failed
            if Path(path).name == "ieum-spec.toml" and not failed:
                failed = True
                raise OSError("simulated disk write failure")
            return original(path, content)
        with patch.object(storage, "atomic_write", side_effect=fail_one), self.assertRaises(OSError):
            role_agents.codex_install(self.project, preview)
        self.assertTrue(failed)
        self.assertEqual(list((self.project / ".codex/agents").glob("*.toml")), [])
        self.assertEqual(lifecycle.events(self.project), [])
        self.assertFalse((self.project / "state/pending.json").exists())

    def test_linked_target_and_non_project_are_rejected(self):
        original = Path.is_symlink
        target = self.project / ".codex"
        with patch.object(Path, "is_symlink", lambda p: p == target or original(p)), self.assertRaisesRegex(ValueError, "연결된"):
            role_agents.codex_preview(self.project)
        with self.assertRaises((ValueError, OSError)):
            role_agents.codex_preview(self.workspace)
        self.assertFalse((self.workspace / "state").exists())
        with self.assertRaisesRegex(ValueError, "고객 프로젝트"):
            role_agents.codex_preview(starter.BLUEPRINT)
        self.assertFalse((starter.BLUEPRINT / "state").exists())

    def test_claude_shortcut_install_preserves_settings_and_is_idempotent(self):
        settings = self.project / ".claude/settings.json"
        settings.parent.mkdir()
        settings.write_text('{"permissions": {}}', encoding="utf-8")
        preview = role_agents.claude_preview(self.project)
        self.assertEqual(preview["changes"], [".claude/skills/ieum/SKILL.md"])
        self.assertEqual(preview["command"], "/ieum")
        result = role_agents.claude_install(self.project, preview)
        self.assertFalse(result["host_loaded"])
        content = (self.project / ".claude/skills/ieum/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: ieum", content)
        self.assertIn("project-lifecycle-agent:ieum", content)
        self.assertIn("$ARGUMENTS", content)
        self.assertEqual(settings.read_text(encoding="utf-8"), '{"permissions": {}}')
        events = lifecycle.events(self.project)
        self.assertEqual(events[-1]["action"], "ieum-alias-install")
        again = role_agents.claude_install(self.project, role_agents.claude_preview(self.project))
        self.assertTrue(again["unchanged"])
        self.assertEqual(events, lifecycle.events(self.project))

    def test_claude_existing_skill_or_legacy_command_is_not_shadowed(self):
        legacy = self.project / ".claude/commands/ieum.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("user command", encoding="utf-8")
        preview = role_agents.claude_preview(self.project)
        self.assertIn(".claude/commands/ieum.md", preview["conflicts"])
        with self.assertRaisesRegex(ValueError, "기존 역할"):
            role_agents.claude_install(self.project, preview)
        self.assertFalse((self.project / ".claude/skills/ieum/SKILL.md").exists())
        self.assertEqual(legacy.read_text(encoding="utf-8"), "user command")
        legacy.unlink()
        skill = self.project / ".claude/skills/ieum/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("user skill", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "기존 역할"):
            role_agents.claude_install(self.project, role_agents.claude_preview(self.project))
        self.assertEqual(skill.read_text(encoding="utf-8"), "user skill")

    def test_claude_legacy_command_added_after_preview_invalidates_install(self):
        preview = role_agents.claude_preview(self.project)
        legacy = self.project / ".claude/commands/ieum.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("created after preview", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "미리보기"):
            role_agents.claude_install(self.project, preview)
        self.assertFalse((self.project / ".claude/skills/ieum/SKILL.md").exists())

    def test_claude_cli_preview_and_install_from_customer_folder(self):
        command = [sys.executable, "-X", "utf8", str(starter.ROOT / "tools/role_agents.py")]
        preview = subprocess.run([*command, "claude-preview", str(self.project)], cwd=self.workspace,
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        request = self.workspace / "preview.json"
        request.write_text(preview.stdout, encoding="utf-8")
        install = subprocess.run([*command, "claude-install", str(self.project), "--file", str(request)],
            cwd=self.workspace, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
        self.assertEqual(json.loads(install.stdout)["command"], "/ieum")
        self.assertTrue((self.project / ".claude/skills/ieum/SKILL.md").is_file())

    def test_distributed_archive_contains_roles_and_can_render_without_source_repo(self):
        archive_path = package_plugin.package(self.workspace / "plugin.zip")
        with ZipFile(archive_path) as archive:
            prefix = "project-lifecycle-agent/"
            self.assertIn(prefix + "skills/ieum/SKILL.md", archive.namelist())
            self.assertEqual(len([n for n in archive.namelist() if n.startswith(prefix + "agents/ieum-")]), 18)
            archive.extractall(self.workspace / "unpacked")
        plugin = self.workspace / "unpacked/project-lifecycle-agent"
        result = subprocess.run([sys.executable, "-X", "utf8", str(plugin / "tools/role_agents.py"), "show", "DB"],
            cwd=self.workspace, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("데이터 아키텍트", result.stdout)
        self.assertIn("작업 시작", result.stdout)


if __name__ == "__main__":
    unittest.main()
