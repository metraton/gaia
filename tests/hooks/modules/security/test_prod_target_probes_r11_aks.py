"""AKS Entra-ID kubeconfig (installHint block scalar): reads run free, writes ask, and a signed retry runs."""

from __future__ import annotations

from tests.hooks.modules.security.test_prod_target_probes_r6 import ASK, _mint, _validate, env, put, verdict
from tests.hooks.modules.security.test_prod_target_probes_r11 import AKS_AAD

__all__ = ["env"]


def test_r11_aks_aad_dev_read_and_grant(env):
    put(env["home"] / ".kube" / "config", AKS_AAD.format(e="dev"))
    assert verdict("kubectl get pods", env["work"])[0] == "FREE"
    assert verdict("kubectl frobnicate x", env["work"])[0] in ASK
    _mint("kubectl apply -f x.yaml", env["work"])
    after = _validate("kubectl apply -f x.yaml", env["work"])
    assert after.allowed, (after.reason or "")[:200]
    flagged = _validate("kubectl --context shop-dev apply -f x.yaml", env["work"])
    assert not flagged.allowed and flagged.approval_id, (flagged.reason or "")[:200]
