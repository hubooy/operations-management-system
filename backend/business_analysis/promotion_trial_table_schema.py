"""Versioned renderer-9 declaration proof; never consumes source rows."""
from .contracts import AnalysisContractError, digest
from .report_files import Column, Table


SCHEMA = "promotion-trial-table-declarations-v1"


def digest_tables(tables):
    if type(tables) not in (list, tuple) or not 1 <= len(tables) <= 12000:
        raise AnalysisContractError("词货试用版表声明数量无效")
    declarations = []
    for table in tables:
        if type(table) is not Table or type(table.columns) not in (list, tuple):
            raise AnalysisContractError("词货试用版表声明类型无效")
        columns = []
        for column in table.columns:
            if type(column) is not Column:
                raise AnalysisContractError("词货试用版列声明类型无效")
            columns.append({"key": column.key, "label": column.label,
                "kind": column.kind, "total": column.total,
                "ratioOf": list(column.ratio_of) if column.ratio_of is not None else None})
        declarations.append({"key": table.key, "title": table.title,
            "note": table.note, "columns": columns})
    return digest({"schemaVersion": SCHEMA, "tables": declarations})
