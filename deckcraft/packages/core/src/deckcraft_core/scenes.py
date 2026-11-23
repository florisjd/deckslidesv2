"""Typed scene contract (plan §5.3). One discriminated union; field limits come from each
layout's real capacity so overflow is rejected at the schema, not discovered in a render."""
from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, TypeAdapter, model_validator

Short = Annotated[str, Field(min_length=1, max_length=60)]
Line = Annotated[str, Field(min_length=1, max_length=140)]
Body = Annotated[str, Field(min_length=1, max_length=220)]
LeadText = Annotated[str, Field(max_length=170)]      # shared lead-in shown once above a list


class Base(BaseModel):
    claim_ids: list[int] = Field(default_factory=list)   # provenance
    notes: str = ""                                       # narration draft (beats come in Phase 3)


class Cover(Base):
    kind: Literal["cover"] = "cover"
    eyebrow: Short = "Training"
    title: Annotated[str, Field(min_length=1, max_length=80)]
    subtitle: Annotated[str, Field(max_length=120)] = ""


class Statement(Base):
    kind: Literal["statement"] = "statement"
    headline: Annotated[str, Field(min_length=1, max_length=140)]
    body: Annotated[str, Field(max_length=220)] = ""


class Stat(Base):
    kind: Literal["stat"] = "stat"
    value: Annotated[str, Field(min_length=1, max_length=14)]
    label: Short
    body: Annotated[str, Field(max_length=220)] = ""

    @model_validator(mode="after")
    def _digit(self):
        if not any(ch.isdigit() for ch in self.value):
            raise ValueError("stat value must contain a digit")
        return self


class Card(BaseModel):
    title: Annotated[str, Field(max_length=60)] = ""          # may be empty: the text then stands alone
    text: Annotated[str, Field(max_length=160)] = ""


class Cards(Base):
    kind: Literal["cards"] = "cards"
    heading: Short
    lead: LeadText = ""
    cards: Annotated[list[Card], Field(min_length=2, max_length=4)]


class Step(BaseModel):
    title: Short
    text: Annotated[str, Field(max_length=100)] = ""


class Process(Base):
    kind: Literal["process"] = "process"
    heading: Short
    steps: Annotated[list[Step], Field(min_length=3, max_length=5)]


class Checklist(Base):
    kind: Literal["checklist"] = "checklist"
    heading: Short
    lead: LeadText = ""
    items: Annotated[list[Line], Field(min_length=3, max_length=6)]


class Compare(Base):
    kind: Literal["compare"] = "compare"
    heading: Short
    do_lead: LeadText = ""
    dont_lead: LeadText = ""
    do: Annotated[list[Line], Field(min_length=2, max_length=4)]
    dont: Annotated[list[Line], Field(min_length=2, max_length=4)]


class Scenario(Base):
    kind: Literal["scenario"] = "scenario"
    situation: Body
    options: Annotated[list[Line], Field(min_length=2, max_length=3)]
    answer_index: int
    why: Body

    @model_validator(mode="after")
    def _idx(self):
        if not 0 <= self.answer_index < len(self.options):
            raise ValueError("answer_index out of range")
        return self


class MythFact(Base):
    kind: Literal["myth_fact"] = "myth_fact"
    myth: Body
    fact: Body


class Spotlight(Base):
    kind: Literal["spotlight"] = "spotlight"
    term: Short
    definition: Body


class Escalation(Base):
    kind: Literal["escalation"] = "escalation"
    heading: Short
    levels: Annotated[list[Step], Field(min_length=2, max_length=4)]


class Takeaways(Base):
    kind: Literal["takeaways"] = "takeaways"
    heading: Short = "Key takeaways"
    lead: LeadText = ""
    points: Annotated[list[Line], Field(min_length=2, max_length=4)]


class ImageScene(Base):
    """Narrating an existing slide: the rasterised slide is the visual."""
    kind: Literal["image"] = "image"
    image_sha: str
    caption: Annotated[str, Field(max_length=120)] = ""


Scene = Annotated[
    Union[Cover, Statement, Stat, Cards, Process, Checklist, Compare, Scenario, MythFact,
          Spotlight, Escalation, Takeaways, ImageScene],
    Field(discriminator="kind"),
]
SceneAdapter = TypeAdapter(Scene)
LAYOUT_KINDS = [t.model_fields["kind"].default for t in
                (Cover, Statement, Stat, Cards, Process, Checklist, Compare, Scenario, MythFact,
                 Spotlight, Escalation, Takeaways, ImageScene)]


def parse_scene(data: dict) -> Base:
    return SceneAdapter.validate_python(data)
