"""Block until a backgrounded pytest run finishes, then report its progress.

A census over the whole suite outlives the harness's foreground timeout, so the
run is backgrounded and its stdout tailed from a file. Polling that file once
per agent tool call spends one call per sample, which exhausts a turn budget
long before an hour-long run ends. This waits INSIDE a single call instead:
it samples the file every few seconds and returns as soon as pytest's own
summary line appears, or when the caller's time box expires.

Argv: <output-file> [max-seconds]. Exit 0 = run finished, 2 = still running.
"""

import re
import sys
import time
from pathlib import Path

# pytest's terminal summary is the only unambiguous end-of-run marker: the
# progress percentages reach 100% slightly BEFORE teardown finishes writing.
DONE_RE = re.compile(
    r"^(?:=+ )?\d+ (?:passed|failed)|^=+ (?:ERRORS|FAILURES|short test summary)",
    re.MULTILINE,
)
PROGRESS_RE = re.compile(r"\[ *(\d+)%\]")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def main() -> int:
    path = Path(sys.argv[1])
    deadline = time.monotonic() + (float(sys.argv[2]) if len(sys.argv) > 2 else 540.0)
    while True:
        text = path.read_text(errors="replace") if path.exists() else ""
        pct = PROGRESS_RE.findall(text)
        # Report failures as they stream in, not only from the final summary:
        # the progress line prints one char per test, so a triage can start
        # before the run ends. Colour codes are stripped first so the F/E
        # count is not inflated by SGR payloads.
        plain = ANSI_RE.sub("", text)
        marks = {c: plain.count(c) for c in "FE"}
        tally = f"F={marks['F']} E={marks['E']}"
        if DONE_RE.search(text):
            print(f"DONE at {pct[-1] if pct else '?'}% {tally}")
            return 0
        if time.monotonic() >= deadline:
            print(f"STILL RUNNING at {pct[-1] if pct else '?'}% {tally}")
            return 2
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
