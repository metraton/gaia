"""Quick triage scripts per domain, started by run_triage.sh.

run_triage.sh takes all (the default), gitops (k8s, kubernetes), terraform (tf)
or gcp (cloud) and runs the matching quicktriage_*.sh script. The scripts under
cloud/aws and appservices exist but run_triage.sh does not call them.

See README.md.
"""

__version__ = "1.0.0"
__all__ = [
    "terraform",
    "gitops",
    "cloud",
    "appservices",
]
