import math
import os
import struct

import bpy
import mathutils
from bpy.types import Collection

from .materials import search


LUMP_PLANES = 0x01
LUMP_VERTICES = 0x03
LUMP_GAME_LUMP = 0x23
LUMP_TRICOLL_TRIANGLES = 0x42
LUMP_TRICOLL_HEADERS = 0x45
LUMP_CM_GRID = 0x55
LUMP_CM_GEO_SETS = 0x57
LUMP_CM_PRIMITIVES = 0x59
LUMP_CM_UNIQUE_CONTENTS = 0x5B
LUMP_CM_BRUSHES = 0x5C
LUMP_CM_BRUSH_SIDE_PLANE_OFFSETS = 0x5D

TYPE_BRUSH = 0x00
TYPE_TRICOLL = 0x40
TYPE_PROP = 0x60

TOOLSCLIP_CONTENTS = {
    0x00020000,
    0x00110000,
    0x00230000,
    0x00230080,
    0x002B0000,
    0x00300000,
    0x00310000,
}

# taken from mrvn-radiant
CONTENTS_SOLID = 0x00000001
CONTENTS_WINDOW = 0x00000002
CONTENTS_AUX = 0x00000004
CONTENTS_GRATE = 0x00000008
CONTENTS_SLIME = 0x00000010
CONTENTS_WATER = 0x00000020
CONTENTS_WINDOW_NOCOLLIDE = 0x00000040
CONTENTS_OPAQUE = 0x00000080
CONTENTS_TESTFOGVOLUME = 0x00000100
CONTENTS_PHYSICSCLIP = 0x00000200
CONTENTS_BLOCKLIGHT = 0x00000400
CONTENTS_NOGRAPPLE = 0x00000800
CONTENTS_UNUSED_03 = 0x00001000
CONTENTS_IGNORE_NODRAW_OPAQUE = 0x00002000
CONTENTS_MOVEABLE = 0x00004000
CONTENTS_TEST_SOLID_BODY_SHOT = 0x00008000
CONTENTS_PLAYERCLIP = 0x00010000
CONTENTS_MONSTERCLIP = 0x00020000
CONTENTS_OPERATOR_FLOOR = 0x00040000
CONTENTS_BLOCKLOS = 0x00080000
CONTENTS_NOCLIMB = 0x00100000
CONTENTS_TITANCLIP = 0x00200000
CONTENTS_BULLETCLIP = 0x00400000
CONTENTS_OPERATORCLIP = 0x00800000
CONTENTS_MONSTER = 0x02000000
CONTENTS_DEBRIS = 0x04000000
CONTENTS_DETAIL = 0x08000000
CONTENTS_TRANSLUCENT = 0x10000000
CONTENTS_HITBOX = 0x40000000

MDL_COLLISION_OFFSET = 460
MDL_STATIC_COLLISION_COUNT = 464
MDL_VTX_OFFSET = 428
INCHES_PER_METER = 39.37007874


def world_collision(bsp_path: str, collection: Collection, navmesh, titan):
    bsp = RawBsp(bsp_path)
    vertices = []
    faces = []

    for primitive in bsp.world_primitives():
        contents = bsp.contents_value(primitive.contents_index)

        if navmesh and not has_contents_that_block(contents, titan):
            continue

        mesh = bsp.tricoll_mesh(primitive.index) if primitive.type == TYPE_TRICOLL else bsp.brush_mesh(primitive.index)
        append_mesh(vertices, faces, mesh)

    if faces:
        make_object("bsp_world_collision", vertices, faces, collection)


def tricoll_collision(bsp_path: str, collection: Collection, navmesh, titan):
    bsp = RawBsp(bsp_path)
    vertices = []
    faces = []

    for primitive in bsp.world_primitives():
        if primitive.type != TYPE_TRICOLL:
            continue

        contents = bsp.contents_value(primitive.contents_index)

        if navmesh and not has_contents_that_block(contents, titan):
            continue

        append_mesh(vertices, faces, bsp.tricoll_mesh(primitive.index))

    if faces:
        obj = make_object("bsp_world_tricoll", vertices, faces, collection)
        obj["rbsp_type"] = "world_tricoll_merged"


