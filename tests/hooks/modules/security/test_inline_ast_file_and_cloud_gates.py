#!/usr/bin/env python3
"""Negative-test closure for the Python AST lane's file and cloud-SDK gates.

Every test here is a NEGATIVE test in the strict sense: each asserted input
classified T0 (not mutative) BEFORE the gates in ``inline_ast_analyzer`` were
widened, and must classify T3 after.  They are the executable statement of two
holes that were measured, not hypothesised:

A. ``shutil.copyfile`` was absent while ``copy``/``copy2``/``copytree``/``move``
   were present -- one character between a gate and no gate.  A subagent
   blocked three times on ``cp`` reached the same syscalls through it.
B. No cloud-SDK category existed at all.  ``NETWORK`` matches what egress
   LOOKS like (``requests.post``, ``socket.socket``); an SDK looks like an
   ordinary attribute chain, so ``boto3.client("s3").delete_object(...)`` and
   its peers classified T0 while deleting real cloud resources.

The false-positive classes are asserted with equal weight (see
``TestBenignPayloadsStayReadOnly``): a security gate that blocks legitimate
read-only work is reverted, and the hole reopens with it.

``TestKnownEscapes`` deliberately asserts that a dynamically-dispatched
mutation is NOT caught.  That is not an oversight to fix later -- it pins the
ceiling of static classification so a future reader cannot mistake this catalog
for containment.
"""

import pytest

from hooks.modules.security.inline_ast_analyzer import analyze_python_inline
from hooks.modules.security.mutative_verbs import detect_mutative_command


BOTO3_DELETE_SRC = (
    'import boto3\n'
    'boto3.client("s3").delete_object(Bucket="prod-data", Key="k")\n'
)


def _write_script(tmp_path, body: str) -> str:
    script = tmp_path / "c.py"
    script.write_text(body)
    return str(script)


# ---------------------------------------------------------------------------
# A -- the omission that fired in a real session
# ---------------------------------------------------------------------------
class TestShutilCopyfileGate:
    """``shutil.copyfile`` must gate identically to its four siblings."""

    def test_copyfile_via_script_file_is_mutative(self, tmp_path):
        # The exact escape that was used: blocked on `cp`, reached the same
        # effect by pointing the interpreter at a script.
        path = _write_script(
            tmp_path,
            'import shutil\nshutil.copyfile("/tmp/a", "/tmp/b")\n',
        )
        result = detect_mutative_command(f"python3 {path}")
        assert result.is_mutative is True
        assert result.verb == "shutil-copyfile"

    def test_copyfile_via_inline_c_is_mutative(self):
        result = detect_mutative_command(
            'python3 -c "import shutil; shutil.copyfile(\'/tmp/a\',\'/tmp/b\')"'
        )
        assert result.is_mutative is True
        assert result.verb == "shutil-copyfile"

    def test_copyfile_classifies_like_copy(self):
        # The sibling that WAS gated, as the reference point: one character of
        # spelling must not change the tier.
        copyfile = analyze_python_inline(
            'import shutil\nshutil.copyfile("/tmp/a", "/tmp/b")'
        )
        copy = analyze_python_inline('import shutil\nshutil.copy("/tmp/a", "/tmp/b")')
        assert copyfile.is_dangerous is copy.is_dangerous is True
        assert copyfile.category == copy.category == "FILE_MUTATION"


