"""Every Bandit call id maps to a Gaia effect class, or to an exclusion that states its reason."""

import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "hooks"))
sys.path.insert(0, str(REPO_ROOT))

from hooks.modules.security.inline_ast_analyzer import parse_finds_no_effect

BANDIT_VERSION = "1.9.4"
BANDIT_SOURCE = (
    "bandit/blacklists/calls.py, bandit/plugins/injection_shell.py gen_config, and the call matchers of "
    "pytorch_load, huggingface_unsafe_download, logging_config_insecure_listen, yaml_load, "
    "tarfile_unsafe_members, hashlib_insecure_functions, request_without_timeout, django_sql_injection, "
    "jinja2_templates, mako_templates, insecure_ssl_tls, snmp_security_check, injection_paramiko, "
    "weak_cryptographic_key, ssh_no_host_key_verification, app_debug and crypto_request_no_cert_validation, "
    "read at tag 1.9.4 (PyPI latest, 2026-02-25); B111 exec_as_root could not be fetched and stays unverified"
)

EXCLUDED = "EXCLUDED"
EFFECT_CLASSES = frozenset({
    "PROCESS_EXECUTION", "FILE_DELETION", "FILE_WRITE", "FILE_MUTATION", "PERMISSION_MOD",
    "NETWORK", "CLOUD_SDK", "DYNAMIC_CODE", EXCLUDED,
})

CALL_IDS = frozenset({
    "B102", "B103", "B111", "B113", "B201", "B202", "B301", "B302", "B303", "B304", "B305", "B306",
    "B307", "B308", "B310", "B311", "B312", "B313", "B314", "B315", "B316", "B317", "B318", "B319",
    "B321", "B323", "B324", "B501", "B502", "B504", "B505", "B506", "B507", "B508", "B509", "B601", "B602",
    "B603", "B604", "B605", "B606", "B607", "B609", "B610", "B611", "B612", "B614", "B615", "B701",
    "B702", "B703", "B704",
})


class Entry(NamedTuple):
    source_id: str
    call: str
    effect: str
    reason: str = ""
    code: str = ""
    verified: bool = True


NO_STATE_EFFECT = "construction or computation only; nothing outside the process changes"
HASH_OR_CIPHER = "digest, cipher or key object computed in memory; the weakness is a smell, not an effect"
XML_PARSE = "parser reads the named file into a tree; no write, process or network effect on current expat"
NO_MODULE_PATH = "method on a receiver the analyzer cannot name; no dotted module path to match"
UNBOUNDED_NAME = "selected by a keyword on a call of any name; the callee is unbounded, so no name catalogue can hold it"
UNPICKLER = "import {m}\n{m}.{c}(stream).load()"
HTTP_VERBS = ("get", "options", "head", "post", "put", "patch", "delete")
HTTPX_CALLS = HTTP_VERBS + ("request", "stream", "Client", "AsyncClient")
YAML_LOAD = "import yaml\nyaml.load(payload, {loader})"
PY2_ONLY ="module removed in Python 3; the import fails under python3, the only interpreter this lane runs"

