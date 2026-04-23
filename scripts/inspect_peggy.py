import zipfile

path = r'c:\Users\Jack\Documents\My Projects\Blender Scripts\3MF Format\Blender3mfFormat\ReferenceFiles\PeggyPalette38+Mini+BRYW.3mf'
zf = zipfile.ZipFile(path)
with zf.open('3D/Objects/WhoShrunkPeggyPalette.step_2.model') as f:
    content = f.read().decode('utf-8', 'replace')
zf.close()

# Find object id=1 block
marker = 'id="1"'
start = content.find(marker)
if start == -1:
    print("NOT FOUND")
else:
    # Find the object tag start
    obj_start = content.rfind('<object', 0, start)
    # Find matching </object>
    obj_end = content.find('</object>', obj_start) + len('</object>')
    block = content[obj_start:obj_end]
    print("Object 1 block (first 1000 chars):")
    print(block[:1000])
    print()
    print("Has <mesh>:", '<mesh>' in block or '<mesh ' in block)
    print("Has <components>:", '<components>' in block)
    print("Has <triangles>:", '<triangles>' in block)
