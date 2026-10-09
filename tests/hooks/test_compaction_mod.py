"""The compaction mod ships in the plugin build and can start only the snapshot read.

The mod is a Claude Code hooks module (hooks/mods/compaction/). It reaches the
host through hooks/hooks.json's `modules` key, generated from the manifest's
`host_mods` key; `$.process.run` skips the permission prompt and Gaia's
PreToolUse, so the second class pins what the mod's source may spawn.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MOD_DIR = ROOT / "hooks" / "mods" / "compaction"
MOD_SOURCES = sorted(p for p in MOD_DIR.glob("*.ts") if not p.name.endswith(".test.ts"))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_plugin():
    return _load("_gaia_build_plugin", "scripts/build-plugin.py")


@pytest.fixture(scope="module")
def manifest(build_plugin):
    return build_plugin.load_manifest("gaia")


class TestBuildOutputShipsTheMod:
    def test_generated_hooks_json_declares_the_mod(self, build_plugin, manifest):
        generated = build_plugin.generate_hooks_json(manifest)
        assert generated["modules"] == ["./mods/compaction/register.ts"]

    def test_committed_hooks_json_declares_the_mod(self):
        committed = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        assert committed["modules"] == ["./mods/compaction/register.ts"]

    def test_declared_module_resolves_from_hooks_json(self):
        committed = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        for declared in committed["modules"]:
            assert (ROOT / "hooks" / declared).is_file(), declared

    def test_mod_sources_are_in_the_build_file_list(self, build_plugin, manifest):
        listed = set(build_plugin.resolve_file_list(manifest))
        assert set(MOD_SOURCES) <= listed

    def test_modules_key_is_independent_of_the_python_modules_key(self, build_plugin, manifest):
        without_mods = {k: v for k, v in manifest.items() if k != "host_mods"}
        assert "modules" not in build_plugin.generate_hooks_json(without_mods)
        assert manifest["modules"] == "all"

    def test_drift_guard_fails_when_the_committed_modules_key_is_dropped(
        self, build_plugin, manifest, tmp_path, monkeypatch, capsys
    ):
        guard = _load("_gaia_check_hooks_drift", "scripts/check_hooks_drift.py")
        stale = build_plugin.generate_hooks_json(manifest)
        del stale["modules"]
        fixtures = {
            "MANIFEST": ("gaia.manifest.json", manifest),
            "HOOKS_JSON": ("hooks.json", stale),
            "PLUGIN_JSON": ("plugin.json", {"name": "gaia"}),
        }
        for attr, (filename, content) in fixtures.items():
            path = tmp_path / filename
            path.write_text(json.dumps(content))
            monkeypatch.setattr(guard, attr, path)

        assert guard.main() == 1
        assert "'modules'" in capsys.readouterr().err

    def test_drift_guard_passes_on_the_working_tree(self, capsys):
        guard = _load("_gaia_check_hooks_drift", "scripts/check_hooks_drift.py")
        assert guard.main() == 0, capsys.readouterr().err


class TestModSpawnsOnlyTheSnapshotRead:
    SNAPSHOT = MOD_DIR / "register.ts"
    PROCESS_USE = re.compile(r"\$\.process\.(\w+)")
    OTHER_REACH = re.compile(
        r"\$\.(tool|agent|network|fs|settings|env)\b|\bimport\s*\(|\beval\s*\(|new Function|\bfetch\s*\("
    )

    def test_mod_sources_exist(self):
        assert self.SNAPSHOT in MOD_SOURCES

    def test_the_only_process_use_is_one_run_in_register_ts(self):
        uses = [
            (path.name, verb)
            for path in MOD_SOURCES
            for verb in self.PROCESS_USE.findall(path.read_text())
        ]
        assert uses == [("register.ts", "run")]

    def test_mod_reaches_nothing_else_outside_its_own_session_and_ui(self):
        for path in MOD_SOURCES:
            assert not self.OTHER_REACH.search(path.read_text()), path.name

    def test_run_receives_the_argv_built_in_register_ts(self):
        source = self.SNAPSHOT.read_text()
        assert re.search(r"\$\.process\.run\(argv,", source)
        argv = re.search(r"const argv = \[(.*?)\n  \]", source, re.S).group(1)
        elements = [line.strip().rstrip(",") for line in argv.strip().splitlines()]
        assert elements == [
            "'sh'",
            "`${root}/${LAUNCHER}`",
            "`${root}/${GAIA_CLI}`",
            "...SNAPSHOT_ARGS",
            "await $.session.id()",
        ]

    def test_constants_name_the_read_only_snapshot_verb_of_this_package(self):
        source = self.SNAPSHOT.read_text()
        launcher = re.search(r"const LAUNCHER = '([^']+)'", source).group(1)
        cli = re.search(r"const GAIA_CLI = '([^']+)'", source).group(1)
        args = re.search(r"const SNAPSHOT_ARGS = \[(.*?)\] as const", source).group(1)

        assert (ROOT / launcher).is_file()
        assert cli == json.loads((ROOT / "package.json").read_text())["bin"]["gaia"]
        assert re.findall(r"'([^']+)'", args) == ["session", "snapshot", "--session-id"]

    def test_the_snapshot_verb_it_names_is_read_only(self):
        session_cli = (ROOT / "bin" / "cli" / "session.py").read_text()
        assert "Print one session's open contracts" in session_cli
        assert "read-only" in session_cli


class TestSnapshotArrivesOnce:
    """With the mod active the SessionStart(compact) refresh drops its snapshot copy."""

    @pytest.fixture
    def builder(self, monkeypatch):
        from modules.context import compact_context_builder as builder

        monkeypatch.setattr(builder, "_build_snapshot_block", lambda session_id: "SNAPSHOT")
        return builder

    @staticmethod
    def _boundary(pre_tokens=1000):
        compact = {} if pre_tokens is None else {"preTokens": pre_tokens}
        return {"type": "system", "subtype": "compact_boundary", "compactMetadata": compact}

    @staticmethod
    def _user(text):
        return {"type": "user", "message": {"role": "user", "content": text}}

    @staticmethod
    def _transcript(tmp_path, entries, raw_head=""):
        path = tmp_path / "transcript.jsonl"
        path.write_text(raw_head + "".join(json.dumps(entry) + "\n" for entry in entries))
        return str(path)

    def _delivered(self, builder, path) -> bool:
        context = builder.build_compact_context(session_id="s", transcript_path=path)
        assert "Post-Compaction Context Refresh" in context
        return "SNAPSHOT" in context

    def test_the_mod_builds_the_marker_the_builder_looks_for(self, builder):
        source = (MOD_DIR / "register.ts").read_text()
        template = re.search(r"`(\[gaia:session-snapshot tokens-before=\$\{tokensBefore\}\])\\n", source)
        assert template.group(1).replace("${tokensBefore}", "1000") == builder.snapshot_marker(1000)

    def test_the_mod_marks_nothing_without_a_token_count(self):
        source = (MOD_DIR / "register.ts").read_text()
        assert "tokensBefore === undefined\n    ? snapshot" in source

    def test_the_marker_is_written_by_the_compact_hook_alone(self):
        source = (MOD_DIR / "register.ts").read_text()
        schedule = source[source.index("export function scheduleCompact"):source.index("export const register")]
        assert "withMarker" not in schedule
        assert source.count("withMarker(snapshot, compacted.tokensBefore)") == 1

    def test_this_compactions_marker_leaves_the_snapshot_out(self, builder, tmp_path):
        path = self._transcript(tmp_path, [
            self._boundary(1000),
            self._user("summary"),
            self._user(builder.snapshot_marker(1000) + "\nstate"),
        ])
        assert not self._delivered(builder, path)

    def test_a_read_that_failed_appended_nothing_so_the_snapshot_is_delivered(self, builder, tmp_path):
        path = self._transcript(tmp_path, [self._boundary(1000), self._user("summary")])
        assert self._delivered(builder, path)

    def test_a_compaction_that_skipped_the_hook_delivers_the_snapshot_despite_an_older_marker(
        self, builder, tmp_path
    ):
        path = self._transcript(tmp_path, [
            self._boundary(1000),
            self._user("summary"),
            self._user(builder.snapshot_marker(1000) + "\nstate"),
            self._boundary(2000),
            self._user("summary"),
        ])
        assert self._delivered(builder, path)

    def test_a_marker_kept_across_the_boundary_for_another_compaction_does_not_count(
        self, builder, tmp_path
    ):
        path = self._transcript(tmp_path, [
            self._boundary(2000),
            self._user("summary"),
            self._user(builder.snapshot_marker(1000) + "\nstate"),
        ])
        assert self._delivered(builder, path)

    def test_marker_text_quoted_in_the_conversation_does_not_count(self, builder, tmp_path):
        quoted = f"the mod writes {builder.snapshot_marker(1000)} first"
        path = self._transcript(tmp_path, [
            self._boundary(1000),
            self._user(quoted),
            {"type": "assistant", "message": {"content": [{"type": "text", "text": builder.snapshot_marker(1000)}]}},
        ])
        assert self._delivered(builder, path)

    def test_a_marker_in_a_text_block_of_a_user_entry_counts(self, builder, tmp_path):
        block = {"type": "user", "message": {"content": [{"type": "text", "text": builder.snapshot_marker(7) + "\nx"}]}}
        path = self._transcript(tmp_path, [self._boundary(7), block])
        assert not self._delivered(builder, path)

    def test_an_ambiguous_transcript_delivers_the_snapshot(self, builder, tmp_path):
        marked = self._user(builder.snapshot_marker(1000) + "\nstate")
        for entries in (
            [marked],
            [self._boundary(None), marked],
            [self._boundary("1000"), marked],
        ):
            assert self._delivered(builder, self._transcript(tmp_path, entries))

    def test_a_boundary_older_than_the_tail_window_is_not_found(self, builder, tmp_path):
        filler = "x" * (builder._TRANSCRIPT_TAIL_BYTES + 10)
        path = self._transcript(tmp_path, [
            self._boundary(1000),
            self._user(filler),
            self._user(builder.snapshot_marker(1000) + "\nstate"),
        ])
        assert self._delivered(builder, path)

    @pytest.mark.parametrize("path", ["", "/nonexistent/transcript.jsonl"])
    def test_no_readable_transcript_delivers_the_snapshot(self, builder, path):
        assert self._delivered(builder, path)

    def test_the_hook_passes_the_events_transcript_path_through(self):
        hook = (ROOT / "hooks" / "session_start.py").read_text()
        assert 'transcript_path=event_data.get("transcript_path", "")' in hook
