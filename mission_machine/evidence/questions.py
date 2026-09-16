"""Open questions - an explicit gap in preference to an invented number.

When Mission Machine cannot count a quantity, it has two honest options and one
dishonest one. It can count it, or it can withhold it and say so. What it must
not do is report the withheld quantity as zero, because zero is a measurement
and absence is not.

An :class:`OpenQuestion` is the second option made visible: the quantity that
was not counted, why it was not counted, and what would change if somebody
answered. It is deliberately small - a key, a question, an impact and a number -
so that it can be attached to any metric without dragging a framework behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class OpenQuestion:
    """Something unresolved, and the quantity it is holding back."""

    key: str
    """Stable identifier, so the same question de-duplicates across time steps."""

    topic: str
    question: str
    impact: str
    """What answering it would change."""

    withheld_kwh: float | None = None
    """Energy not counted because of this question, where it is quantifiable."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "topic": self.topic,
            "question": self.question,
            "impact": self.impact,
            "withheld_kwh": round(self.withheld_kwh, 2) if self.withheld_kwh is not None else None,
        }


def merge(questions: Iterable[OpenQuestion]) -> list[OpenQuestion]:
    """Collapse repeated questions, keeping the largest quantity each withheld.

    The same question is usually raised at every time step. The operator needs
    to see it once, with the worst case attached.
    """

    worst: dict[str, OpenQuestion] = {}
    for question in questions:
        existing = worst.get(question.key)
        if existing is None:
            worst[question.key] = question
            continue
        if (question.withheld_kwh or 0.0) > (existing.withheld_kwh or 0.0):
            worst[question.key] = question
    return sorted(worst.values(), key=lambda q: (-(q.withheld_kwh or 0.0), q.key))
