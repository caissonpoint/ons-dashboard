#!/usr/bin/env python3
"""Write .state/last_refresh.json after a successful build.

Kept as a file rather than a heredoc inside the workflow: a heredoc terminator
is line-ending sensitive, and a repo pushed from Windows can carry CRLF into
the YAML, at which point the Linux runner never matches the terminator.
"""

import datetime as dt
import json
from pathlib import Path

import pandas as pd

df = pd.read_parquet("data/daily.parquet")
stamp = {
    "refreshed_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "latest_data_date": str(pd.to_datetime(df["date"]).max().date()),
    "rows": int(len(df)),
}
Path(".state").mkdir(exist_ok=True)
Path(".state/last_refresh.json").write_text(json.dumps(stamp, indent=2) + "\n")
print(json.dumps(stamp))