def split_world_collision(bsp_path: str, collection: Collection, navmesh, titan) -> int:
    """Import each world brush and tricoll primitive as a named wireframe."""
    bsp = RawBsp(bsp_path)
    brush_collection = child_collection(collection, "world brushes")
    tricoll_collection = child_collection(collection, "world tricoll")
    count = 0

    for primitive in bsp.world_primitives():
        vertices, faces = (
            bsp.brush_mesh(primitive.index)
            if primitive.type == TYPE_BRUSH
            else bsp.tricoll_mesh(primitive.index))
        if not faces:
            continue

        contents = bsp.contents_value(primitive.contents_index)

        if navmesh and not has_contents_that_block(contents, titan):
            continue

        if primitive.type == TYPE_BRUSH:
            name = f"world_brush_{primitive.index:05d}_0x{contents:08X}"
            obj = make_object(name, vertices, faces, brush_collection)
            brush = bsp.read_brush(primitive.index)
            if brush is not None:
                annotate_brush_object(obj, primitive.index, contents, brush, "world_brush")
        else:
            name = f"world_tricoll_{primitive.index:05d}_0x{contents:08X}"
            obj = make_object(name, vertices, faces, tricoll_collection)
            obj["rbsp_type"] = "world_tricoll"
            obj["rbsp_tricoll_index"] = primitive.index
            obj["rbsp_contents"] = f"0x{contents:08X}"
        count += 1

    return count


def split_tricoll_collision(bsp_path: str, collection: Collection, navmesh, titan) -> int:
    """Import each world tricoll primitive as a named wireframe."""
    bsp = RawBsp(bsp_path)
    tricoll_collection = child_collection(collection, "world tricoll")
    count = 0

    for primitive in bsp.world_primitives():
        if primitive.type != TYPE_TRICOLL:
            continue

        vertices, faces = bsp.tricoll_mesh(primitive.index)
        if not faces:
            continue

        contents = bsp.contents_value(primitive.contents_index)

        if navmesh and not has_contents_that_block(contents, titan):
            continue

        name = f"world_tricoll_{primitive.index:05d}_0x{contents:08X}"
        obj = make_object(name, vertices, faces, tricoll_collection)
        obj["rbsp_type"] = "world_tricoll"
        obj["rbsp_tricoll_index"] = primitive.index
        obj["rbsp_contents"] = f"0x{contents:08X}"
        count += 1

    return count


def toolsclip(bsp_path: str, collection: Collection) -> int:
    """Import all known toolsclip brush variants as labeled wireframes."""
    bsp = RawBsp(bsp_path)
    count = 0
    for contents in sorted(TOOLSCLIP_CONTENTS):
        variant_collection = child_collection(collection, f"toolsclip 0x{contents:08X}")
        for primitive in bsp.brush_primitives(contents):
            vertices, faces = bsp.brush_mesh(primitive.index)
            if not faces:
                continue

            brush = bsp.read_brush(primitive.index)
            if brush is None:
                continue

            name = f"toolsclip_{primitive.index:05d}_0x{contents:08X}"
            obj = make_object(name, vertices, faces, variant_collection)
            obj.color = (1.0, 0.08, 0.02, 1.0)
            annotate_brush_object(obj, primitive.index, contents, brush, "toolsclip")
            count += 1
    return count


def child_collection(parent: Collection, name: str) -> Collection:
    for child in parent.children:
        if child.name == name:
            return child
    child = bpy.data.collections.new(name)
    parent.children.link(child)
    return child


def annotate_brush_object(obj, index, contents, brush, kind):
    obj["rbsp_type"] = kind
    obj["rbsp_brush_index"] = index
    obj["rbsp_contents"] = f"0x{contents:08X}"
    obj["rbsp_origin"] = brush["origin"]
    obj["rbsp_extents"] = brush["extents"]
    obj["rbsp_min"] = tuple(
        brush["origin"][axis] - brush["extents"][axis]
        for axis in range(3))
    obj["rbsp_max"] = tuple(
        brush["origin"][axis] + brush["extents"][axis]
        for axis in range(3))
    obj["rbsp_plane_count"] = brush["num_plane_offsets"]


def has_contents_that_block(contents, titan):
    if contents & (CONTENTS_SOLID | CONTENTS_WINDOW | CONTENTS_GRATE):
        return True

    if titan:
        return contents & CONTENTS_TITANCLIP

    # npcs can still move in player clips
    return contents & CONTENTS_PLAYERCLIP

