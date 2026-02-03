# Module Refactoring Guide

This guide documents the process for refactoring large operator files (like `export_3mf.py` or `import_3mf.py`) into a modular structure for better maintainability.

## Overview

The goal is to split a monolithic operator file (~2000+ lines) into 3 smaller, focused modules:

1. **Operator file** (`export_3mf.py` / `import_3mf.py`) - Slim file containing only the Blender operator class with UI properties and `execute()` method
2. **Utils file** (`export_utils.py` / `import_utils.py`) - Shared utility functions used across all format variants
3. **Formats file** (`export_formats.py` / `import_formats.py`) - Format-specific handler classes (Standard, Orca, Prusa, etc.)

## Module Structure

### 1. Operator File (~300-400 lines)
Contains:
- Blender operator class (`Export3MF` / `Import3MF`)
- All `bpy.props` UI properties
- `invoke()`, `execute()`, `draw()` methods
- **Backward-compatible wrapper methods** that delegate to the new modules
- Format dispatcher logic (selecting which format handler to use)

### 2. Utils File (~800-900 lines)
Contains pure functions for:
- Archive operations (`create_archive`, `read_archive`)
- Unit conversion (`unit_scale`)
- XML writing/reading helpers (`write_vertices`, `write_triangles`, `read_vertices`, etc.)
- Material handling (`write_materials`, `read_materials`)
- Transformation formatting/parsing
- Any other reusable logic

**Key principle**: Functions should be stateless where possible, taking all needed data as parameters.

### 3. Formats File (~800-900 lines)
Contains:
- `BaseExporter` / `BaseImporter` - Abstract base class with shared logic
- `StandardExporter` / `StandardImporter` - 3MF Core Spec implementation
- `OrcaExporter` / `OrcaImporter` - Orca Slicer/BambuStudio format
- `PrusaExporter` / `PrusaImporter` - PrusaSlicer format (if applicable)

Each format class:
- Inherits from the base class
- Has reference to operator (`self.op`) for accessing properties
- Implements format-specific logic
- Calls utility functions from the utils module

## Step-by-Step Process

### Step 1: Analyze the Original File
1. Identify all methods in the operator class
2. Categorize each method:
   - **Operator-specific**: UI, invoke, execute, draw → stays in operator file
   - **Utility functions**: Pure functions that don't need `self` → utils file
   - **Format-specific logic**: Writing/reading specific formats → formats file

### Step 2: Create Utils Module
1. Extract stateless utility functions
2. Update function signatures to accept all needed data as parameters
3. Remove `self` references, pass data explicitly
4. Example transformation:
   ```python
   # Before (in operator class):
   def write_vertices(self, mesh_element, vertices):
       decimals = self.coordinate_precision
       ...
   
   # After (in utils module):
   def write_vertices(mesh_element, vertices, use_orca_format, decimals):
       ...
   ```

### Step 3: Create Formats Module
1. Create base class with shared state:
   ```python
   class BaseExporter:
       def __init__(self, operator):
           self.op = operator
           self.next_resource_id = 1
           self.material_resource_id = None
           self.material_name_to_index = {}
   ```
2. Move format-specific methods to appropriate classes
3. Have methods call utility functions from utils module
4. Pass `self.op.property_name` when utility functions need operator properties

### Step 4: Update Operator File
1. Remove moved methods
2. Add imports for new modules
3. Create format dispatcher in `execute()`:
   ```python
   def execute(self, context):
       if self.use_orca_format:
           exporter = OrcaExporter(self)
           return exporter.execute(context)
       else:
           exporter = StandardExporter(self)
           return exporter.execute(context)
   ```
4. **Critical**: Add backward-compatible wrapper methods for unit tests:
   ```python
   def write_vertices(self, mesh_element, vertices):
       """Backward-compatible wrapper for unit tests."""
       from . import export_utils
       return export_utils.write_vertices(
           mesh_element, vertices, 
           self.use_orca_format, self.coordinate_precision)
   ```

### Step 5: Update Unit Tests

This is the most time-consuming part. Unit tests need updates because:
1. Methods moved from operator class to format classes
2. Methods that were instance methods are now module functions
3. Mocking strategies must change

#### Mocking Pattern Changes

**Before** (mocking instance method):
```python
self.exporter.write_object_resource = unittest.mock.MagicMock()
```

**After** (mocking class method with patch.object):
```python
import io_mesh_3mf.export_formats

with unittest.mock.patch.object(
    io_mesh_3mf.export_formats.StandardExporter,
    'write_object_resource',
    unittest.mock.MagicMock(return_value=(1, matrix))
):
    self.exporter.write_objects(...)
```

#### Mock Data Requirements

When utility functions expect Blender-like objects, mocks must match:

**Vertices** - Must have `.co` attribute with **float** tuples:
```python
# Wrong - causes "Precision not allowed in integer format specifier"
vertices = [(1, 2, 3), (4, 5, 6)]

# Correct
mock_vertex1 = unittest.mock.MagicMock(co=(1.0, 2.0, 3.0))
mock_vertex2 = unittest.mock.MagicMock(co=(4.0, 5.0, 6.0))
vertices = [mock_vertex1, mock_vertex2]
```

**Triangles** - Must have `.vertices` and `.material_index`:
```python
mock_triangle = unittest.mock.MagicMock(
    vertices=[0, 1, 2],
    material_index=0
)
```

#### Test File Updates Needed

1. Add import for the formats module at top of test file:
   ```python
   import io_mesh_3mf.export_formats
   ```

2. Update `setUp()` if it creates an exporter instance - may need to set additional properties

3. For each test that mocks methods:
   - Identify if the method is now in utils or formats module
   - Update mocking to use `patch.object()` on the correct class
   - Or update mock data to work with the real utility function

4. For tests comparing formatted output:
   - Use the wrapper method to format expected values
   - Example: `expected = self.exporter.format_transformation(matrix)`

## Common Pitfalls

### 1. Float vs Integer in Format Strings
Python's `f"{value:.9f}"` format specifier requires floats. Mock coordinates must be floats:
```python
co=(1.0, 2.0, 3.0)  # Correct
co=(1, 2, 3)        # Wrong - causes ValueError
```

### 2. Missing Wrapper Methods
Unit tests call methods on the operator instance. If you remove a method without adding a wrapper, tests fail with `AttributeError`.

### 3. State Not Passed to Utils
Utility functions don't have access to `self`. All needed state must be passed as parameters:
```python
# Won't work - utils can't access self.coordinate_precision
write_vertices(mesh_element, vertices)

# Correct - pass all needed values
write_vertices(mesh_element, vertices, use_orca_format, coordinate_precision)
```

### 4. Circular Imports
Be careful with import order. The operator file imports utils and formats, but formats should not import the operator file.

### 5. Test Isolation
When using `patch.object()`, ensure the patch is on the correct class. If tests run in unexpected order, patches from one test might affect another.

## Verification Checklist

After refactoring, verify:

- [ ] All unit tests pass (`python tests/run_all_tests.py`)
- [ ] All integration tests pass
- [ ] Addon loads in Blender without errors
- [ ] Basic import/export still works manually
- [ ] Orca format still works (if applicable)
- [ ] PrusaSlicer format still works (if applicable)
- [ ] No new linter/type errors introduced

## File Sizes (Reference)

After successful refactoring of export:
- `export_3mf.py`: ~375 lines (was ~2123)
- `export_utils.py`: ~868 lines
- `export_formats.py`: ~922 lines

Total is slightly more lines due to:
- Explicit imports
- Docstrings on new module functions
- Wrapper methods for backward compatibility
- Class boilerplate

But the code is now much more maintainable and testable.