# ---------------------------------------------------------------------------
# A -- the rest of the audited shutil / os / pathlib mutation surface
# ---------------------------------------------------------------------------
class TestAuditedFileMutationFamily:
    """Each entry mutates a file, its metadata, or its permissions."""

    @pytest.mark.parametrize("src,label", [
        ('import shutil\nshutil.copyfile("/a","/b")', "shutil-copyfile"),
        ('import shutil\nshutil.copyfileobj(x, y)', "shutil-copyfileobj"),
        ('import shutil\nshutil.copymode("/a","/b")', "shutil-copymode"),
        ('import shutil\nshutil.copystat("/a","/b")', "shutil-copystat"),
        ('import shutil\nshutil.chown("/a", user="root")', "shutil-chown"),
        ('import shutil\nshutil.unpack_archive("/a.tar","/dst")', "shutil-unpack-archive"),
        ('import shutil\nshutil.make_archive("/a","zip","/src")', "shutil-make-archive"),
        ('import os\nos.truncate("/a", 0)', "os-truncate"),
        ('import os\nos.ftruncate(3, 0)', "os-ftruncate"),
        ('import os\nos.write(3, b"x")', "os-write"),
        ('import os\nos.pwrite(3, b"x", 0)', "os-pwrite"),
        ('import os\nos.renames("/a","/b/c")', "os-renames"),
        ('import os\nos.mknod("/a")', "os-mknod"),
        ('import os\nos.mkfifo("/a")', "os-mkfifo"),
        ('import os\nos.utime("/a")', "os-utime"),
        ('import os\nos.fchmod(3, 511)', "os-fchmod"),
        ('import os\nos.setxattr("/a","u.x",b"y")', "os-setxattr"),
        ('import os\nos.removexattr("/a","u.x")', "os-removexattr"),
    ])
    def test_audited_api_is_mutative(self, src, label):
        result = analyze_python_inline(src)
        assert result.is_dangerous is True
        assert result.label == label

    @pytest.mark.parametrize("src,label", [
        ('from pathlib import Path\nPath("/a").replace("/b")', "pathlib-replace"),
        ('from pathlib import Path\nPath("/l").symlink_to("/etc/passwd")', "pathlib-symlink-to"),
        ('from pathlib import Path\nPath("/l").hardlink_to("/etc/passwd")', "pathlib-hardlink-to"),
        ('from pathlib import Path\nPath("/l").link_to("/etc/passwd")', "pathlib-link-to"),
        ('from pathlib import Path\nPath("/a").lchmod(511)', "pathlib-lchmod"),
    ])
    def test_pathlib_additions_are_mutative(self, src, label):
        result = analyze_python_inline(src)
        assert result.is_dangerous is True
        assert result.label == label

    @pytest.mark.parametrize("src,label", [
        # These entries pre-existed but were UNREACHABLE: the resolver stopped
        # at the first intermediate call, so no idiomatic `Path(x).method()`
        # ever matched them.  They are negative tests exactly like the new
        # additions -- each classified T0 before the chain resolver.
        ('from pathlib import Path\nPath("/a").unlink()', "pathlib-unlink"),
        ('from pathlib import Path\nPath("/d").rmdir()', "pathlib-rmdir"),
        ('from pathlib import Path\nPath("/a").rename("/b")', "pathlib-rename"),
        ('from pathlib import Path\nPath("/a").write_text("x")', "pathlib-write-text"),
        ('from pathlib import Path\nPath("/a").write_bytes(b"x")', "pathlib-write-bytes"),
        ('from pathlib import Path\nPath("/a").touch()', "pathlib-touch"),
        ('from pathlib import Path\nPath("/d").mkdir()', "pathlib-mkdir"),
        ('from pathlib import Path\nPath("/a").chmod(511)', "pathlib-chmod"),
        ('import pathlib\npathlib.Path("/a").unlink()', "pathlib-unlink"),
    ])
    def test_previously_unreachable_pathlib_entries_now_fire(self, src, label):
        result = analyze_python_inline(src)
        assert result.is_dangerous is True
        assert result.label == label

    def test_pathlib_via_local_binding_is_mutative(self):
        result = analyze_python_inline(
            'from pathlib import Path\np = Path("/a")\np.unlink()'
        )
        assert result.is_dangerous is True
        assert result.label == "pathlib-unlink"


