import itertools
import math
import os

import bpy
import mathutils

from bpy.types import Collection, Mesh

from ass.scene.valve import Mdl

from .geometry import get_base_uv
from .materials import placeholder, search
# TODO: _fix material node assembler
# -- for now, props will just use placeholders


# TODO: break down model paths as collections
# foliage > ...
# auto-hide large collections (> 1000 props)


def prop_label(prop_index: int, asset_path: str, origin) -> str:
    x, y, z = origin
    return (
        f"prop_{prop_index:06d} | {asset_path} | "
        f"{x:.2f} {y:.2f} {z:.2f}")


def annotate_prop_object(obj, prop, prop_index: int, asset_path: str):
    solid_type = getattr(prop, "solid_type", getattr(prop, "solid_mode", 0))
    obj["rbsp_type"] = "static_prop"
    obj["rbsp_prop_index"] = prop_index
    obj["rbsp_model_index"] = prop.model_name
    obj["rbsp_model_path"] = asset_path
    obj["rbsp_origin"] = tuple(prop.origin)
    obj["rbsp_angles"] = tuple(prop.angles)
    obj["rbsp_scale"] = prop.scale
    obj["rbsp_solid_type"] = int(solid_type) if isinstance(solid_type, int) else str(solid_type)
    obj["rbsp_flags"] = getattr(prop, "flags", 0)
    obj["rbsp_collision_flags_add"] = getattr(prop, "collision_flags_add", 0)
    obj["rbsp_collision_flags_remove"] = getattr(prop, "collision_flags_remove", 0)
    obj["asset_path"] = asset_path


def as_empties(bsp, prop_collection: Collection):
    """Requires all models to be extracted beforehand"""
    for prop_index, prop in enumerate(bsp.GAME_LUMP.sprp.props):
        path = bsp.GAME_LUMP.sprp.model_names[prop.model_name]
        name = prop_label(prop_index, path, prop.origin)
        empty = bpy.data.objects.new(name, None)
        annotate_prop_object(empty, prop, prop_index, path)
        empty.empty_display_type = "SPHERE"
        empty.empty_display_size = 64
        empty.location = tuple(prop.origin)
        radians = list(map(math.radians, prop.angles))
        empty.rotation_euler = mathutils.Euler(
            (radians[2], radians[0], radians[1]), "YZX")
        empty.scale = (prop.scale,) * 3
        # empty.color = [c / 255 for c in prop.diffuse_modulation]
        # blend (lerp) white (exp0) -> mdl.rgb (exp255)
        r, g, b, exponent = prop.diffuse_modulation
        exponent = exponent / 255
        rgb = [(255 + exponent * (c - 255)) / 255 for c in (r, g, b)]
        empty.color = (*rgb, 1.0)
        prop_collection.objects.link(empty)


def static_props(bsp, prop_collection: Collection, bsp_path: str = None):
    vpk_folder = bpy.context.scene.rbsp_prefs.vpk_folder
    if not os.path.isdir(vpk_folder):
        sibling_models = None
        if bsp_path is not None:
            sibling_models = os.path.abspath(os.path.join(
                os.path.dirname(bsp_path), os.pardir, "models"))
        if sibling_models is not None and os.path.isdir(sibling_models):
            vpk_folder = sibling_models
        else:
            print(
                "io_import_rbsp: VPK/model folder is not set; "
                "importing static props as named empties instead")
            as_empties(bsp, prop_collection)
            return
    meshes = [
        load_model(model_path(vpk_folder, model_name))
        for model_name in bsp.GAME_LUMP.sprp.model_names]
    found_models = sum(
        model_path(vpk_folder, model_name) is not None
        for model_name in bsp.GAME_LUMP.sprp.model_names)
    parsed_models = sum(mesh is not None for mesh in meshes)
    created_objects = 0

    for prop_index, prop in enumerate(bsp.GAME_LUMP.sprp.props):
        mesh = meshes[prop.model_name]
        path = bsp.GAME_LUMP.sprp.model_names[prop.model_name]
        name = prop_label(prop_index, path, prop.origin)
        prop_object = bpy.data.objects.new(name, mesh)
        annotate_prop_object(prop_object, prop, prop_index, path)
        if mesh is None:
            prop_object.empty_display_type = "SPHERE"
            prop_object.empty_display_size = 64
        prop_object.location = tuple(prop.origin)
        radians = list(map(math.radians, prop.angles))
        prop_object.rotation_euler = mathutils.Euler(
            (radians[2], radians[0], radians[1]), "YZX")
        prop_object.scale = (prop.scale,) * 3
        # blend (lerp) white (exp0) -> mdl.rgb (exp255)
        r, g, b, exponent = prop.diffuse_modulation
        exponent = exponent / 255
        rgb = [(255 + exponent * (c - 255)) / 255 for c in (r, g, b)]
        prop_object.color = (*rgb, 1.0)
        prop_collection.objects.link(prop_object)
        created_objects += 1

    print(
        "io_import_rbsp: static props imported: "
        f"{created_objects} objects, {found_models}/{len(meshes)} model files found, "
        f"{parsed_models}/{len(meshes)} model meshes parsed")


def model_path(vpk_folder: str, asset_path: str) -> str:
    asset_path = asset_path.replace("\\", "/")
    candidates = [asset_path]
    if asset_path.lower().startswith("models/"):
        candidates.append(asset_path[7:])
    else:
        candidates.append(f"models/{asset_path}")

    for candidate in candidates:
        filepath = os.path.join(vpk_folder, candidate)
        if os.path.exists(filepath):
            return filepath  # case-sensitive match

    for candidate in candidates:
        filepath = search(vpk_folder, candidate)
        if filepath is not None:
            return filepath
    return None


def load_model(filepath: str) -> Mesh:
    if filepath is None:
        return  # search() FileNotFound
    try:
        mdl = Mdl.from_file(filepath)
        mdl.parse()
    except Exception:
        return None  # failed to parse
    base_name = os.path.splitext(mdl.filename)[0]
    model_name = f"{base_name}.lod0"
    model = mdl.models[model_name]
    # ass.Model -> Blender Mesh
    vertex_materials = [
        (vertex, mesh.material)
        for mesh in model.meshes
        for polygon in mesh.polygons
        for vertex in polygon.vertices]
    if len(vertex_materials) != 0:
        vertices, materials = zip(*vertex_materials)
    else:  # model w/ no geo
        vertices, materials = list(), list()
    assert len(vertices) % 3 == 0, "not a triangle soup"
    indices = list(itertools.chain([
        (i + 2, i + 1, i + 0)
        for i in range(0, len(vertices), 3)]))

    mesh = bpy.data.meshes.new(model_name)
    mesh.from_pydata(
        [vertex.position for vertex in vertices],
        list(),  # auto-generate edges
        indices,
        shade_flat=False)

    # NOTE: uv0 only, no vertex colour
    base_uv = mesh.uv_layers.new(name="base")
    base_uv.data.foreach_set(
        "uv",
        list(itertools.chain(*[
            get_base_uv(vertices, index)
            for tri in indices
            for index in tri])))

    material_indices = dict()
    for material in {sub_mesh.material for sub_mesh in model.meshes}:
        blender_material = placeholder(material.name, "fix")
        mesh.materials.append(blender_material)
        material_indices[material.name] = len(mesh.materials) - 1

    # assign materials
    mesh.polygons.foreach_set(
        "material_index", [
            material_indices[materials[tri[0]].name]
            for tri in indices])

    mesh.update()
    return mesh
