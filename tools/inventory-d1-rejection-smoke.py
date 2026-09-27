from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: inventory-d1-rejection-smoke.py <live-d1-path>")
    path = Path(sys.argv[1]).resolve()
    if not path.is_file() or path.is_symlink():
        raise SystemExit("live D1 path must be a regular file")

    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE inventory_import_batches SET status=status "
            "WHERE id=(SELECT id FROM inventory_import_batches LIMIT 1)"
        )
    except sqlite3.IntegrityError as error:
        connection.rollback()
        if "inventory_write_authority_not_d1" not in str(error):
            raise
        print(f"{type(error).__name__}:{error}")
        return 0
    else:
        connection.rollback()
        raise SystemExit("legacy inventory D1 write was not rejected")
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
