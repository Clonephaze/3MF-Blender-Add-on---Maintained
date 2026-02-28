# Public API Documentation

Full documentation lives in [docs/site/](docs/site/index.html) — open `docs/site/index.html` in a browser.

If you change the API or docstrings, rebuild with:

```powershell
docs/build.ps1          # or: docs/build.ps1 -Clean
```

The docs include:

- **Getting Started** — overview, quick start, export format reference, callbacks, CLI usage
- **Recipes** — ready-to-use patterns (import, export, round-trip, batch, inspect)
- **Core API Reference** — auto-generated parameter docs for `import_3mf`, `export_3mf`, `inspect_3mf`, and batch helpers
- **API Discovery** — version checking, capability detection, standalone helper module
- **Building Blocks** — colors, units, types, segmentation, extensions, metadata

### Quick example

```python
from io_mesh_3mf.api import import_3mf, export_3mf, inspect_3mf

result = import_3mf("model.3mf")
result = export_3mf("output.3mf", use_selection=True)
info   = inspect_3mf("model.3mf")
```
