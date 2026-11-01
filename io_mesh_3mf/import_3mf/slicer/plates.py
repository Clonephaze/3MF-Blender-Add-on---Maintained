"""Read Orca/Bambu plate membership and grid placement from a 3MF archive."""

import json
import math
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, TYPE_CHECKING

from ...common.plates import MAX_PLATES, plate_origin, printable_area_size

if TYPE_CHECKING:
    from ..context import ImportContext


@dataclass(frozen=True)
class PlateAssignment:
    number: int
    name: str
    grid_offset_mm: Optional[Tuple[float, float]]
    assemble_transform: Optional[str] = None


def _metadata(element: ET.Element) -> Dict[str, str]:
    values = {}
    for entry in element.findall("metadata"):
        key, value = entry.get("key"), entry.get("value")
        if key is not None and value is not None:
            values[key] = value
    return values


def read_orca_plates(ctx: "ImportContext", archive_path: str) -> None:
    """Populate build-item plate assignments, retaining unknown positions."""
    ctx.plate_assignments.clear()
    ctx.has_plate_metadata = False
    ctx.plate_count = 0
    settings_path = "Metadata/model_settings.config"
    project_path = "Metadata/project_settings.config"

    try:
        with zipfile.ZipFile(archive_path) as archive:
            if settings_path not in archive.namelist():
                return
            try:
                root = ET.fromstring(archive.read(settings_path))
            except ET.ParseError as exc:
                ctx.safe_report({"WARNING"}, f"Cannot read plate settings: {exc}")
                return

            plates = []
            seen_numbers = set()
            for plate in root.findall("plate"):
                metadata = _metadata(plate)
                try:
                    number = int(metadata["plater_id"])
                    if not 1 <= number <= MAX_PLATES or number in seen_numbers:
                        raise ValueError("invalid or duplicate plate number")
                except (KeyError, ValueError):
                    ctx.safe_report({"WARNING"}, "Ignoring a plate with an invalid or duplicate ID.")
                    continue
                seen_numbers.add(number)
                plates.append((number, metadata.get("plater_name", ""), plate))

            if not plates:
                return
            ctx.has_plate_metadata = True
            plate_count = max(number for number, _, _ in plates)
            ctx.plate_count = plate_count
            assemble_transforms = {}
            for item in root.findall("./assemble/assemble_item"):
                try:
                    object_id = int(item.attrib["object_id"])
                    instance_id = int(item.attrib["instance_id"])
                    transform = item.attrib["transform"]
                    values = [float(value) for value in transform.split()]
                    if (
                        object_id <= 0 or instance_id < 0 or len(values) != 12
                        or not all(math.isfinite(value) for value in values)
                    ):
                        raise ValueError("invalid assemble transform")
                except (KeyError, ValueError):
                    ctx.safe_report({"WARNING"}, "Ignoring an invalid assemble item.")
                    continue
                key = (str(object_id), instance_id)
                if key in assemble_transforms:
                    ctx.safe_report(
                        {"WARNING"},
                        f"Object {object_id} instance {instance_id} has multiple "
                        "assemble entries; using the first.",
                    )
                    continue
                assemble_transforms[key] = " ".join(transform.split())

            bed_size = None
            if plate_count > 1:
                try:
                    project = json.loads(archive.read(project_path))
                    bed_size = printable_area_size(project["printable_area"])
                except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
                    ctx.safe_report(
                        {"WARNING"},
                        f"Cannot determine the plate bed dimensions ({exc}); "
                        "keeping multi-plate objects at their global positions.",
                    )

            for number, name, plate in plates:
                offset = (0.0, 0.0)
                if number > 1:
                    offset = (
                        plate_origin(number - 1, plate_count, *bed_size)
                        if bed_size is not None else None
                    )
                for instance in plate.findall("model_instance"):
                    metadata = _metadata(instance)
                    try:
                        object_id = int(metadata["object_id"])
                        instance_id = int(metadata["instance_id"])
                        if object_id <= 0 or instance_id < 0:
                            raise ValueError("invalid object or instance ID")
                    except (KeyError, ValueError):
                        ctx.safe_report(
                            {"WARNING"}, f"Ignoring an invalid instance on plate {number}."
                        )
                        continue
                    key = (str(object_id), instance_id)
                    if key in ctx.plate_assignments:
                        ctx.safe_report(
                            {"WARNING"},
                            f"Object {object_id} instance {instance_id} has multiple "
                            "plate entries; using the first.",
                        )
                        continue
                    ctx.plate_assignments[key] = PlateAssignment(
                        number, name, offset, assemble_transforms.get(key)
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        ctx.safe_report({"WARNING"}, f"Cannot read plate metadata from {archive_path}: {exc}")
