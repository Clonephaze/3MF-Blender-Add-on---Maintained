# Blender add-on to import and export 3MF files.
# Copyright (C) 2020 Ghostkeeper
# Copyright (C) 2025 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
Common utilities shared across import and export.

Re-exports the most frequently used symbols for convenient access::

    from ..common import debug, warn, error
    from ..common import hex_to_rgb, rgb_to_hex, srgb_to_linear
    from ..common import MODEL_NAMESPACE, MATERIAL_NAMESPACE
"""

# Logging
from .logging import DEBUG_MODE, debug, warn, error, safe_report

# Color helpers
from .colors import (
    srgb_to_linear,
    linear_to_srgb,
    hex_to_rgb,
    hex_to_linear_rgb,
    rgb_to_hex,
    linear_rgb_to_hex,
)

# Constants — re-export the most commonly used
from .constants import (
    MODEL_NAMESPACE,
    MODEL_NAMESPACES,
    MODEL_DEFAULT_UNIT,
    MATERIAL_NAMESPACE,
    PRODUCTION_NAMESPACE,
    TRIANGLE_SETS_NAMESPACE,
    BAMBU_NAMESPACE,
    SLIC3RPE_NAMESPACE,
    SUPPORTED_EXTENSIONS,
    CONTENT_TYPES_LOCATION,
    MODEL_LOCATION,
    MODEL_MIMETYPE,
    RELS_MIMETYPE,
    conflicting_mustpreserve_contents,
)

# Units
from .units import blender_to_metre, threemf_to_metre

__all__ = [
    # Logging
    "DEBUG_MODE",
    "debug",
    "warn",
    "error",
    "safe_report",
    # Colors
    "srgb_to_linear",
    "linear_to_srgb",
    "hex_to_rgb",
    "hex_to_linear_rgb",
    "rgb_to_hex",
    "linear_rgb_to_hex",
    # Constants (subset)
    "MODEL_NAMESPACE",
    "MODEL_NAMESPACES",
    "MODEL_DEFAULT_UNIT",
    "MATERIAL_NAMESPACE",
    "PRODUCTION_NAMESPACE",
    "TRIANGLE_SETS_NAMESPACE",
    "BAMBU_NAMESPACE",
    "SLIC3RPE_NAMESPACE",
    "SUPPORTED_EXTENSIONS",
    "CONTENT_TYPES_LOCATION",
    "MODEL_LOCATION",
    "MODEL_MIMETYPE",
    "RELS_MIMETYPE",
    "conflicting_mustpreserve_contents",
    # Units
    "blender_to_metre",
    "threemf_to_metre",
]
