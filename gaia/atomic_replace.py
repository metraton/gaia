"""``os.replace`` that survives a transient hold on the target file."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Union

# Windows refuses to replace a file another process holds open without
# FILE_SHARE_DELETE (antivirus and the search indexer do, for milliseconds) and
# reports it as PermissionError. Worst case the attempts wait 2.25 s in total.
REPLACE_ATTEMPTS = 10
REPLACE_BACKOFF_SECONDS = 0.05


def replace_retrying(source: Union[str, Path], target: Union[str, Path]) -> None:
    """Replace *target* with *source*, retrying while the target is held; the last PermissionError propagates."""
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS:
                raise
            time.sleep(REPLACE_BACKOFF_SECONDS * attempt)
