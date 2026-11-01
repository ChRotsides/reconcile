"""Run `python -m <module> args...` and log every file opened under data/ to a file.
The log is opened exclusively ("x"), so an existing log is never overwritten."""

import os
import runpy
import sys

log_path = os.environ["READ_LOG"]
seen = set()


def hook(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        p = os.fsdecode(args[0])
        if "/data/" in os.path.abspath(p) + "/" and p not in seen:
            seen.add(p)


sys.addaudithook(hook)
module = sys.argv[1]
sys.argv = [module] + sys.argv[2:]
try:
    runpy.run_module(module, run_name="__main__", alter_sys=True)
finally:
    with open(log_path, "x") as fh:
        fh.write("\n".join(sorted(os.path.abspath(p) for p in seen)) + "\n")