# ---------------------------------------------------------------------------
# A -- mode / flag gated openers
# ---------------------------------------------------------------------------
class TestModeGatedOpeners:
    """A write-mode open is FILE_WRITE; a read-mode open stays T0."""

    @pytest.mark.parametrize("src", [
        'from pathlib import Path\nPath("/a").open("w")',
        'from pathlib import Path\nPath("/a").open("a")',
        'from pathlib import Path\nPath("/a").open(mode="r+")',
        'import io\nio.open("/a", "w")',
        'import codecs\ncodecs.open("/a", "w", "utf-8")',
    ])
    def test_write_mode_open_is_mutative(self, src):
        assert analyze_python_inline(src).is_dangerous is True

    @pytest.mark.parametrize("src", [
        'from pathlib import Path\nPath("/a").open()',
        'from pathlib import Path\nPath("/a").open("r")',
        'from pathlib import Path\nPath("/a").open("rb")',
        'import io\nio.open("/a")',
        'import codecs\ncodecs.open("/a", "r", "utf-8")',
    ])
    def test_read_mode_open_stays_read_only(self, src):
        assert analyze_python_inline(src).is_dangerous is False

    def test_os_open_write_flags_are_mutative(self):
        result = analyze_python_inline(
            'import os\nos.open("/a", os.O_WRONLY | os.O_CREAT | os.O_TRUNC)'
        )
        assert result.is_dangerous is True
        assert result.label == "os-open-write"

    def test_os_open_rdonly_stays_read_only(self):
        result = analyze_python_inline('import os\nos.open("/a", os.O_RDONLY)')
        assert result.is_dangerous is False

    def test_read_open_does_not_mask_a_later_mutation(self):
        # A mode-gated entry that comes back safe must not end the walk: the
        # read open is first in source order, the delete is what matters.
        result = analyze_python_inline(
            'import os\nfrom pathlib import Path\n'
            'data = Path("/a").open("r").read()\n'
            'os.remove("/a")\n'
        )
        assert result.is_dangerous is True
        assert result.label == "os-remove"


# ---------------------------------------------------------------------------
# D -- cloud SDK mutations, invoked through attribute+call chains
# ---------------------------------------------------------------------------
class TestCloudSdkGate:
    """The five SDKs, in the chained form they are actually written."""

    def test_boto3_delete_object_is_dangerous(self):
        result = analyze_python_inline(BOTO3_DELETE_SRC)
        assert result.is_dangerous is True
        assert result.category == "CLOUD_SDK"

    @pytest.mark.parametrize("src", [
        # Chained through TWO intermediate calls, the form a flat
        # `module.function` catalog entry can never match.
        'from google.cloud import storage\nstorage.Client().bucket("b").delete()',
        'from kubernetes import client\nclient.CoreV1Api().delete_namespace("prod")',
        'from googleapiclient.discovery import build\n'
        'build("compute","v1").instances().delete(project="p",zone="z",instance="i").execute()',
        'from python_terraform import Terraform\nTerraform(working_dir="/repo").apply(skip_plan=True)',
        'from python_terraform import Terraform\nTerraform(working_dir="/repo").destroy()',
        'import boto3\nboto3.client("ec2").terminate_instances(InstanceIds=["i-1"])',
        'import boto3\nboto3.resource("s3").Bucket("b").objects.filter().delete()',
        'import boto3\nboto3.client("s3").put_object(Bucket="b", Key="k", Body=b"x")',
        'from kubernetes import client\n'
        'client.AppsV1Api().patch_namespaced_deployment("d","prod",{})',
        'from kubernetes import client\n'
        'client.AppsV1Api().replace_namespaced_deployment("d","prod",{})',
    ])
    def test_chained_cloud_mutation_is_dangerous(self, src):
        result = analyze_python_inline(src)
        assert result.is_dangerous is True
        assert result.category == "CLOUD_SDK"

    def test_cloud_mutation_via_local_binding_is_dangerous(self):
        result = analyze_python_inline(
            'import boto3\ns3 = boto3.client("s3")\ns3.delete_object(Bucket="b", Key="k")'
        )
        assert result.is_dangerous is True
        assert result.category == "CLOUD_SDK"

    def test_camel_case_verb_is_detected(self):
        result = analyze_python_inline(
            'from googleapiclient.discovery import build\n'
            'build("compute","v1").instances().deleteAccessConfig().execute()'
        )
        assert result.is_dangerous is True
        assert result.category == "CLOUD_SDK"

    def test_cloud_delete_via_script_file_is_mutative(self, tmp_path):
        path = _write_script(tmp_path, BOTO3_DELETE_SRC)
        result = detect_mutative_command(f"python3 {path}")
        assert result.is_mutative is True
        assert result.verb == "cloud-sdk-delete"


