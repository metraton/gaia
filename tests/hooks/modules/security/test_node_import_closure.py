"""A node run is classified by its entry's local import closure, not its layout.

``node tools/balance.mjs refresh`` wrote ``data/data.generated.js`` through an
imported module and ran unsigned, while the same ``writeFileSync`` inlined in
the entry asked for a signature.  The classifier read only the entry file, so
the signature boundary moved with the code layout.  These tests pin the
property: a write anywhere in the entry's relative-import closure classifies as
if it were in the entry, and a write-free closure stays free.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.security import mutative_verbs  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.tiers import SecurityTier, classify_command_tier  # noqa: E402

T3 = SecurityTier.T3_BLOCKED
T0 = SecurityTier.T0_READ_ONLY

WRITER = (
    "import { writeFileSync } from 'node:fs';\n"
    "export function refresh() { writeFileSync('data/data.generated.js', 'x'); }\n"
)
READER = (
    "import { readFileSync } from 'node:fs';\n"
    "export function refresh() { return readFileSync('data/in.json', 'utf8'); }\n"
)


def _write(root: Path, relative: str, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _classify(root: Path, entry: str):
    command = f"node {root / entry}"
    return detect_mutative_command(command), classify_command_tier(command)


class TestWriteInClosure:
    def test_entry_importing_a_writing_module_is_t3(self, tmp_path):
        _write(tmp_path, "lib.mjs", WRITER)
        _write(tmp_path, "main.mjs", "import { refresh } from './lib.mjs';\nrefresh();\n")
        result, tier = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is True
        assert result.verb == "fs-write"
        assert tier == T3

    def test_inlined_write_is_t3(self, tmp_path):
        _write(tmp_path, "main.mjs", WRITER + "refresh();\n")
        result, tier = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is True
        assert tier == T3

    @pytest.mark.parametrize(
        "entry_body",
        [
            "import { refresh } from './lib';\nrefresh();\n",
            "import './lib.mjs';\n",
            "export * from './lib.mjs';\n",
            "import {\n  refresh,\n} from './lib.mjs';\nrefresh();\n",
            "const { refresh } = require('./lib.cjs');\nrefresh();\n",
            "const m = await import('./lib.mjs');\nm.refresh();\n",
        ],
    )
    def test_every_literal_relative_import_form_is_followed(self, tmp_path, entry_body):
        _write(tmp_path, "lib.mjs", WRITER)
        _write(tmp_path, "lib.cjs", "const fs = require('fs');\nfs.writeFileSync('o', 'x');\n")
        _write(tmp_path, "main.mjs", entry_body)
        result, tier = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is True, entry_body
        assert tier == T3

    def test_write_two_hops_away_is_t3(self, tmp_path):
        _write(tmp_path, "tools/balance.mjs", "import { run } from '../engine/refresh.mjs';\nrun();\n")
        _write(tmp_path, "engine/refresh.mjs", "import { build } from './build-data.mjs';\nexport const run = () => build();\n")
        _write(tmp_path, "engine/build-data.mjs", WRITER.replace("refresh", "build"))
        result, tier = _classify(tmp_path, "tools/balance.mjs")
        assert result.is_mutative is True
        assert tier == T3

    def test_row_repro_shape_resolved_against_cd_target(self, tmp_path):
        _write(tmp_path, "tools/balance.mjs", (
            "import { refresh } from '../engine/refresh.mjs';\n"
            "if (process.argv[2] === 'refresh') refresh();\n"
        ))
        _write(tmp_path, "engine/refresh.mjs", (
            "import fs from 'node:fs';\n"
            "export function refresh() { fs.writeFileSync('data/data.generated.js', 'x'); }\n"
        ))
        result = detect_mutative_command("node tools/balance.mjs refresh", cwd=str(tmp_path))
        assert result.is_mutative is True
        assert "refresh.mjs" in result.reason

    @pytest.mark.parametrize(
        "relative, spec",
        [
            ("lib.js", "./lib"),
            ("lib/index.js", "./lib"),
            ("lib/index.mjs", "./lib/"),
        ],
    )
    def test_extension_and_index_resolution_follow_node(self, tmp_path, relative, spec):
        _write(tmp_path, relative, "const fs = require('fs');\nfs.rmSync('x');\n")
        _write(tmp_path, "main.js", f"require('{spec}');\n")
        result, tier = _classify(tmp_path, "main.js")
        assert result.is_mutative is True
        assert tier == T3


class TestDirectoryResolution:
    """``require('./pkg')`` loads what pkg/package.json names, not just index.js."""

    BENIGN = "module.exports = 1;\n"
    CJS_WRITER = "const fs = require('fs');\nfs.writeFileSync('o', 'x');\n"

    def test_package_main_writer_beside_a_benign_index_is_t3(self, tmp_path):
        _write(tmp_path, "pkg/package.json", '{"main": "real.js"}')
        _write(tmp_path, "pkg/real.js", self.CJS_WRITER)
        _write(tmp_path, "pkg/index.js", self.BENIGN)
        _write(tmp_path, "e.js", "require('./pkg');\n")
        result, tier = _classify(tmp_path, "e.js")
        assert result.is_mutative is True
        assert result.verb == "fs-write"
        assert tier == T3

    def test_package_main_without_an_index_is_followed(self, tmp_path):
        _write(tmp_path, "pkg/package.json", '{"main": "./real"}')
        _write(tmp_path, "pkg/real.js", self.CJS_WRITER)
        _write(tmp_path, "e.js", "require('./pkg');\n")
        result, _ = _classify(tmp_path, "e.js")
        assert result.is_mutative is True

    def test_package_exports_string_is_followed(self, tmp_path):
        _write(tmp_path, "pkg/package.json", '{"exports": {".": {"require": "./real.cjs"}}}')
        _write(tmp_path, "pkg/real.cjs", self.CJS_WRITER)
        _write(tmp_path, "pkg/index.js", self.BENIGN)
        _write(tmp_path, "e.js", "require('./pkg');\n")
        result, _ = _classify(tmp_path, "e.js")
        assert result.is_mutative is True

    def test_missing_main_falls_back_to_index_like_node(self, tmp_path):
        _write(tmp_path, "pkg/package.json", '{"main": "gone.js"}')
        _write(tmp_path, "pkg/index.js", self.CJS_WRITER)
        _write(tmp_path, "e.js", "require('./pkg');\n")
        result, _ = _classify(tmp_path, "e.js")
        assert result.is_mutative is True
        assert result.verb == "fs-write"

    def test_benign_package_stays_free(self, tmp_path):
        _write(tmp_path, "pkg/package.json", '{"name": "x", "main": "lib/main.js"}')
        _write(tmp_path, "pkg/lib/main.js", self.BENIGN)
        _write(tmp_path, "e.js", "require('./pkg');\n")
        result, tier = _classify(tmp_path, "e.js")
        assert result.is_mutative is False
        assert tier == T0

    @pytest.mark.parametrize("manifest", ["{not json", "[]", '{"main": 3}', '{"exports": 7}'])
    def test_unreadable_or_odd_package_json_fails_closed(self, tmp_path, manifest):
        _write(tmp_path, "pkg/package.json", manifest)
        _write(tmp_path, "pkg/index.js", self.BENIGN)
        _write(tmp_path, "e.js", "require('./pkg');\n")
        result, tier = _classify(tmp_path, "e.js")
        assert result.is_mutative is True
        assert tier == T3
        assert result.verb == "js-import-unresolved"
        assert "package.json" in result.reason


class TestSyntaxVersusText:
    """Only real import syntax is an import, not text inside a string or comment."""

    @pytest.mark.parametrize(
        "body",
        [
            "console.log(\"usage: require('./missing-plugin')\");\n",
            "console.log('run: import \"./missing\"');\n",
            "const help = `import x from './missing'`;\n",
            "// require('./missing')\n/* import('./missing') */\n",
        ],
    )
    def test_import_text_in_a_string_or_comment_is_not_followed(self, tmp_path, body):
        _write(tmp_path, "main.mjs", body)
        result, tier = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is False, body
        assert tier == T0

    @pytest.mark.parametrize(
        "body",
        [
            "require(`./lib.cjs`);\n",
            "const m = require(\n  './lib.cjs'\n);\n",
            "import * as ns from \"./lib.mjs\" with { type: 'javascript' };\n",
        ],
    )
    def test_real_syntax_spelling_variants_are_followed(self, tmp_path, body):
        _write(tmp_path, "lib.cjs", "const fs = require('fs');\nfs.writeFileSync('o', 'x');\n")
        _write(tmp_path, "lib.mjs", WRITER)
        _write(tmp_path, "main.mjs", body)
        result, _ = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is True, body

    def test_a_real_import_after_a_string_mentioning_one_is_still_followed(self, tmp_path):
        _write(tmp_path, "lib.mjs", WRITER)
        _write(tmp_path, "main.mjs", (
            "console.log(\"require('./nope')\");\nimport './lib.mjs';\n"
        ))
        result, _ = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is True
        assert result.verb == "fs-write"


class TestWriteFreeClosure:
    def test_entry_importing_a_read_only_module_stays_free(self, tmp_path):
        _write(tmp_path, "lib.mjs", READER)
        _write(tmp_path, "main.mjs", "import { refresh } from './lib.mjs';\nconsole.log(refresh());\n")
        result, tier = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is False
        assert tier == T0

    def test_bare_and_builtin_specifiers_are_not_followed(self, tmp_path):
        _write(tmp_path, "main.mjs", (
            "import path from 'node:path';\nimport chalk from 'chalk';\n"
            "const x = require('lodash');\n"
        ))
        result, _ = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is False

    def test_import_in_a_comment_is_not_followed(self, tmp_path):
        _write(tmp_path, "main.mjs", "// import './missing.mjs';\n/* require('./gone') */\nconsole.log(1);\n")
        result, _ = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is False

    def test_write_in_a_comment_of_an_imported_module_is_not_a_write(self, tmp_path):
        _write(tmp_path, "lib.mjs", "// fs.writeFileSync('x', 'y')\nexport const a = 1;\n")
        _write(tmp_path, "main.mjs", "import { a } from './lib.mjs';\nconsole.log(a);\n")
        result, _ = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is False

    def test_json_import_is_a_leaf(self, tmp_path):
        _write(tmp_path, "data.json", "{}")
        _write(tmp_path, "main.cjs", "const d = require('./data.json');\nconsole.log(d);\n")
        result, _ = _classify(tmp_path, "main.cjs")
        assert result.is_mutative is False


class TestCycles:
    def test_cyclic_write_free_imports_terminate_free(self, tmp_path):
        _write(tmp_path, "a.mjs", "import './b.mjs';\nexport const a = 1;\n")
        _write(tmp_path, "b.mjs", "import './a.mjs';\nexport const b = 2;\n")
        result, tier = _classify(tmp_path, "a.mjs")
        assert result.is_mutative is False
        assert tier == T0

    def test_self_import_terminates(self, tmp_path):
        _write(tmp_path, "a.mjs", "import './a.mjs';\nexport const a = 1;\n")
        result, _ = _classify(tmp_path, "a.mjs")
        assert result.is_mutative is False

    def test_write_reachable_through_a_cycle_is_t3(self, tmp_path):
        _write(tmp_path, "a.mjs", "import './b.mjs';\n")
        _write(tmp_path, "b.mjs", "import './a.mjs';\nimport './c.mjs';\n")
        _write(tmp_path, "c.mjs", WRITER)
        result, tier = _classify(tmp_path, "a.mjs")
        assert result.is_mutative is True
        assert tier == T3


class TestFailClosed:
    def test_file_count_bound_exceeded_is_t3_with_the_reason(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mutative_verbs, "_JS_IMPORT_CLOSURE_MAX_FILES", 3)
        for index in range(5):
            _write(tmp_path, f"m{index}.mjs", f"import './m{index + 1}.mjs';\n")
        _write(tmp_path, "m5.mjs", "export const end = 1;\n")
        result, tier = _classify(tmp_path, "m0.mjs")
        assert result.is_mutative is True
        assert tier == T3
        assert result.verb == "js-import-closure-bound"
        assert "3 files" in result.reason

    def test_depth_bound_exceeded_is_t3_with_the_reason(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mutative_verbs, "_JS_IMPORT_CLOSURE_MAX_DEPTH", 2)
        for index in range(4):
            _write(tmp_path, f"m{index}.mjs", f"import './m{index + 1}.mjs';\n")
        _write(tmp_path, "m4.mjs", "export const end = 1;\n")
        result, tier = _classify(tmp_path, "m0.mjs")
        assert result.is_mutative is True
        assert tier == T3
        assert result.verb == "js-import-closure-bound"
        assert "depth 2" in result.reason

    def test_closure_within_the_bound_is_not_penalised(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mutative_verbs, "_JS_IMPORT_CLOSURE_MAX_FILES", 3)
        _write(tmp_path, "m0.mjs", "import './m1.mjs';\n")
        _write(tmp_path, "m1.mjs", "import './m2.mjs';\n")
        _write(tmp_path, "m2.mjs", "export const end = 1;\n")
        result, _ = _classify(tmp_path, "m0.mjs")
        assert result.is_mutative is False

    def test_unresolvable_relative_import_is_t3_naming_it(self, tmp_path):
        _write(tmp_path, "main.mjs", "import { x } from './does-not-exist.mjs';\n")
        result, tier = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is True
        assert tier == T3
        assert result.verb == "js-import-unresolved"
        assert "./does-not-exist.mjs" in result.reason

    def test_module_larger_than_the_read_limit_is_t3(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mutative_verbs, "_MAX_SCRIPT_READ_BYTES", 200)
        _write(tmp_path, "big.mjs", "export const pad = '" + "x" * 300 + "';\n")
        _write(tmp_path, "main.mjs", "import './big.mjs';\n")
        result, _ = _classify(tmp_path, "main.mjs")
        assert result.is_mutative is True
        assert result.verb == "js-import-unresolved"
        assert "big.mjs" in result.reason
