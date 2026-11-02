"""
Plate data model for Orca/Bambu export.

Pure Python — no bpy.  A ``PlateLayout`` is one ``<plate>`` entry in
``model_settings.config``: which wrapper objects (and instances) sit on it.
Emitters iterate plates instead of hard-coding plate 1, so plate-assignment
strategies (per material, per property, ...) only have to produce this list.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from ..common.plates import MAX_PLATES, PLATE_NAME_PROPERTY, PLATE_PROPERTY


@dataclass
class PlateInstance:
    """One ``model_instance`` entry on a plate."""

    object_id: int
    instance_id: int = 0
    identify_id: int = 0


@dataclass
class PlateLayout:
    """One ``<plate>`` entry.  *index* is zero-based; ``plater_id`` is index + 1."""

    index: int
    name: str = ""
    instances: List[PlateInstance] = field(default_factory=list)

    @property
    def plater_id(self) -> int:
        return self.index + 1


def plate_property(obj) -> Optional[int]:
    """
    Read the ``3mf_plate`` custom property as a plate number (1-based).

    Accepts ints and whole-number floats.  Anything else, or a value below 1,
    is treated as unset.  Values above ``MAX_PLATES`` are clamped.
    """
    value = obj.get(PLATE_PROPERTY)
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or value < 1:
        return None
    return min(value, MAX_PLATES)


def explicit_unit_plate(empty, members: Iterable) -> Tuple[Optional[int], bool]:
    """
    Explicit plate request for one export unit, or ``None`` if there is none.

    The assembly Empty's property wins, then the first member that has one.
    Returns ``(plate_number_or_None, conflict)``; *conflict* is True when
    members ask for different plates (the first request is used).
    """
    if empty is not None:
        number = plate_property(empty)
        if number is not None:
            return number, False
    requested = [n for n in (plate_property(m) for m in members) if n is not None]
    if not requested:
        return None, False
    return requested[0], len(set(requested)) > 1


def resolve_unit_plate(empty, members: Iterable) -> Tuple[int, bool]:
    """Like :func:`explicit_unit_plate`, but defaults to plate 1."""
    number, conflict = explicit_unit_plate(empty, members)
    return (1 if number is None else number), conflict


def layout_plates(
    groups: List[dict],
    ungrouped: List[dict],
    names: Optional[Dict[int, str]] = None,
) -> List[PlateLayout]:
    """
    Build plate layouts from export units.

    Each unit dict carries ``wrapper_id`` and optionally ``plate`` (1-based,
    default 1).  Plates 1..N are all created, so a gap (units on plates 1 and
    3) yields an empty plate 2, because Orca derives the plate count from the
    plate entries.  Within a plate, assemblies come first, then loose objects,
    which is the order the exporter has always emitted.  *names* maps plate
    number to plate name.  With no ``plate`` keys and no names this is exactly
    the single-plate layout.
    """
    units = list(groups) + list(ungrouped)
    highest = max((int(u.get("plate", 1)) for u in units), default=1)
    plates: Dict[int, PlateLayout] = {
        n: PlateLayout(index=n - 1, name=(names or {}).get(n, ""))
        for n in range(1, highest + 1)
    }
    for unit in units:
        wrapper_id = int(unit["wrapper_id"])
        plates[int(unit.get("plate", 1))].instances.append(
            PlateInstance(object_id=wrapper_id, instance_id=0, identify_id=wrapper_id)
        )
    return [plates[n] for n in sorted(plates)]


def single_plate_layout(groups: List[dict], ungrouped: List[dict]) -> List[PlateLayout]:
    """Default layout: every wrapper object on plate 1 (today's behavior)."""
    return layout_plates(
        [{**g, "plate": 1} for g in groups], [{**u, "plate": 1} for u in ungrouped]
    )
