# 🗺️ Development Roadmap

> **3MF Import/Export for Blender** — Future Development Plan

Features and improvements organized by priority. Complexity ratings help with planning but don't determine feasibility — we can tackle hard problems with proper research.

---

## 📊 Legend

| Symbol | Meaning |
|--------|---------|
| 📋 | Planned |
| 💭 | Needs Research |

**Complexity:** `🟢 Easy` `🟡 Medium` `🔴 Hard`

---

## 📦 3MF Extensions

### Materials Extension v1.2.1
> `http://schemas.microsoft.com/3dmanufacturing/material/2015/02`

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| ✅ | Full Passthrough Support | 🟡 | All element types preserved for round-trip |
| ✅ | Color Groups | 🟡 | `<colorgroup>` with color arrays |
| ✅ | Texture 2D / 2D Groups | 🔴 | Texture definitions with UV coordinates |
| ✅ | Composite Materials | 🟡 | Material blending definitions |
| ✅ | Multiproperties | 🟡 | Multiple property assignments |
| ✅ | Active PBR Metallic | 🟡 | Metallic/roughness applied to Principled BSDF |
| ✅ | Active PBR Specular | 🟡 | Specular/glossiness applied to Principled BSDF |
| ✅ | Active Translucent | 🟡 | IOR/transmission/attenuation applied to glass materials |
| ✅ | Textured PBR Display | 🔴 | Full node setup with metallic/roughness texture maps |

### Triangle Sets Extension
> `http://schemas.microsoft.com/3dmanufacturing/trianglesets/2021/07`

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| ✅ | Import Triangle Sets | 🟡 | Map to Blender face maps |
| ✅ | Export Triangle Sets | 🟡 | Export face maps as triangle sets |

---

## 🖨️ Slicer Compatibility

### Orca Slicer / BambuStudio
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | Object Settings | 🟡 | Per-object print settings preservation |

### PrusaSlicer / SuperSlicer
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | Object Config | 🟡 | `slic3rpe:` per-object attributes |

### Cura
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | Cura Settings | 🟡 | `cura:` namespace support |

---

## 🎨 Blender Integration

### Materials
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | Alpha/Transparency | 🟢 | RGBA support with blend modes |

### Geometry
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | Sharp Edges | 🟡 | Preserve via edge marks |

### Scene
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | Collections → Components | 🟡 | Map hierarchy to 3MF structure |
| 📋 | Instances | 🟡 | Linked duplicates as component refs |

### Thumbnails
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | Custom Thumbnail | 🟢 | Use custom image file |
| 📋 | Resolution Option | 🟢 | Configurable size |

---

## 💾 Metadata

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | Custom Metadata | 🟡 | Preserve vendor metadata on re-export |
| 📋 | Metadata Panel | 🟡 | UI to view/edit 3MF metadata |
| 📋 | Blender Info | 🟢 | Export Blender version, author |

---

## 🖥️ User Experience

### Export
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | Export Presets | 🟡 | Save/load configurations |
| 📋 | Compression Level | 🟢 | Adjustable ZIP compression |


### UI
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | Properties Panel | 🟡 | Sidebar panel for 3MF data |

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
| 📋 | User Guide | 🟡 | Usage documentation |

---

## 🚀 Priority Tiers

### High Priority
*Core functionality and features*

- [ ] Custom thumbnail option (Camera angles, resolution)

### Medium Priority
*Quality of life improvements*

- [ ] Compression level option

### Lower Priority
*Nice to have*

- [ ] Texture 2D support
- [ ] Cura support
- [ ] Collections → Components
- [ ] Organize Properties Panel
- [ ] Better Progress Indicators

### Research Needed
*Requires investigation before committing*

- [ ] PrusaSlicer Volumetric Paint (Per-vertex paint bucket encoding - requires reverse-engineering proprietary format)
- [ ] Seam/support painting formats (No idea if we can add this in any way slicers support it)
- [ ] Material settings round-trip (Extra material settings, etc, for full re-import)

---

## 🤝 Contributing

Help wanted:
1. **Testing** — Try different slicers, report issues
2. **Research** — Document undocumented slicer formats
3. **Code** — Pick something from the roadmap and PR it

---

*Current version: 1.2.5*
