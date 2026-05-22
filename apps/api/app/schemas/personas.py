"""Opponent persona schemas (PRD §7.3 / US-A2).

`PersonaCard` is the public, card-facing shape — it has **no
`system_prompt` field** by design (PRD §6: the role-play seed is never
shown to the user).
"""

from pydantic import BaseModel, Field


class PersonaCard(BaseModel):
    """One persona as rendered on the US-A2 picker card.

    Fields mirror PRD §10.2's card spec (头像 / 名字 / 年龄 / 背景一句话)
    plus `difficulty` for easy → hard ordering. The internal
    `system_prompt` is deliberately absent.
    """

    id: str = Field(..., examples=["p_hard"])
    name: str = Field(..., description="The persona's name.", examples=["赵刚"])
    style: str = Field(
        ...,
        description="Archetype label — one of the 4 fixed base personas.",
        examples=["强硬型"],
    )
    age: int = Field(..., ge=1, description="The persona's age.", examples=[45])
    avatar: str = Field(
        ...,
        description="Avatar asset key the client maps to an image.",
        examples=["persona-hard"],
    )
    background: str = Field(
        ...,
        description="One-line persona blurb shown on the picker card.",
        examples=["目标明确、节奏快，习惯用权威和数字压人，不爱绕弯子。"],
    )
    difficulty: int = Field(
        ...,
        ge=1,
        le=5,
        description="1 = easy / 5 = hard. Drives 从易到难 ordering on the picker.",
        examples=[3],
    )


class PersonaListResponse(BaseModel):
    """`GET /v1/personas` — the 4 base opponent personas."""

    items: list[PersonaCard]
    total: int = Field(..., ge=0, description="Count of personas in `items`.")
