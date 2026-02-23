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


## 🖨️ Slicer Compatibility

### Cura
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | MMU Research Needed | 🔴 | Import/Export of cura MMU Data Needed

---

## 🧪 Testing & Docs

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | User Guide | 🟡 | Usage documentation (Needs Improvement) |
| 📋 | API Documentation | 🟡 | Public API reference (Needs Improvement) |

---

### Research Needed
*Requires investigation*

- [ ] Cura MMU Data: Research how to read/write MMU data from Cura and represent it in Blender and export it back to 3MF. This is a low priority item, cura seems little used in the MMU community, but it would be a nice addition for users who rely on Cura for slicing. Cura stores MMU data in a png texture file using the blue hue. It supports 8 colors, and maps those colors to the first 8 values of blue. so 1/255 blue corresponds to material index 1, 2/255 to index 2, etc. It reads this data for color zones, but another file for actual color data. No seam or support data seems to be stored in the texture file. Cura doesn't seem to have seam data at all. All I've found so far. 
---

## 🤝 Contributing

Help wanted:
1. **Testing** — Try different slicers, report issues
2. **Research** — Document undocumented slicer formats
3. **Code** — Pick something from the roadmap and PR it

---

*Current version: 2.2.1*
