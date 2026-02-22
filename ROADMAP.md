# 🗺️ Development Roadmap

> **3MF Import/Export for Blender** — Future Development Plan

Features and improvements organized by priority. Complexity ratings help with planning but don't determine feasibility — we can tackle hard problems with proper research.

---

## 📊 Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Done |
| 📋 | Planned |
| 💭 | Needs Research |

**Complexity:** `🟢 Easy` `🟡 Medium` `🔴 Hard`

---

## 📦 3MF Extensions

### Triangle Sets Extension
> `http://schemas.microsoft.com/3dmanufacturing/trianglesets/2021/07`

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | Triangle Sets UI Panel | 🟡 | Create/edit sets in Blender (face selection → named groups) |

---

## 🖨️ Slicer Compatibility

### Cura
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | MMU Research Needed | 🔴 | Import/Export of cura MMU Data Needed

---

## 🎨 Blender Integration

### Geometry
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | Sharp Edges | 🟡 | Preserve via edge marks |

---

## 💾 Metadata

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | Vendor Metadata | 🟡 | Preserve vendor metadata on re-export |
| ✅ | Metadata Panel | 🟡 | UI to view/edit 3MF metadata |

---

## 🖥️ User Experience

### Export
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| ✅ | Export Presets | 🟡 | Save/load configurations |
| ✅ | Compression Level | 🟢 | Adjustable ZIP compression |


### UI
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| ✅ | Properties Panel | 🟡 | Sidebar panel for 3MF data |

---

## ⚡ Performance

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | Progress Indicators | 🟢 | Progress bar for long operations |
| 📋 | Large Files | 🟡 | Streaming XML parsing |
| 📋 | Optimize Output | 🟡 | Minimize file size |

---

## 🧪 Testing & Docs

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | User Guide | 🟡 | Usage documentation (Needs Improvement) |
| 📋 | API Documentation | 🟡 | Public API reference (Needs Improvement) |

---

### Research Needed
*Requires investigation*

- [ ] Triangle Sets UI for Custom Assignment
- [ ] Sharp Edge Preservation Method/Options
- [ ] Project Config Preservation/Editing
- [x] Custom Metadata Editing/Adding/Viewing
- [ ] Better Progress Indicators (Blender import/export helpers block main thread)
- [ ] Seam/support painting formats (No idea if we can add this in any way slicers support it)
---

## 🤝 Contributing

Help wanted:
1. **Testing** — Try different slicers, report issues
2. **Research** — Document undocumented slicer formats
3. **Code** — Pick something from the roadmap and PR it

---

*Current version: 2.2.0*
