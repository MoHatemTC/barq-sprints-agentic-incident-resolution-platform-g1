from __future__ import annotations

from pydantic import BaseModel, field_validator


class WorkNoteUpdate(BaseModel):
    note: str

    @field_validator("note")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("work note text must not be blank")
        return v

    def to_table_api_body(self) -> dict[str, str]:
        return {"work_notes": self.note}
