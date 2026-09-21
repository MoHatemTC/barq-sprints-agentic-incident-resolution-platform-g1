from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Cell:
    text: str
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
