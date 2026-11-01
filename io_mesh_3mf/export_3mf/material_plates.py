"""
Plate-per-material planning for Orca/Bambu export.

Pure Python — no bpy.  Given each export unit's material key (and a display
label) plus any explicit ``3mf_plate`` request, decide the plate number of
every unit and a name for each automatically created plate.

Rules (mirrors Orca's "allow multiple materials on a single plate" = off):

* One plate per distinct material key, numbered 1.. in first-seen order.
  Plates are global: all red parts from every assembly share the red plate.
* A unit with no determinable material shares one "No material" plate.
* Explicit ``3mf_plate`` requests reserve their plates. Unassigned units of
  the same material use its first explicit plate; others use the first free
  plate, avoiding unintended mixing.
* The plate name is the material label of the first unit that created it.
"""

from typing import Dict, Hashable, List, Optional, Sequence, Tuple

from .plate_model import MAX_PLATES

NO_MATERIAL_LABEL = "No material"


def plan_material_plates(
    units: Sequence[Tuple[Optional[Hashable], Optional[str]]],
    explicit: Sequence[Optional[int]],
) -> Tuple[List[int], Dict[int, str]]:
    """
    Plan plates for *units*.

    :param units: ``(material_key, label)`` per unit, in export order.  A key
        of ``None`` means "no material".
    :param explicit: Requested plate per unit (1-based) or ``None``; same
        length as *units*.
    :return: ``(plate_numbers, names)``: the plate for each unit, and
        ``{plate_number: name}`` for automatically created plates.
    """
    if len(units) != len(explicit):
        raise ValueError("units and explicit must have the same length")

    auto_number: Dict[Optional[Hashable], int] = {}
    reserved = set()
    names: Dict[int, str] = {}
    plates: List[int] = []

    for (key, _), requested in zip(units, explicit):
        if requested is not None:
            auto_number.setdefault(key, requested)
            reserved.add(requested)

    for (key, label), requested in zip(units, explicit):
        if key not in auto_number:
            number = next((n for n in range(1, MAX_PLATES + 1) if n not in reserved), MAX_PLATES)
            auto_number[key] = number
            reserved.add(number)
        number = requested if requested is not None else auto_number[key]
        plates.append(number)
        names.setdefault(number, label or NO_MATERIAL_LABEL)

    return plates, names
