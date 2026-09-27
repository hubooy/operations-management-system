"""Exercise the production budget XLSX writer in native Excel with synthetic data.

The generated workbook exists only in a temporary directory. Excel opens it
read-only, changes editable cells in memory, recalculates, and never saves.
"""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from business_analysis.budget import calculate  # noqa: E402
from business_analysis.budget_offline import payload  # noqa: E402
from business_analysis.report_files import Column, Table, write_pair  # noqa: E402
from business_analysis.test_budget import fixture  # noqa: E402


def main():
    runtime = (ROOT / ".runtime").resolve()
    runtime.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="excel-native-budget-", dir=runtime) as name:
        folder = Path(name).resolve()
        if folder.parent != runtime:
            raise RuntimeError("Synthetic Excel fixture escaped the isolated runtime")
        workbook = folder / "budget.xlsx"
        html = folder / "budget.html"
        plan, baselines = fixture()
        model = payload(calculate(plan, baselines), "synthetic-native-excel")
        with workbook.open("wb") as xlsx_file, html.open("wb") as html_file:
            write_pair(xlsx_file, html_file, title="合成预算验收", metadata={},
                tables=[Table("source", "合成来源", "仅用于 Excel 原生复算",
                    (Column("value", "原值", "integer"),), [[1]], 1)],
                excel_budget=model, xlsx_opc_version=2)
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File",
            str(ROOT / "tests" / "excel-native-recalc-probe.ps1"),
            "-WorkbookPath", str(workbook), "-VerifyBudgetFixture"],
            text=True, encoding="utf-8", errors="replace", capture_output=True,
            timeout=180, check=False)
        if result.returncode:
            raise RuntimeError("Native Excel budget fixture failed: " +
                (result.stderr or "")[-1000:])
        print(result.stdout.strip())


if __name__ == "__main__":
    main()