def static_prop_collision(bsp_path: str, collection: Collection):
    vpk_folder = bpy.context.scene.rbsp_prefs.vpk_folder
    if not os.path.isdir(vpk_folder):
        sibling_models = os.path.abspath(os.path.join(
            os.path.dirname(bsp_path), os.pardir, "models"))
        if os.path.isdir(sibling_models):
            vpk_folder = sibling_models
        else:
            print(
                "io_import_rbsp: VPK/model folder is not set; "
                "skipping static prop collision")
            return

    bsp = RawBsp(bsp_path)
    sprp = bsp.parse_sprp()
    prop_indices = bsp.static_prop_collision_indices()
    meshes_by_model = {}

    for prop_index in sorted(prop_indices):
        if prop_index >= len(sprp.props):
            continue

        prop = sprp.props[prop_index]
        solid_type = getattr(prop, "solid_type", getattr(prop, "solid_mode", 0))
        if solid_type == 0:
            continue

        if prop.model not in meshes_by_model:
            model_path = asset_path(vpk_folder, prop.model)
            vertices, faces = load_model_collision(model_path)
            meshes_by_model[prop.model] = make_mesh(os.path.basename(prop.model), vertices, faces) if faces else None

        mesh = meshes_by_model[prop.model]
        if mesh is None:
            continue

        obj = bpy.data.objects.new(
            f"collision_prop_{prop_index:06d} | {prop.model}",
            mesh)
        obj.location = tuple(prop.origin)
        radians = list(map(math.radians, prop.angles))
        obj.rotation_euler = mathutils.Euler((radians[2], radians[0], radians[1]), "YZX")
        obj.scale = (prop.scale,) * 3
        obj["asset_path"] = prop.model
        obj["rbsp_type"] = "static_prop_collision"
        obj["rbsp_prop_index"] = prop_index
        obj["rbsp_model_path"] = prop.model
        obj["rbsp_origin"] = tuple(prop.origin)
        obj["rbsp_angles"] = tuple(prop.angles)
        obj["rbsp_scale"] = prop.scale
        obj["rbsp_solid_type"] = int(solid_type) if isinstance(solid_type, int) else str(solid_type)
        collection.objects.link(obj)


def load_model_collision(model_path: str):
    if model_path is None:
        return [], []

    vertices, faces = load_mdl_static_collision(model_path)
    if faces:
        return vertices, faces

    phy_path = os.path.splitext(model_path)[0] + ".phy"
    if os.path.exists(phy_path):
        return load_phy_collision(phy_path)

    return [], []


def asset_path(vpk_folder: str, asset: str):
    asset = asset.replace("\\", "/")
    candidates = [asset]
    if asset.lower().startswith("models/"):
        candidates.append(asset[7:])
    else:
        candidates.append(f"models/{asset}")

    for candidate in candidates:
        path = os.path.join(vpk_folder, candidate)
        if os.path.exists(path):
            return path

    for candidate in candidates:
        path = search(vpk_folder, candidate)
        if path is not None:
            return path
    return None


def make_mesh(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces, shade_flat=False)
    mesh.update()
    return mesh


def make_object(name, vertices, faces, collection):
    obj = bpy.data.objects.new(name, make_mesh(name, vertices, faces))
    obj.display_type = "WIRE"
    obj.show_in_front = True
    collection.objects.link(obj)
    return obj


def append_mesh(out_vertices, out_faces, mesh):
    vertices, faces = mesh
    if not vertices or not faces:
        return
    base = len(out_vertices)
    out_vertices.extend(vertices)
    out_faces.extend(tuple(base + index for index in face) for face in faces)


def load_mdl_static_collision(model_path: str):
    with open(model_path, "rb") as f:
        data = f.read()

    collision_offset = i32(data, MDL_COLLISION_OFFSET)
    count = i32(data, MDL_STATIC_COLLISION_COUNT)
    vtx_offset = i32(data, MDL_VTX_OFFSET)
    if collision_offset <= 0 or count <= 0 or vtx_offset <= collision_offset:
        return [], []

    vertices = []
    faces = []
    for index in range(count):
        section = read_static_collision_section(data, collision_offset, index)
        base = len(vertices)
        vertices.extend(section["verts"])
        for plane_index in range(section["face_count"]):
            face = face_for_plane(section, plane_index)
            if face:
                faces.append(tuple(base + i for i in face))

    return vertices, faces


