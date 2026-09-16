"""Evidence labels.

Pack 1 rule: nothing in this system may be presented as validated. Every
number that reaches an operator carries a label saying where it came from.

    SYNTHETIC   - invented input data, not measured, not supplied by any
                  armed forces organisation.
    SIMULATED   - produced by this model from synthetic inputs.
    ASSUMED     - a modelling assumption chosen by the developers.
    UNVALIDATED - not checked against field data or an external reference.
    DERIVED     - computed from other labelled values; inherits their weakest
                  label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class EvidenceLabel(str, Enum):
    SYNTHETIC = "SYNTHETIC"
    SIMULATED = "SIMULATED"
    ASSUMED = "ASSUMED"
    UNVALIDATED = "UNVALIDATED"
    DERIVED = "DERIVED"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Shown on every UI screen and at the top of every CLI report.
DEMONSTRATOR_DISCLAIMER = (
    "SYNTHETIC DATA - SIMULATED RESULTS - UNVALIDATED. "
    "Mission Machine is a research demonstrator. It has no military validation, "
    "no operational readiness status, and no requirement basis from any armed "
    "forces organisation. The operator is the decision authority."
)

#: Weakest-first ordering, used when a derived value inherits labels.
_STRENGTH = {
    EvidenceLabel.UNVALIDATED: 0,
    EvidenceLabel.ASSUMED: 1,
    EvidenceLabel.SYNTHETIC: 2,
    EvidenceLabel.SIMULATED: 3,
    EvidenceLabel.DERIVED: 4,
}


@dataclass(frozen=True)
class Provenance:
    """Where a value came from, and how much weight it can carry."""

    labels: tuple[EvidenceLabel, ...] = (EvidenceLabel.SYNTHETIC,)
    source: str = "mission-machine/pack-1 synthetic data set"
    note: str = ""

    @classmethod
    def synthetic(cls, source: str, note: str = "") -> "Provenance":
        return cls(
            labels=(EvidenceLabel.SYNTHETIC, EvidenceLabel.UNVALIDATED),
            source=source,
            note=note,
        )

    @classmethod
    def simulated(cls, source: str, note: str = "") -> "Provenance":
        return cls(
            labels=(EvidenceLabel.SIMULATED, EvidenceLabel.UNVALIDATED),
            source=source,
            note=note,
        )

    @classmethod
    def assumed(cls, source: str, note: str = "") -> "Provenance":
        return cls(
            labels=(EvidenceLabel.ASSUMED, EvidenceLabel.UNVALIDATED),
            source=source,
            note=note,
        )

    def weakest(self) -> EvidenceLabel:
        return min(self.labels, key=lambda lab: _STRENGTH[lab])

    @property
    def is_operational_truth(self) -> bool:
        """True only when a value may be shown to an operator as describing the world.

        Computed rather than asserted, so that the claim can be tested. In Pack 1
        it is False everywhere by construction: every value in the system is
        synthetic, assumed or unvalidated, and usually all three.
        """

        return not any(
            label
            in (
                EvidenceLabel.SYNTHETIC,
                EvidenceLabel.ASSUMED,
                EvidenceLabel.UNVALIDATED,
            )
            for label in self.labels
        )

    def to_dict(self) -> dict:
        return {
            "labels": [str(lab) for lab in self.labels],
            "source": self.source,
            "note": self.note,
        }


def is_operational_truth(labels: Iterable[str | EvidenceLabel]) -> bool:
    """Whether a labelled payload may be presented as describing the world.

    The same rule as :attr:`Provenance.is_operational_truth`, for the flat
    ``data_labels`` lists that travel in JSON payloads.
    """

    return Provenance(labels=tuple(EvidenceLabel(lab) for lab in labels)).is_operational_truth


def label_block(labels: Iterable[EvidenceLabel]) -> str:
    """Render labels as a short banner, e.g. ``[SYNTHETIC | UNVALIDATED]``."""

    ordered = sorted({EvidenceLabel(lab) for lab in labels}, key=lambda lab: _STRENGTH[lab])
    return "[" + " | ".join(str(lab) for lab in ordered) + "]"