BANDIT_ENTRIES: Tuple[Entry, ...] = (
    Entry("B102", "exec", "DYNAMIC_CODE"),
    Entry("B103", "os.chmod", "PERMISSION_MOD"),
    Entry("B111", "oslo_concurrency.processutils.execute", EXCLUDED, UNBOUNDED_NAME, verified=False),
    *(Entry(i, f"requests.{m}", "NETWORK") for i in ("B113", "B501") for m in HTTP_VERBS),
    *(Entry(i, f"httpx.{m}", "NETWORK") for i in ("B113", "B501") for m in HTTPX_CALLS),
    Entry("B201", "<app>.run", EXCLUDED, NO_MODULE_PATH),
    Entry("B202", "tarfile.TarFile.extractall", "FILE_WRITE", code="import tarfile\ntarfile.open(payload).extractall(payload)"),
    Entry("B301", "pickle.loads", "DYNAMIC_CODE"),
    Entry("B301", "pickle.load", "DYNAMIC_CODE"),
    Entry("B301", "pickle.Unpickler", "DYNAMIC_CODE", code=UNPICKLER.format(m="pickle", c="Unpickler")),
    Entry("B301", "dill.loads", "DYNAMIC_CODE"),
    Entry("B301", "dill.load", "DYNAMIC_CODE"),
    Entry("B301", "dill.Unpickler", "DYNAMIC_CODE", code=UNPICKLER.format(m="dill", c="Unpickler")),
    Entry("B301", "shelve.open", "FILE_WRITE"),
    Entry("B301", "shelve.DbfilenameShelf", "FILE_WRITE"),
    Entry("B301", "jsonpickle.decode", "DYNAMIC_CODE"),
    Entry("B301", "jsonpickle.unpickler.decode", "DYNAMIC_CODE"),
    Entry("B301", "jsonpickle.unpickler.Unpickler", "DYNAMIC_CODE",
          code="import jsonpickle.unpickler\njsonpickle.unpickler.Unpickler().restore(payload)"),
    Entry("B301", "pandas.read_pickle", "DYNAMIC_CODE"),
    Entry("B302", "marshal.load", "DYNAMIC_CODE"),
    Entry("B302", "marshal.loads", "DYNAMIC_CODE"),
    *(Entry("B303", f"{p}.{h}.new", EXCLUDED, HASH_OR_CIPHER) for p in ("Crypto", "Cryptodome")
      for h in ("Hash.MD2", "Hash.MD4", "Hash.MD5", "Hash.SHA")),
    Entry("B303", "cryptography.hazmat.primitives.hashes.MD5", EXCLUDED, HASH_OR_CIPHER),
    Entry("B303", "cryptography.hazmat.primitives.hashes.SHA1", EXCLUDED, HASH_OR_CIPHER),
    *(Entry("B304", f"{p}.Cipher.{c}.new", EXCLUDED, HASH_OR_CIPHER) for p in ("Crypto", "Cryptodome")
      for c in ("ARC2", "ARC4", "Blowfish", "DES", "XOR")),
    *(Entry("B304", f"cryptography.hazmat.primitives.ciphers.algorithms.{c}", EXCLUDED, HASH_OR_CIPHER)
      for c in ("ARC4", "Blowfish", "CAST5", "IDEA", "SEED", "TripleDES")),
    Entry("B305", "cryptography.hazmat.primitives.ciphers.modes.ECB", EXCLUDED, HASH_OR_CIPHER),
    Entry("B306", "tempfile.mktemp", "FILE_MUTATION"),
    Entry("B307", "eval", "DYNAMIC_CODE"),
    Entry("B308", "django.utils.safestring.mark_safe", EXCLUDED, NO_STATE_EFFECT),
    Entry("B310", "urllib.request.urlopen", "NETWORK"),
    Entry("B310", "urllib.request.urlretrieve", "NETWORK"),
    Entry("B310", "urllib.request.URLopener", "NETWORK", code="import urllib.request\nurllib.request.URLopener().open(payload)"),
    Entry("B310", "urllib.request.FancyURLopener", "NETWORK",
          code="import urllib.request\nurllib.request.FancyURLopener().open(payload)"),
    Entry("B310", "six.moves.urllib.request.urlopen", "NETWORK"),
    Entry("B310", "six.moves.urllib.request.urlretrieve", "NETWORK"),
    Entry("B310", "six.moves.urllib.request.URLopener", "NETWORK",
          code="import six.moves.urllib.request\nsix.moves.urllib.request.URLopener().open(payload)"),
    Entry("B310", "six.moves.urllib.request.FancyURLopener", "NETWORK",
          code="import six.moves.urllib.request\nsix.moves.urllib.request.FancyURLopener().open(payload)"),
    *(Entry("B311", f"random.{f}", EXCLUDED, NO_STATE_EFFECT) for f in (
        "Random", "random", "randrange", "randint", "choice", "choices", "uniform", "triangular",
        "randbytes", "sample", "getrandbits")),
    Entry("B312", "telnetlib.Telnet", "NETWORK"),
    *(Entry("B313", f"xml.etree.cElementTree.{f}", EXCLUDED, XML_PARSE) for f in (
        "parse", "iterparse", "fromstring", "XMLParser")),
    *(Entry("B314", f"xml.etree.ElementTree.{f}", EXCLUDED, XML_PARSE) for f in (
        "parse", "iterparse", "fromstring", "XMLParser")),
    Entry("B315", "xml.sax.expatreader.create_parser", EXCLUDED, XML_PARSE),
    Entry("B316", "xml.dom.expatbuilder.parse", EXCLUDED, XML_PARSE),
    Entry("B316", "xml.dom.expatbuilder.parseString", EXCLUDED, XML_PARSE),
    *(Entry("B317", f"xml.sax.{f}", EXCLUDED, XML_PARSE) for f in ("parse", "parseString", "make_parser")),
    Entry("B318", "xml.dom.minidom.parse", EXCLUDED, XML_PARSE),
    Entry("B318", "xml.dom.minidom.parseString", EXCLUDED, XML_PARSE),
    Entry("B319", "xml.dom.pulldom.parse", EXCLUDED, XML_PARSE),
    Entry("B319", "xml.dom.pulldom.parseString", EXCLUDED, XML_PARSE),
    Entry("B321", "ftplib.FTP", "NETWORK"),
    Entry("B323", "ssl._create_unverified_context", "NETWORK"),
    *(Entry("B324", f"hashlib.{f}", EXCLUDED, HASH_OR_CIPHER) for f in ("md4", "md5", "sha", "sha1", "new")),
    Entry("B502", "ssl.wrap_socket", "NETWORK"),
    Entry("B502", "pyOpenSSL.SSL.Context", EXCLUDED, NO_STATE_EFFECT),
    Entry("B504", "ssl.wrap_socket", "NETWORK"),
    *(Entry("B505", f"cryptography.hazmat.primitives.asymmetric.{k}.generate_private_key", EXCLUDED, HASH_OR_CIPHER)
      for k in ("dsa", "rsa", "ec")),
    *(Entry("B505", f"{p}.PublicKey.{k}.generate", EXCLUDED, HASH_OR_CIPHER)
      for p in ("Crypto", "Cryptodome") for k in ("DSA", "RSA")),
    Entry("B506", "yaml.load", "DYNAMIC_CODE"),
    Entry("B506", "yaml.load(Loader=yaml.Loader)", "DYNAMIC_CODE", code=YAML_LOAD.format(loader="Loader=yaml.Loader")),
    Entry("B506", "yaml.load(yaml.FullLoader)", "DYNAMIC_CODE", code=YAML_LOAD.format(loader="yaml.FullLoader")),
    Entry("B506", "yaml.load(Loader=yaml.UnsafeLoader)", "DYNAMIC_CODE",
          code=YAML_LOAD.format(loader="Loader=yaml.UnsafeLoader")),
    Entry("B507", "paramiko.SSHClient.set_missing_host_key_policy", "NETWORK",
          code="import paramiko\nclient = paramiko.SSHClient()\nclient.set_missing_host_key_policy(paramiko.AutoAddPolicy())"),
    Entry("B508", "pysnmp.hlapi.CommunityData", EXCLUDED, NO_STATE_EFFECT),
    Entry("B509", "pysnmp.hlapi.UsmUserData", EXCLUDED, NO_STATE_EFFECT),
    Entry("B601", "paramiko.SSHClient.exec_command", "PROCESS_EXECUTION",
          code="import paramiko\nparamiko.SSHClient().exec_command(payload)"),
    *(Entry("B602", f"subprocess.{f}", "PROCESS_EXECUTION") for f in ("Popen", "call", "check_call", "check_output", "run")),
    *(Entry("B603", f"subprocess.{f}", "PROCESS_EXECUTION") for f in ("Popen", "call", "check_call", "check_output", "run")),
    Entry("B604", "any_function(shell=True)", EXCLUDED, UNBOUNDED_NAME),
    *(Entry("B605", f"os.{f}", "PROCESS_EXECUTION") for f in ("system", "popen", "popen2", "popen3", "popen4")),
    *(Entry("B605", f"popen2.{f}", EXCLUDED, PY2_ONLY) for f in ("popen2", "popen3", "popen4", "Popen3", "Popen4")),
    *(Entry("B605", f"commands.{f}", EXCLUDED, PY2_ONLY) for f in ("getoutput", "getstatusoutput")),
    Entry("B605", "subprocess.getoutput", "PROCESS_EXECUTION"),
    Entry("B605", "subprocess.getstatusoutput", "PROCESS_EXECUTION"),
    *(Entry("B606", f"os.{f}", "PROCESS_EXECUTION") for f in (
        "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
        "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "startfile")),
    *(Entry("B607", f"subprocess.{f}", "PROCESS_EXECUTION") for f in ("Popen", "run")),
    Entry("B609", "os.system", "PROCESS_EXECUTION"),
    Entry("B610", "extra", EXCLUDED, NO_MODULE_PATH),
    Entry("B611", "RawSQL", EXCLUDED, NO_STATE_EFFECT),
    Entry("B612", "logging.config.listen", "DYNAMIC_CODE"),
    Entry("B614", "torch.load", "DYNAMIC_CODE"),
    Entry("B614", "torch.serialization.load", "DYNAMIC_CODE"),
    Entry("B615", "huggingface_hub.hf_hub_download", "NETWORK"),
    Entry("B615", "huggingface_hub.snapshot_download", "NETWORK"),
    Entry("B701", "jinja2.Environment", EXCLUDED, NO_STATE_EFFECT),
    Entry("B702", "mako.template.Template", EXCLUDED, NO_STATE_EFFECT),
    Entry("B703", "django.utils.safestring.mark_safe", EXCLUDED, NO_STATE_EFFECT),
    Entry("B704", "markupsafe.Markup", EXCLUDED, NO_STATE_EFFECT),
    Entry("B704", "flask.Markup", EXCLUDED, NO_STATE_EFFECT),
)

GAIA_AUDIT_ENTRIES: Tuple[Entry, ...] = (
    Entry("audit-36975", "pydoc.pipepager", "PROCESS_EXECUTION"),
    Entry("verifier-37020", "yaml.unsafe_load", "DYNAMIC_CODE"),
)

ALL_ENTRIES = BANDIT_ENTRIES + GAIA_AUDIT_ENTRIES
MAPPED = tuple(e for e in ALL_ENTRIES if e.effect != EXCLUDED)


def _sources(entry: Entry) -> Tuple[str, ...]:
    if entry.code:
        return (entry.code,)
    module, _, leaf = entry.call.rpartition(".")
    if not module:
        return (f"{leaf}(payload)",)
    return (f"import {module}\n{entry.call}(payload)", f"from {module} import {leaf}\n{leaf}(payload)")


JUDGED_FREE_TODAY = frozenset({
    "pickle.loads", "pickle.load", "pickle.Unpickler", "dill.loads", "dill.load", "dill.Unpickler",
    "jsonpickle.decode", "jsonpickle.unpickler.decode", "jsonpickle.unpickler.Unpickler", "pandas.read_pickle",
    "marshal.load", "marshal.loads", "torch.load", "torch.serialization.load", "logging.config.listen",
    "urllib.request.urlretrieve", "urllib.request.URLopener", "urllib.request.FancyURLopener",
    "six.moves.urllib.request.urlopen", "six.moves.urllib.request.urlretrieve",
    "six.moves.urllib.request.URLopener", "six.moves.urllib.request.FancyURLopener",
    "huggingface_hub.hf_hub_download", "huggingface_hub.snapshot_download", "os.startfile", "pydoc.pipepager",
    "yaml.load", "yaml.load(Loader=yaml.Loader)", "yaml.load(yaml.FullLoader)",
    "yaml.load(Loader=yaml.UnsafeLoader)", "yaml.unsafe_load",
})
STILL_FREE = pytest.mark.xfail(strict=True, reason="analyzer has no class for this call yet (task 1114 finding)")

def _samples(known_gaps_marked: bool):
    return tuple(
        pytest.param(
            entry, source, id=f"{entry.source_id}-{entry.call}-{form}",
            marks=[STILL_FREE] if known_gaps_marked and entry.call in JUDGED_FREE_TODAY else [],
        )
        for entry in MAPPED
        for form, source in zip(("code",) if entry.code else ("import", "from"), _sources(entry))
    )


SAMPLES = _samples(known_gaps_marked=False)
MAPPED_SOURCES = _samples(known_gaps_marked=True)


class TestCatalogueShape:
    def test_every_entry_names_a_known_effect_class(self):
        unknown = [e for e in ALL_ENTRIES if e.effect not in EFFECT_CLASSES]
        assert unknown == []

    def test_every_exclusion_states_its_reason(self):
        bare = [e.call for e in ALL_ENTRIES if e.effect == EXCLUDED and len(e.reason) < 20]
        assert bare == []

    def test_a_mapped_entry_carries_no_exclusion_reason(self):
        assert [e.call for e in MAPPED if e.reason] == []

    def test_every_bandit_call_id_has_an_entry(self):
        assert CALL_IDS - {e.source_id for e in BANDIT_ENTRIES} == set()

    def test_every_bandit_entry_uses_a_listed_id(self):
        assert {e.source_id for e in BANDIT_ENTRIES} - CALL_IDS == set()
        assert all(re.fullmatch(r"B\d{3}", e.source_id) for e in BANDIT_ENTRIES)

    def test_entries_not_read_from_the_1_9_4_source_are_named(self):
        assert {e.source_id for e in ALL_ENTRIES if not e.verified} == {"B111"}

    def test_a_pinned_version_is_recorded(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", BANDIT_VERSION) and BANDIT_SOURCE

    def test_no_entry_repeats_within_its_id(self):
        keys = [(e.source_id, e.call) for e in ALL_ENTRIES]
        assert len(keys) == len(set(keys))


class TestJudgeDiscriminates:
    def test_a_known_dangerous_call_is_not_free(self):
        assert parse_finds_no_effect("import os\nos.system(payload)") is False

    def test_a_pure_expression_is_free(self):
        assert parse_finds_no_effect("len(payload)") is True

    @pytest.mark.parametrize("entry,source", SAMPLES)
    def test_the_sample_parses(self, entry, source):
        ast.parse(source)


class TestBanditCallsAreJudged:
    @pytest.mark.parametrize("entry,source", MAPPED_SOURCES)
    def test_mapped_call_is_not_judged_free(self, entry, source):
        assert parse_finds_no_effect(source) is False, (
            f"{entry.source_id} {entry.call} maps to {entry.effect} but the analyzer judges it free: {source!r}"
        )