def read_static_collision_section(data, collision_offset, index):
    header = collision_offset + index * 20
    face_count = i32(data, header)
    plane_count = i32(data, header + 4)
    edge_count = i32(data, header + 8)
    vert_count = i32(data, header + 12)
    data_offset = i32(data, header + 16)
    data_start = header + data_offset
    edge_start = data_start + plane_count * 16
    vert_start = edge_start + edge_count * 28

    planes = []
    for i in range(plane_count):
        off = data_start + i * 16
        planes.append({
            "normal": (f32(data, off), f32(data, off + 4), f32(data, off + 8)),
            "distance": f32(data, off + 12),
        })

    verts = []
    for i in range(vert_count):
        off = vert_start + i * 12
        verts.append((f32(data, off), f32(data, off + 4), f32(data, off + 8)))

    return {
        "face_count": face_count,
        "planes": planes,
        "verts": verts,
    }


def face_for_plane(section, plane_index):
    if plane_index >= len(section["planes"]):
        return None

    plane = section["planes"][plane_index]
    points = [
        (i, vertex)
        for i, vertex in enumerate(section["verts"])
        if abs(dot(plane["normal"], vertex) - plane["distance"]) <= 0.08
    ]
    if len(points) < 3:
        return None

    center = tuple(sum(point[1][axis] for point in points) / len(points) for axis in range(3))
    normal = normalize(plane["normal"])
    seed = (0, 0, 1) if abs(normal[2]) < 0.9 else (0, 1, 0)
    u_axis = normalize(cross(seed, normal))
    v_axis = cross(normal, u_axis)

    return [
        point[0]
        for point in sorted(
            points,
            key=lambda point: math.atan2(
                dot(sub(point[1], center), v_axis),
                dot(sub(point[1], center), u_axis),
            ),
        )
    ]


def load_phy_collision(phy_path: str):
    with open(phy_path, "rb") as f:
        data = f.read()

    vertices = []
    faces = []
    for ledge in collect_phy_ledges(data):
        raw_tris = []
        used = {}
        valid = True

        for i in range(ledge["tri_count"]):
            tri = decode_phy_triangle(data, ledge["off"] + 16 + i * 16)
            raw_tris.append(tri)
            for vertex in tri:
                if vertex not in used:
                    used[vertex] = len(used)

        ledge_vertices = [None] * len(used)
        for old_index, new_index in used.items():
            point_off = ledge["point_start"] + old_index * 16
            if point_off + 16 > len(data):
                valid = False
                break
            ledge_vertices[new_index] = read_phy_point(data, point_off)

        if not valid or len(ledge_vertices) < 3:
            continue

        base = len(vertices)
        vertices.extend(ledge_vertices)
        for tri in raw_tris:
            face = tuple(base + used[index] for index in tri if index in used)
            if len(face) == 3:
                faces.append(face)

    return vertices, faces


def collect_phy_ledges(data):
    if len(data) < 4:
        return []

    surface = i32(data, 0)
    if surface <= 0 or surface + 84 > len(data):
        return []

    legacy = surface + 32
    root = legacy + i32(data, legacy + 32)
    end = surface + i32(data, surface) + 4
    nodes = set()
    ledges = {}

    def add_ledge(off):
        if off < surface + 80 or off + 16 >= end or off in ledges:
            return
        point_offset = i32(data, off)
        tri_count = i16(data, off + 12)
        point_start = off + point_offset
        if point_offset <= 0 or tri_count <= 0 or tri_count > 8192:
            return
        if point_start <= off or point_start >= end or off + 16 + tri_count * 16 > point_start:
            return
        ledges[off] = {"off": off, "point_start": point_start, "tri_count": tri_count}

    def visit_node(off):
        if off < surface or off + 24 > end or off in nodes or len(nodes) > 4096:
            return
        nodes.add(off)
        right = i32(data, off)
        ledge_offset = i32(data, off + 4)
        if ledge_offset:
            add_ledge(off + ledge_offset)
        if -0x100000 < right < 0x100000 and right != 0:
            visit_node(off + 24)
            visit_node(off + right)

    visit_node(root)
    for off in range(surface + 80, end - 16, 16):
        add_ledge(off)
    return [ledges[key] for key in sorted(ledges)]


