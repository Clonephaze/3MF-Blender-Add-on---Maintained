import bpy

print("\n=== OBJECTS IN SCENE ===")
for obj in sorted(bpy.data.objects, key=lambda o: o.name):
    if obj.type != 'MESH':
        continue
    print(f"\nObject: {obj.name}")
    print(f"  Location: {obj.location.x:.4f}, {obj.location.y:.4f}, {obj.location.z:.4f}")
    for i, slot in enumerate(obj.material_slots):
        if not slot.material:
            continue
        m = slot.material
        col = None
        if m.use_nodes:
            for node in m.node_tree.nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    col = node.inputs['Base Color'].default_value
                    break
        hex_col = "?"
        if col:
            r, g, b = int(col[0]*255), int(col[1]*255), int(col[2]*255)
            hex_col = f"#{r:02X}{g:02X}{b:02X}"
        print(f"    Slot {i}: '{m.name}'  hex={hex_col}")
    # Custom properties on mesh data
    mesh = obj.data
    for key in sorted(mesh.keys()):
        if not key.startswith('_'):
            print(f"  Mesh prop '{key}': {repr(mesh[key])}")

print("\n=== MATERIALS ===")
for m in sorted(bpy.data.materials, key=lambda m: m.name):
    col = None
    if m.use_nodes:
        for node in m.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                col = node.inputs['Base Color'].default_value
                break
    hex_col = "?"
    if col:
        r, g, b = int(col[0]*255), int(col[1]*255), int(col[2]*255)
        hex_col = f"#{r:02X}{g:02X}{b:02X}"
    print(f"  '{m.name}'  hex={hex_col}")