# ---------------------------------------------------------------------------
# The false-positive side: legitimate read-only work must stay free
# ---------------------------------------------------------------------------
class TestBenignPayloadsStayReadOnly:
    """A gate that blocks read-only work gets reverted, hole included."""

    @pytest.mark.parametrize("src", [
        'print(open("/a").read())',
        'print(open("/a", "r").read())',
        'print(open("/a", "rb").read())',
        'from pathlib import Path\nprint(Path("/a").read_text())',
        'from pathlib import Path\nprint(list(Path("/tmp").glob("*.py")))',
        'import os\nprint(os.listdir("/tmp"))',
        'import os\nprint(os.path.exists("/a"), os.path.getsize("/a"))',
        # `replace` is a cloud verb AND a string method: the prefix gate is
        # what keeps ordinary string work out of the cloud lane.
        's = "a-b"\nprint(s.replace("-", "_"))',
        'x = str(1)\nprint(x.replace("1", "2"))',
        'from pathlib import Path\np = Path("/a-b.txt")\nprint(p.name.replace("-","_"))',
        'import datetime\nprint(datetime.datetime.now().replace(hour=0))',
        'd = dict(a=1)\nprint(d.copy())',
        'import json\nprint(json.load(open("/a.json")))',
        'import sqlite3\nprint(sqlite3.connect("/x.db").cursor().execute("SELECT 1").fetchall())',
    ])
    def test_benign_local_payload_is_read_only(self, src):
        assert analyze_python_inline(src).is_dangerous is False

    @pytest.mark.parametrize("src", [
        'import boto3\nprint(boto3.client("s3").get_object(Bucket="b", Key="k"))',
        'import boto3\nprint(boto3.client("s3").list_objects_v2(Bucket="b"))',
        'import boto3\nprint(boto3.client("ec2").describe_instances())',
        # Local logging configuration, not a remote mutation: the reason `set`
        # is deliberately absent from the cloud verb set.
        'import boto3\nboto3.set_stream_logger("botocore")',
        'from google.cloud import storage\n'
        'print(storage.Client().bucket("b").blob("x").download_as_text())',
        'from kubernetes import client\nprint(client.CoreV1Api().list_namespaced_pod("prod"))',
        'from googleapiclient.discovery import build\n'
        'print(build("compute","v1").instances().get(project="p",zone="z",instance="i").execute())',
        # A dry-run is T2 by the tier ladder, never T3.
        'from python_terraform import Terraform\nprint(Terraform(working_dir="/r").plan())',
        # Token equality, not substring: "creation" must not read as "create".
        'import boto3\nprint(boto3.client("s3").get_creation_time())',
    ])
    def test_cloud_read_payload_is_read_only(self, src):
        assert analyze_python_inline(src).is_dangerous is False


# ---------------------------------------------------------------------------
# The ceiling, asserted rather than assumed
# ---------------------------------------------------------------------------
class TestKnownEscapes:
    """Static name matching is a gate, not containment.

    These assertions are deliberately ``is False``.  They exist so that a
    reader who trusts a clean result can see, in the suite itself, exactly
    which paths reach the same syscalls without naming anything in the
    catalog.  If a future change makes one of them True, that is an
    improvement -- update the assertion; do not read the current value as
    coverage.
    """

    def test_dynamic_getattr_dispatch_is_not_caught(self):
        assert analyze_python_inline(
            'import shutil\ngetattr(shutil, "copy" + "file")("/a", "/b")'
        ).is_dangerous is False

    def test_importlib_dispatch_is_not_caught(self):
        assert analyze_python_inline(
            'import importlib\nimportlib.import_module("shutil").copyfile("/a","/b")'
        ).is_dangerous is False

    def test_write_on_a_handle_bound_elsewhere_is_not_caught(self):
        assert analyze_python_inline(
            'def sink(p):\n    return p\n'
            'f = sink(None)\nf.write("x")\n'
        ).is_dangerous is False

    def test_uncatalogued_sdk_is_not_caught(self):
        assert analyze_python_inline(
            'import azure.mgmt.resource as az\n'
            'az.ResourceManagementClient(None, "s").resource_groups.begin_delete("rg")'
        ).is_dangerous is False

    def test_mode_from_a_variable_is_not_gated(self):
        # The mode is unresolvable statically, so the opener is not escalated.
        assert analyze_python_inline(
            'm = "w"\nopen("/a", m).write("x")'
        ).is_dangerous is False
