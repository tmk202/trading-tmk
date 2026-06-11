from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, is_dataclass
from typing import Iterable


class CopyTradeStore:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def append_csv(self, name: str, rows: Iterable[object]) -> str:
        materialized = [self._to_row(row) for row in rows]
        path = os.path.join(self.data_dir, name)
        if not materialized:
            return path

        fieldnames = list(materialized[0].keys())
        exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if not exists:
                writer.writeheader()
            writer.writerows(materialized)
        return path

    def append_jsonl(self, name: str, rows: Iterable[object]) -> str:
        path = os.path.join(self.data_dir, name)
        with open(path, "a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(self._to_row(row), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        return path

    def _to_row(self, row: object) -> dict:
        if hasattr(row, "to_row"):
            return row.to_row()
        if is_dataclass(row):
            return asdict(row)
        if isinstance(row, dict):
            return row
        raise TypeError(f"Unsupported row type: {type(row)!r}")

