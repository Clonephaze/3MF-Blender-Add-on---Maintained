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

# <pep8 compliant>

"""
Component and instance detection for 3MF export.

This module provides utilities to detect linked duplicates in Blender (objects sharing
the same mesh data) and organize them for efficient 3MF component export.
"""

from typing import Dict, List
from dataclasses import dataclass

import bpy

from .utilities import debug


@dataclass
class ComponentGroup:
    """
    Represents a group of objects that share the same mesh data.
    
    Attributes:
        mesh_data: The shared mesh data object
        objects: List of Blender objects that reference this mesh
        component_id: Resource ID assigned for the component definition
    """
    mesh_data: bpy.types.Mesh
    objects: List[bpy.types.Object]
    component_id: int = -1


def detect_linked_duplicates(blender_objects: List[bpy.types.Object]) -> Dict[bpy.types.Mesh, ComponentGroup]:
    """
    Detect linked duplicates - objects that share the same mesh data.
    
    In Blender, when you Alt+D duplicate an object, it creates a new object that
    references the same mesh data. This is perfect for 3MF components:
    - Export the mesh once as a component definition
    - Export each object as an instance with just a transform
    
    :param blender_objects: List of Blender objects to analyze
    :return: Dictionary mapping mesh data to ComponentGroup objects
    """
    mesh_to_objects: Dict[bpy.types.Mesh, List[bpy.types.Object]] = {}
    
    for obj in blender_objects:
        if obj.type != 'MESH':
            continue
        
        # Group objects by their mesh data pointer
        mesh_data = obj.data
        if mesh_data not in mesh_to_objects:
            mesh_to_objects[mesh_data] = []
        mesh_to_objects[mesh_data].append(obj)
    
    # Create ComponentGroups for meshes with multiple references
    component_groups: Dict[bpy.types.Mesh, ComponentGroup] = {}
    
    for mesh_data, objects in mesh_to_objects.items():
        # Only create components for meshes with 2+ instances
        # (single instance exports normally as inline mesh)
        if len(objects) >= 2:
            component_groups[mesh_data] = ComponentGroup(
                mesh_data=mesh_data,
                objects=objects
            )
            debug(f"Detected component group: {len(objects)} instances of '{mesh_data.name}'")
    
    return component_groups


def should_use_components(component_groups: Dict[bpy.types.Mesh, ComponentGroup],
                         blender_objects: List[bpy.types.Object]) -> bool:
    """
    Determine if using components would provide significant benefit.
    
    :param component_groups: Detected component groups
    :param blender_objects: All objects being exported
    :return: True if components should be used
    """
    if not component_groups:
        return False
    
    # Calculate potential savings
    total_objects = len([obj for obj in blender_objects if obj.type == 'MESH'])
    instanced_objects = sum(len(group.objects) for group in component_groups.values())
    
    # Use components if >10% of objects are instances
    savings_ratio = instanced_objects / total_objects if total_objects > 0 else 0
    
    debug(f"Component analysis: {instanced_objects}/{total_objects} objects are instances "
          f"({savings_ratio*100:.1f}% savings potential)")
    
    return savings_ratio > 0.1


def get_component_objects(blender_objects: List[bpy.types.Object],
                         component_groups: Dict[bpy.types.Mesh, ComponentGroup]) -> List[bpy.types.Object]:
    """
    Get list of objects that should be exported as component instances.
    
    :param blender_objects: All objects being exported
    :param component_groups: Detected component groups
    :return: List of objects that are component instances
    """
    component_meshes = set(component_groups.keys())
    return [obj for obj in blender_objects if obj.type == 'MESH' and obj.data in component_meshes]


def get_non_component_objects(blender_objects: List[bpy.types.Object],
                              component_groups: Dict[bpy.types.Mesh, ComponentGroup]) -> List[bpy.types.Object]:
    """
    Get list of objects that should be exported normally (not as components).
    
    :param blender_objects: All objects being exported
    :param component_groups: Detected component groups
    :return: List of objects that are NOT component instances
    """
    component_meshes = set(component_groups.keys())
    return [obj for obj in blender_objects if obj.type != 'MESH' or obj.data not in component_meshes]
