"""Quick round-trip smoke test for mixed_filaments parse/serialize.
Run with venv Python (not Blender): python tests/test_roundtrip_mixed.py
"""
import sys, types, importlib.util
from unittest import mock

# --- stub bpy so the module can import ---
bpy_mod = mock.MagicMock()
for name in ("bpy", "bpy.types", "bpy.props"):
    sys.modules[name] = bpy_mod

# --- wire up the logging dep ---
log_spec = importlib.util.spec_from_file_location(
    "io_mesh_3mf.common.logging",
    "io_mesh_3mf/common/logging.py",
)
log_mod = importlib.util.module_from_spec(log_spec)
sys.modules["io_mesh_3mf.common.logging"] = log_mod
log_spec.loader.exec_module(log_mod)

# --- set up the package stub so relative imports resolve ---
pkg = types.ModuleType("io_mesh_3mf.common")
pkg.__path__ = []
for attr in ("debug", "warn", "error"):
    setattr(pkg, attr, getattr(log_mod, attr))
sys.modules["io_mesh_3mf"] = types.ModuleType("io_mesh_3mf")
sys.modules["io_mesh_3mf.common"] = pkg

# --- load mixed_filaments with its full dotted name ---
spec = importlib.util.spec_from_file_location(
    "io_mesh_3mf.common.mixed_filaments",
    "io_mesh_3mf/common/mixed_filaments.py",
)
mod = importlib.util.module_from_spec(spec)
mod.__package__ = "io_mesh_3mf.common"
sys.modules["io_mesh_3mf.common.mixed_filaments"] = mod
spec.loader.exec_module(mod)

parse = mod.parse_mixed_filament_definitions
serialize = mod.serialize_mixed_filament_definitions

PASS = 0
FAIL = 0

def check(label, got, expected):
    global PASS, FAIL
    if got == expected:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}")
        print(f"        expected: {expected!r}")
        print(f"        got:      {got!r}")
        FAIL += 1


# ---------------------------------------------------------------------------
# Round-trip tests — rows from PeggyPalette (no z/xa/xb in source)
# The serializer normalises these by adding z0,xa0.0,xb0.0 (matching latest slicer)
# ---------------------------------------------------------------------------
print("\n=== Round-trip: normalization of old-style rows (no z/xa/xb) ===")
old_row = "1,2,0,0,50,0,g,w,m2,d1,o1,u1"
entries = parse(old_row)
assert len(entries) == 1
mf = entries[0]
check("component_a", mf.component_a, 1)
check("component_b", mf.component_b, 2)
check("enabled", mf.enabled, False)   # deleted forces enabled=False
check("custom", mf.custom, False)
check("mix_b_percent", mf.mix_b_percent, 50)
check("deleted", mf.deleted, True)
check("origin_auto", mf.origin_auto, True)
check("stable_id", mf.stable_id, 1)
check("distribution_mode", mf.distribution_mode, 2)
serialized = serialize(entries)
# Normalised form adds z/xa/xb that were absent in the file
expected_normalised = "1,2,0,0,50,0,g,w,m2,z0,xa0.0,xb0.0,d1,o1,u1"
check("normalised serialization", serialized, expected_normalised)

# ---------------------------------------------------------------------------
# Round-trip tests — rows that already have all fields (should be exact)
# ---------------------------------------------------------------------------
print("\n=== Round-trip: full-field rows (exact match expected) ===")

cases = [
    # test row from the failing harness
    "1,2,1,1,50,1,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u3",
    # full-field row with manual pattern
    "1,2,1,1,33,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u19,123",
    # deleted full-field row
    "2,3,0,0,50,0,g,w,m2,z0,xa0.0,xb0.0,d1,o1,u4",
    # gradient IDs and weights
    "1,3,1,1,30,0,g123,w50/25/25,m0,z0,xa0.0,xb0.0,d0,o0,u99",
    # pattern with perimeter groups
    "1,2,1,1,0,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u44,14343434",
]

for row in cases:
    entries = parse(row)
    got = serialize(entries)
    check(f"exact: {row[:40]}...", got, row)

# ---------------------------------------------------------------------------
# Multi-row round-trip
# ---------------------------------------------------------------------------
print("\n=== Round-trip: multi-row string ===")
multi = (
    "1,2,1,1,50,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u1;"
    "2,3,1,1,30,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u2,123"
)
entries = parse(multi)
check("entry count", len(entries), 2)
got = serialize(entries)
check("multi-row exact", got, multi)

# ---------------------------------------------------------------------------
# Edge: legacy 4-token format
# ---------------------------------------------------------------------------
print("\n=== Legacy 4-token format ===")
legacy = parse("1,2,1,50")
check("legacy parse count", len(legacy), 1)
check("legacy custom defaults True", legacy[0].custom, True)
check("legacy mix_b_percent", legacy[0].mix_b_percent, 50)

# ---------------------------------------------------------------------------
# Edge: invalid rows are skipped
# ---------------------------------------------------------------------------
print("\n=== Invalid row skipping ===")
bad = parse("0,0,1,1,50,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u1;1,2,1,1,50,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u99")
check("bad pair skipped, good kept", len(bad), 1)
check("good entry stable_id", bad[0].stable_id, 99)

print(f"\n{'='*40}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
