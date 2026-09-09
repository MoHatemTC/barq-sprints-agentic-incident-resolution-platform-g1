from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Incident(BaseModel):
    model_config = ConfigDict(extra="allow")

    sys_id: str
    number: str
    short_description: str = ""
    description: str = ""
    state: str
    priority: str = ""
    category: str = ""
    subcategory: str = ""
    active: bool
