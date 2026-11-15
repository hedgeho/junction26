from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileProposal(StrictModel):
    markdown: str = Field(min_length=1, max_length=24000)
    search_terms: list[str] = Field(default_factory=list, max_length=30)


class PreferenceLine(StrictModel):
    category: str = Field(pattern=r"^[a-z_]{1,20}$")
    text: str = Field(min_length=1, max_length=300)


class ProfileEdits(StrictModel):
    # Every list is required in the schema, so guided decoding cannot shortcut to an empty object.
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"required": ["remove", "add", "add_terms", "remove_terms"]}
    )

    remove: list[int] = Field(default_factory=list, max_length=20)
    add: list[PreferenceLine] = Field(default_factory=list, max_length=5)
    add_terms: list[str] = Field(default_factory=list, max_length=10)
    remove_terms: list[str] = Field(default_factory=list, max_length=20)


class Assessment(StrictModel):
    post_key: str
    decision: Literal["include", "exclude", "insufficient_context"]
    relevance: float = Field(ge=0, le=1, allow_inf_nan=False)
    substance: float = Field(ge=0, le=1, allow_inf_nan=False)
    opportunity: float = Field(ge=0, le=1, allow_inf_nan=False)


class Assessments(StrictModel):
    items: list[Assessment]


class ChunkMatch(StrictModel):
    chunk_id: str
    relevance: float = Field(ge=0, le=1, allow_inf_nan=False)


class Matches(StrictModel):
    items: list[ChunkMatch] = Field(max_length=3)


class SeedProfile(StrictModel):
    interests: list[str] = Field(max_length=8)
    search_terms: list[str] = Field(max_length=12)
