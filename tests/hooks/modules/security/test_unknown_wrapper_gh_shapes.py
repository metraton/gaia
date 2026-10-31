"""An unknown wrapper around gh pays consent for the mutations its arguments name."""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.tiers import SecurityTier, classify_command_tier  # noqa: E402

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED


def _clear_caches():
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture(autouse=True)
def _no_declared_wrapper(monkeypatch):
    _clear_caches()
    yield
    _clear_caches()


class TestUnknownWrapperMutationsAreSigned:
    @pytest.mark.parametrize("command", [
        "ghx pr merge 1",
        "ghx -C /repo pr merge 1",
        "ghx repo delete x",
        "ghx workflow run ci.yml --ref main",
        "ghx run rerun 123456",
        "ghx release create v1.0.0",
        "ghx issue close 1",
        "ghx pr edit 1 --base main",
        "ghx auth switch -u someone",
        "ghx auth logout",
        "ghx api repos/o/r/issues -X POST -f title=t",
        "ghx api repos/o/r --method DELETE",
        "ghx pr comment 1 --body x",
        "ghx secret set NAME --body x",
        "ghx repo edit o/r --visibility public",
        "ghx pr close 1",
        "ghx pr update-branch 1",
    ])
    def test_mutation_is_consent(self, command):
        assert classify_command_tier(command) == T3, command

    @pytest.mark.parametrize("command", [
        "ghx api repos/o/r/issues -f title=t",
        "ghx api repos/o/r/issues -F body=@file",
        "ghx api repos/o/r/issues --field title=t",
        "ghx api repos/o/r/issues --raw-field title=t",
        "ghx api repos/o/r/issues --field=title=t",
        "ghx api repos/o/r/issues --input payload.json",
        "ghx api repos/o/r/issues -ftitle=t",
        "ghx api repos/o/r -XPATCH",
        "ghx api repos/o/r --method=PUT",
        "ghx -R o/r api repos/o/r/issues -f title=t",
        "otherbin api some/path -f k=v",
        "ghx pr review 1 --approve",
        "ghx pr review 1 --request-changes --body x",
        "ghx pr review 1 --comment --body x",
        "ghx pr review 1 -a",
        "ghx pr ready 1",
        "ghx pr ready 1 --undo",
        "otherbin mr ready 1",
        "otherbin pull-request ready 1",
        "otherbin merge-request ready 1",
    ])
    def test_generic_shape_mutation_is_consent(self, command):
        assert classify_command_tier(command) == T3, command

    @pytest.mark.parametrize("command", [
        "ghx api repos/o/r/issues",
        "ghx api repos/o/r/issues -X GET",
        "ghx api search/issues -X GET -f q=is:open",
        "ghx api repos/o/r --method GET --field q=x",
        "ghx api repos/o/r/issues --jq .[].title",
        "ghx api repos/o/r -H Accept:application/json",
        "ghx pr review 1",
        "ghx pr checkout 1",
        "ghx pr list --state ready",
        "git checkout ready",
        "git log ready",
        "kubectl get pods",
        "kubectl describe pod x",
        "gcloud compute instances list",
        "aws s3api list-buckets",
        "aws ec2 describe-instances",
        "otherbin api some/path",
        "make ready",
        "kubectl logs ready",
        "kubectl describe ready",
        "docker logs ready",
        "docker inspect ready",
        "helm status ready",
        "systemctl status ready",
        "ghx api graphql -f query=query{viewer{login}}",
        "ghx api graphql -F query=query($n:Int!){viewer{login}} -F n=1",
    ])
    def test_generic_shape_read_is_free(self, command):
        assert classify_command_tier(command) == T0, command

    @pytest.mark.parametrize("command", [
        "ghx api graphql -f query=mutation{addStar(input:{starrableId:\"x\"}){clientMutationId}}",
        "ghx api graphql -f query='  MUTATION { x }'",
        "ghx api graphql --field query=mutation{x}",
        "ghx api graphql -f query=@/nonexistent/doc.graphql",
        "ghx api graphql --input /nonexistent/payload.json",
    ])
    def test_graphql_mutation_or_unreadable_document_is_consent(self, command):
        assert classify_command_tier(command) == T3, command

    def test_graphql_document_in_a_file_is_read_by_content(self, tmp_path):
        query = tmp_path / "q.graphql"
        query.write_text("query { viewer { login } }")
        mutation = tmp_path / "m.graphql"
        mutation.write_text("mutation { addStar(input: {starrableId: \"x\"}) { clientMutationId } }")
        payload = tmp_path / "p.json"
        payload.write_text('{"query": "query { viewer { login } }"}')
        assert classify_command_tier(f"ghx api graphql -F query=@{query}") == T0
        assert classify_command_tier(f"ghx api graphql --input {payload}") == T0
        assert classify_command_tier(f"ghx api graphql -F query=@{mutation}") == T3

    @pytest.mark.parametrize("command", [
        "ghx pr list",
        "ghx -C /repo pr list",
        "ghx pr view 1",
        "ghx pr checks 1",
        "ghx run list",
        "ghx run view 123456",
        "ghx issue list",
        "ghx repo view o/r",
        "ghx auth status",
    ])
    def test_read_is_free(self, command):
        assert classify_command_tier(command) == T0, command

    @pytest.mark.parametrize("command", [
        "ghx pr create --title t --body b",
        "ghx -C /repo pr create --title t",
        "ghx pr edit 1 --title t",
    ])
    def test_opening_or_editing_own_change_request_is_free(self, command):
        assert classify_command_tier(command) == T0, command
