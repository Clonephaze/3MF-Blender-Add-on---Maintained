"""Helpers for unit tests that need real Blender objects."""

import unittest

import bpy


class BlenderObjectTestCase(unittest.TestCase):
    def make_object(self, name, kind="MESH", parent=None, props=None):
        if kind == "MESH":
            data = bpy.data.meshes.new(name)
            self.addCleanup(bpy.data.meshes.remove, data)
        elif kind == "CAMERA":
            data = bpy.data.cameras.new(name)
            self.addCleanup(bpy.data.cameras.remove, data)
        elif kind == "EMPTY":
            data = None
        else:
            raise ValueError(f"Unsupported test object type: {kind}")
        obj = bpy.data.objects.new(name, data)
        self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
        bpy.context.scene.collection.objects.link(obj)
        if parent is not None:
            obj.parent = parent
        for key, value in (props or {}).items():
            obj[key] = value
        return obj