def read_phy_point(data, off):
    point = (f32(data, off), f32(data, off + 4), f32(data, off + 8))
    return (
        point[0] * INCHES_PER_METER,
        point[2] * INCHES_PER_METER,
        -point[1] * INCHES_PER_METER,
    )


def decode_phy_triangle(data, off):
    return (
        u32(data, off + 4) & 0xFFFF,
        u32(data, off + 8) & 0xFFFF,
        u32(data, off + 12) & 0xFFFF,
    )


class RawBsp:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        self.lumps = [self._read_lump(i) for i in range(128)]
        self.contents = self.read_contents()
        self.first_brush_plane = i32(self.data, self.lump(LUMP_CM_GRID).offset + 0x18) if self.lump(LUMP_CM_GRID).length >= 0x1C else 0

    def _read_lump(self, index):
        off = 0x10 + index * 16
        return Lump(
            offset=u32(self.data, off),
            length=u32(self.data, off + 4),
            version=u32(self.data, off + 8),
        )

    def lump(self, index):
        return self.lumps[index]

    def read_contents(self):
        lump = self.lump(LUMP_CM_UNIQUE_CONTENTS)
        return [u32(self.data, off) for off in range(lump.offset, lump.offset + lump.length, 4)]

    def world_primitives(self):
        refs = {}

        def add(raw):
            primitive = decode_primitive(raw)
            if primitive.type not in (TYPE_BRUSH, TYPE_TRICOLL):
                return
            refs[(primitive.type, primitive.index)] = primitive

        geo_sets = self.lump(LUMP_CM_GEO_SETS)
        primitives = self.lump(LUMP_CM_PRIMITIVES)
        primitive_count = primitives.length // 4
        for g in range(geo_sets.length // 8):
            off = geo_sets.offset + g * 8
            num_primitives = u16(self.data, off + 2)
            raw = u32(self.data, off + 4)
            if num_primitives == 1:
                add(raw)
                continue
            parent = decode_primitive(raw)
            for p in range(parent.index, min(parent.index + num_primitives, primitive_count)):
                add(u32(self.data, primitives.offset + p * 4))

        return refs.values()

    def brush_primitives(self, contents_value):
        refs = {}

        def add(raw):
            primitive = decode_primitive(raw)
            if primitive.type != TYPE_BRUSH:
                return
            if primitive.contents_index >= len(self.contents):
                return
            if self.contents[primitive.contents_index] != contents_value:
                return
            refs[primitive.index] = primitive

        geo_sets = self.lump(LUMP_CM_GEO_SETS)
        primitives = self.lump(LUMP_CM_PRIMITIVES)
        primitive_count = primitives.length // 4
        for g in range(geo_sets.length // 8):
            off = geo_sets.offset + g * 8
            num_primitives = u16(self.data, off + 2)
            raw = u32(self.data, off + 4)
            if num_primitives == 1:
                add(raw)
                continue
            parent = decode_primitive(raw)
            for p in range(parent.index, min(parent.index + num_primitives, primitive_count)):
                add(u32(self.data, primitives.offset + p * 4))

        return refs.values()

    def static_prop_collision_indices(self):
        out = set()

        def add(raw):
            primitive = decode_primitive(raw)
            if primitive.type == TYPE_PROP:
                out.add(primitive.index)

        geo_sets = self.lump(LUMP_CM_GEO_SETS)
        primitives = self.lump(LUMP_CM_PRIMITIVES)
        primitive_count = primitives.length // 4
        for g in range(geo_sets.length // 8):
            off = geo_sets.offset + g * 8
            num_primitives = u16(self.data, off + 2)
            raw = u32(self.data, off + 4)
            if num_primitives == 1:
                add(raw)
                continue
            parent = decode_primitive(raw)
            for p in range(parent.index, min(parent.index + num_primitives, primitive_count)):
                add(u32(self.data, primitives.offset + p * 4))

        return out

    def contents_value(self, index):
        return self.contents[index] if 0 <= index < len(self.contents) else 0

    def parse_sprp(self):
        game_lump = self.lump(LUMP_GAME_LUMP)
        count = u32(self.data, game_lump.offset)
        for i in range(count):
            header = game_lump.offset + 4 + i * 16
            ident = u32(self.data, header)
            text = bytes((
                ident & 0xFF,
                (ident >> 8) & 0xFF,
                (ident >> 16) & 0xFF,
                (ident >> 24) & 0xFF,
            )).decode("ascii", errors="ignore")[::-1]
            if text != "sprp":
                continue

            version = u16(self.data, header + 6)
            body = u32(self.data, header + 8)
            model_count = u32(self.data, body)
            p = body + 4
            models = []
            for _ in range(model_count):
                models.append(cstring(self.data, p, 128).replace("\\", "/"))
                p += 128
            if version < 13:
                leaf_count = u32(self.data, p)
                p += 4 + leaf_count * 2
            prop_count = u32(self.data, p)
            p += 12
            prop_size = 0x40 if version >= 13 else 0x54
            props = []
            for index in range(prop_count):
                model_index = u16(self.data, p + (0x1C if version >= 13 else 0x18))
                props.append(StaticProp(
                    index=index,
                    origin=(f32(self.data, p), f32(self.data, p + 4), f32(self.data, p + 8)),
                    angles=(f32(self.data, p + 12), f32(self.data, p + 16), f32(self.data, p + 20)),
                    scale=f32(self.data, p + 24) if version >= 13 else f32(self.data, p + 0x48),
                    model=models[model_index] if model_index < len(models) else "",
                    solid_type=u8(self.data, p + 0x1E),
                ))
                p += prop_size
            return Sprp(version=version, models=models, props=props)
        return Sprp(version=0, models=[], props=[])

    def tricoll_mesh(self, index):
        headers = self.lump(LUMP_TRICOLL_HEADERS)
        triangles = self.lump(LUMP_TRICOLL_TRIANGLES)
        vertices = self.lump(LUMP_VERTICES)
        header_count = headers.length // 0x2C
        vertex_count = vertices.length // 12
        if index < 0 or index >= header_count:
            return [], []

        off = headers.offset + index * 0x2C
        num_vertices = i16(self.data, off + 6)
        num_triangles = u16(self.data, off + 8)
        first_vertex = i32(self.data, off + 0x0C)
        first_triangle = u32(self.data, off + 0x10)
        out_vertices = []
        faces = []

        for t in range(num_triangles):
            tri_offset = triangles.offset + (first_triangle + t) * 4
            if tri_offset + 4 > triangles.offset + triangles.length:
                continue
            tri = decode_tricoll_triangle(u32(self.data, tri_offset))
            face = []
            for local_index in tri:
                if local_index < 0 or local_index >= num_vertices:
                    break
                vertex_index = first_vertex + local_index
                if vertex_index < 0 or vertex_index >= vertex_count:
                    break
                face.append(len(out_vertices))
                out_vertices.append(vec3(self.data, vertices.offset + vertex_index * 12))
            if len(face) == 3:
                faces.append(tuple(face))

        return out_vertices, faces

    def brush_mesh(self, index):
        brush = self.read_brush(index)
        if brush is None:
            return [], []

        planes = self.brush_planes(brush)
        vertices = []
        faces = []
        for i, plane in enumerate(planes):
            points = []
            for j, second in enumerate(planes):
                if j == i:
                    continue
                for k in range(j + 1, len(planes)):
                    if k == i:
                        continue
                    point = plane_intersection(plane, second, planes[k])
                    if point and inside_all_planes(point, planes):
                        add_unique(points, point)
            if len(points) < 3:
                continue
            sorted_points = sort_face(points, normalize(plane["normal"]))
            face = []
            for point in sorted_points:
                face.append(len(vertices))
                vertices.append(point)
            faces.append(tuple(face))
        return vertices, faces

    def read_brush(self, index):
        brushes = self.lump(LUMP_CM_BRUSHES)
        brush_count = brushes.length // 0x20
        if index < 0 or index >= brush_count:
            return None
        off = brushes.offset + index * 0x20
        return {
            "origin": vec3(self.data, off),
            "num_plane_offsets": u8(self.data, off + 0x0D),
            "extents": vec3(self.data, off + 0x10),
            "brush_side_offset": i32(self.data, off + 0x1C),
        }

    def brush_planes(self, brush):
        origin = brush["origin"]
        extents = brush["extents"]
        min_point = tuple(origin[i] - extents[i] for i in range(3))
        max_point = tuple(origin[i] + extents[i] for i in range(3))
        planes = [
            {"normal": (1, 0, 0), "distance": max_point[0]},
            {"normal": (-1, 0, 0), "distance": -min_point[0]},
            {"normal": (0, 1, 0), "distance": max_point[1]},
            {"normal": (0, -1, 0), "distance": -min_point[1]},
            {"normal": (0, 0, 1), "distance": max_point[2]},
            {"normal": (0, 0, -1), "distance": -min_point[2]},
        ]

        plane_offsets = self.lump(LUMP_CM_BRUSH_SIDE_PLANE_OFFSETS)
        plane_offset_count = plane_offsets.length // 2
        for i in range(brush["num_plane_offsets"]):
            offset = brush["brush_side_offset"] + i
            if offset < 0 or offset >= plane_offset_count:
                continue
            brush_plane_offset = offset - u16(self.data, plane_offsets.offset + offset * 2)
            plane = self.read_plane(self.first_brush_plane + brush_plane_offset)
            if plane:
                planes.append(plane)
        return planes

    def read_plane(self, index):
        planes = self.lump(LUMP_PLANES)
        plane_count = planes.length // 16
        if index < 0 or index >= plane_count:
            return None
        off = planes.offset + index * 16
        return {
            "normal": vec3(self.data, off),
            "distance": f32(self.data, off + 12),
        }


class Lump:
    def __init__(self, offset, length, version):
        self.offset = offset
        self.length = length
        self.version = version


class Primitive:
    def __init__(self, raw):
        self.raw = raw
        self.type = raw >> 24
        self.index = (raw >> 8) & 0xFFFF
        self.contents_index = raw & 0xFF


class Sprp:
    def __init__(self, version, models, props):
        self.version = version
        self.models = models
        self.props = props


class StaticProp:
    def __init__(self, index, origin, angles, scale, model, solid_type):
        self.index = index
        self.origin = origin
        self.angles = angles
        self.scale = scale
        self.model = model
        self.solid_type = solid_type


def decode_primitive(raw):
    return Primitive(raw)


def decode_tricoll_triangle(raw):
    a = raw & 0x3FF
    b = (raw >> 10) & 0x7F
    c = (raw >> 17) & 0x7F
    return a, a + b, a + c


def cstring(data, off, limit):
    end = off
    while end < off + limit and end < len(data) and data[end] != 0:
        end += 1
    return data[off:end].decode("utf8", errors="ignore")


def u8(data, off):
    return data[off]


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def i16(data, off):
    return struct.unpack_from("<h", data, off)[0]


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def i32(data, off):
    return struct.unpack_from("<i", data, off)[0]


def f32(data, off):
    return struct.unpack_from("<f", data, off)[0]


def vec3(data, off):
    return f32(data, off), f32(data, off + 4), f32(data, off + 8)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sub(a, b):
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def add(a, b):
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def mul(a, scalar):
    return a[0] * scalar, a[1] * scalar, a[2] * scalar


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def normalize(value):
    length = math.sqrt(dot(value, value))
    return mul(value, 1 / length) if length > 1e-9 else (0, 0, 0)


def plane_intersection(a, b, c):
    n1 = a["normal"]
    n2 = b["normal"]
    n3 = c["normal"]
    n2xn3 = cross(n2, n3)
    n3xn1 = cross(n3, n1)
    n1xn2 = cross(n1, n2)
    denom = dot(n1, n2xn3)
    if abs(denom) < 1e-7:
        return None
    return mul(add(add(mul(n2xn3, a["distance"]), mul(n3xn1, b["distance"])), mul(n1xn2, c["distance"])), 1 / denom)


def inside_all_planes(point, planes):
    return all(dot(plane["normal"], point) <= plane["distance"] + 0.05 for plane in planes)


def same_point(a, b):
    return abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01 and abs(a[2] - b[2]) < 0.01


def add_unique(points, point):
    if not any(same_point(existing, point) for existing in points):
        points.append(point)


def sort_face(points, normal):
    center = tuple(sum(point[axis] for point in points) / len(points) for axis in range(3))
    helper = (0, 0, 1) if abs(normal[2]) < 0.9 else (0, 1, 0)
    u_axis = normalize(cross(helper, normal))
    v_axis = normalize(cross(normal, u_axis))
    return sorted(points, key=lambda point: math.atan2(dot(sub(point, center), v_axis), dot(sub(point, center), u_axis)))
