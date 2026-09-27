"""Runs ONLY inside the disposable container. The container is the security boundary.

Generated code may use all Python capabilities inside that boundary; this wrapper
does not claim Python globals, AST filtering or exception handling isolate code.
"""
import io
import json
import sys
from pathlib import Path
import pandas as pd


def main():
    # Verify effective kernel quotas before reading any exported business rows.
    # cgroup v2 values must be present; flags accepted but ignored are insufficient.
    cgroup = Path("/sys/fs/cgroup")
    memory = (cgroup / "memory.max").read_text().strip()
    pids = (cgroup / "pids.max").read_text().strip()
    cpu = (cgroup / "cpu.max").read_text().split()
    if (memory == "max" or int(memory) > 536870912 or pids == "max" or int(pids) > 64
            or cpu[0] == "max" or int(cpu[0]) > int(cpu[1])):
        raise RuntimeError("kernel_quotas_unavailable")
    job = json.loads(sys.stdin.buffer.read(2 * 1024 * 1024 + 1))
    frames = {name: pd.DataFrame(rows) for name, rows in job["frames"].items()}
    scope = {"pd": pd, "frames": frames}
    # stdout is a bounded transport, not a trusted channel. The broker validates
    # everything again, and source/cleanup evidence is never supplied by code.
    output = sys.stdout
    sys.stdout = io.StringIO()
    try:
        exec(compile(job["code"], "<analysis>", "exec"), scope, scope)
        result = scope.get("result")
        if isinstance(result, pd.Series):
            result = result.to_frame().reset_index()
        if not isinstance(result, pd.DataFrame):
            raise ValueError("result must be a DataFrame")
        if len(result) > 100 or len(result.columns) > 20 or not result.columns.is_unique:
            raise ValueError("output_limit")
        result = result.copy()
        result.columns = [str(c) for c in result.columns]
        rows = json.loads(result.to_json(orient="records", date_format="iso", force_ascii=False))
        output.write(json.dumps({"columns": list(result.columns), "rows": rows}, ensure_ascii=False, allow_nan=False))
        output.flush()
    except BaseException:
        # No source rows, code, file paths or tracebacks in runner diagnostics.
        sys.exit(2)


if __name__ == "__main__":
    main()
