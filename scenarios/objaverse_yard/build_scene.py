"""Build the objaverse_yard test scene with Blender 5.2 (run from the repo root).

    blender -b --python-exit-code 1 -P scenarios/objaverse_yard/build_scene.py -- OBJAVERSE_GLBS POLYHAVEN_SCENES [FRAME_JSON]

1. Normalizes each Objaverse model in MODELS (real-world size, one mesh, origin
   at its bottom center) and exports it as assets/<name>/<name>.usd with a
   sibling <name>.glb for engines that load glTF.
   Props are made opaque with a plausible IOR: some Objaverse glTFs carry
   cutout alpha or an IOR of 1000, which USD renderers show as glass or mirrors.
2. Builds the background yard (floor and walls, Poly Haven textures) the same way.
3. Given FRAME_JSON (a FrameSpec, e.g. frame 0 of scene.yaml), composes
   scene.usda from references to those assets plus the frame's camera and
   lights, then imports it back and renders scene_preview.png with Eevee.

Make FRAME_JSON with:
    python -c "import json; from simkit.scenario import Scenario; print(json.dumps(Scenario.from_yaml('scenarios/objaverse_yard/scene.yaml').sample(0).to_dict()))"
"""

import json
import math
import os
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
REPO = HERE.parents[1]

# name: (file under OBJAVERSE_GLBS, extent to fit, meters)
MODELS = {
    "wooden_pallet": ("000-001/wooden_pallet_002.glb", "length", 1.2),
    "crate_large": ("000-010/wooden_crate_001.glb", "height", 0.8),
    "crate_grey": ("000-011/wooden_crate_002.glb", "height", 0.7),
    "crate_braced": ("000-026/wooden_crate_with_diagonal_bracing_001.glb", "height", 0.6),
    "barrel_hazard": ("000-005/dark_green_hazard_barrel_001.glb", "height", 0.88),
    "barrel_rusty": ("000-029/rusty_green_barrel_001.glb", "height", 0.88),
    "barrel_wooden": ("000-000/wooden_barrel_001.glb", "height", 0.9),
    "forklift_orange": ("000-024/forklift_vehicle_001.glb", "height", 2.1),
    "forklift_rusty": ("000-007/forklift_001.glb", "height", 2.1),
    "traffic_cone": ("000-026/rusty_cone_001.glb", "height", 0.7),
    "fire_extinguisher": ("000-027/fire_extinguisher_003.glb", "height", 0.55),
    "metal_bucket": ("000-010/metal_bucket_with_handles_001.glb", "height", 0.3),
    "recycling_bin": ("000-001/recycling_bin_001.glb", "height", 1.05),
    "step_ladder": ("000-008/step_ladder_001.glb", "height", 1.8),
    "plastic_chair": ("000-005/plastic_chair_001.glb", "height", 0.85),
    "wooden_bucket": ("000-026/wooden_bucket_001.glb", "height", 0.35),
}
# Models whose untextured parts are junk: the pallet carries a white duplicate
# of itself in the same place, plus a stray icosphere.
DROP_UNTEXTURED_PARTS = {"wooden_pallet"}
# Yard boxes as (min corner, max corner, texture under POLYHAVEN_SCENES, meters per texture tile).
FLOOR_TEXTURE = ("hidden_alley/textures/gravel_embedded_concrete_diff_4k.jpg", 4.0)
WALL_TEXTURE = ("hidden_alley/textures/brick_wall_006_diff_2k.jpg", 3.0)
YARD = [
    ((-6.0, -9.0, -0.1), (18.0, 9.0, 0.0), FLOOR_TEXTURE),
    ((13.0, -9.0, 0.0), (13.3, 9.0, 4.0), WALL_TEXTURE),
    ((-6.0, 6.2, 0.0), (13.0, 6.5, 4.0), WALL_TEXTURE),
    ((-6.0, -6.5, 0.0), (13.0, -6.2, 4.0), WALL_TEXTURE),
]
# Camera body (x forward, ROS) to USD/Blender camera (looks down -Z, +Y up):
# simkit.frames.R_BODY_OPTICAL @ R_OPTICAL_OPENGL.
BODY_TO_USD_CAMERA = Matrix(((0, 0, -1), (-1, 0, 0), (0, 1, 0)))
WATTS_PER_LUMEN = 1 / 683  # Blender's photometric conversion


def main(objaverse, polyhaven, frame_json=None):
    for name, (relative_path, extent, size) in MODELS.items():
        clear()
        bpy.ops.import_scene.gltf(filepath=str(Path(objaverse) / relative_path))
        if name in DROP_UNTEXTURED_PARTS:
            drop_untextured_parts()
        obj = join_meshes(name)
        sanitize_materials(obj)
        fit(obj, extent, size)
        export(obj, name)
    clear()
    export(build_yard(polyhaven), "yard")
    if frame_json:
        frame = json.loads(Path(frame_json).read_text())
        compose(frame, HERE / "scene.usda")
        render_preview(HERE / "scene.usda", frame["camera"], HERE / "scene_preview.png")


def clear():
    for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for block in list(collection):
            collection.remove(block)


def drop_untextured_parts():
    for obj in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
        if not any(m and any(n.type == "TEX_IMAGE" for n in m.node_tree.nodes) for m in obj.data.materials):
            bpy.data.objects.remove(obj)


def join_meshes(name):
    """Bake every imported mesh (modifiers, parents) into one world-space object."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    meshes = []
    for obj in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
        mesh.transform(obj.matrix_world)
        if obj.matrix_world.determinant() < 0:
            mesh.flip_normals()
        meshes.append(mesh)
    for obj in list(bpy.context.scene.objects):
        bpy.data.objects.remove(obj)
    parts = [bpy.data.objects.new(f"{name}_{i}", mesh) for i, mesh in enumerate(meshes)]
    for part in parts:
        bpy.context.scene.collection.objects.link(part)
    with bpy.context.temp_override(active_object=parts[0], object=parts[0], selected_objects=parts, selected_editable_objects=parts):
        bpy.ops.object.join()
    obj = parts[0]
    obj.name = obj.data.name = name
    return obj


def sanitize_materials(obj):
    for material in filter(None, obj.data.materials):
        for node in material.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                for link in list(node.inputs["Alpha"].links):
                    material.node_tree.links.remove(link)
                node.inputs["Alpha"].default_value = 1.0
                if not 1.0 <= node.inputs["IOR"].default_value <= 3.0:
                    node.inputs["IOR"].default_value = 1.5


def fit(obj, extent, size):
    """Scale to ``size`` meters along ``extent`` and move the origin to the bottom center."""
    corners = [v.co for v in obj.data.vertices]
    lo = Vector(min(c[i] for c in corners) for i in range(3))
    hi = Vector(max(c[i] for c in corners) for i in range(3))
    dims = hi - lo
    measured = dims.z if extent == "height" else max(dims.x, dims.y)
    scale = size / measured
    bottom_center = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    obj.data.transform(Matrix.Scale(scale, 4) @ Matrix.Translation(-bottom_center))
    print(f"ASSET {obj.name}: {dims.x * scale:.2f} x {dims.y * scale:.2f} x {dims.z * scale:.2f} m, "
          f"{len(obj.data.polygons)} faces, {len(obj.data.materials)} materials")


def export(obj, name):
    folder = ASSETS / name
    folder.mkdir(parents=True, exist_ok=True)
    for other in bpy.context.scene.objects:
        other.select_set(other == obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(filepath=str(folder / f"{name}.glb"), export_format="GLB", use_selection=True, export_apply=True)
    bpy.ops.wm.usd_export(
        filepath=str(folder / f"{name}.usd"), selected_objects_only=True, export_materials=True,
        export_textures_mode="NEW", relative_paths=True, export_lights=False, export_cameras=False,
    )


def build_yard(polyhaven):
    mesh = bpy.data.meshes.new("yard")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    materials = {}
    for lo, hi, (texture, tile) in YARD:
        if texture not in materials:
            materials[texture] = len(materials)
            mesh.materials.append(textured_material(Path(polyhaven) / texture))
        add_box(bm, uv_layer, Vector(lo), Vector(hi), materials[texture], tile)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("yard", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def add_box(bm, uv_layer, lo, hi, material_index, tile):
    """An axis-aligned box with world-space box-mapped UVs, so textures tile at ``tile`` meters."""
    corner = lambda x, y, z: bm.verts.new((hi.x if x else lo.x, hi.y if y else lo.y, hi.z if z else lo.z))
    v = {(x, y, z): corner(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)}
    quads = [  # counter-clockwise seen from outside
        ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
        ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)),
        ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)),
    ]
    for quad in quads:
        face = bm.faces.new([v[c] for c in quad])
        face.material_index = material_index
        face.normal_update()
        axis = max(range(3), key=lambda i: abs(face.normal[i]))
        u_axis, v_axis = [i for i in range(3) if i != axis]
        for loop in face.loops:
            loop[uv_layer].uv = (loop.vert.co[u_axis] / tile, loop.vert.co[v_axis] / tile)


def textured_material(image_path):
    material = bpy.data.materials.new(image_path.stem)
    nodes, links = material.node_tree.nodes, material.node_tree.links
    shader = nodes["Principled BSDF"]
    image = nodes.new("ShaderNodeTexImage")
    image.image = bpy.data.images.load(str(image_path))
    links.new(image.outputs["Color"], shader.inputs["Base Color"])
    shader.inputs["Roughness"].default_value = 0.9
    return material


# --- USD scene ------------------------------------------------------------------

def compose(frame, path):
    """A USD stage that references each placed asset's USD file."""
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    for i, obj in enumerate(frame["objects"]):
        xform = UsdGeom.Xform.Define(stage, f"/World/{obj['asset_name']}_{i}")
        prim = xform.GetPrim()
        prim.GetReferences().AddReference(os.path.relpath(REPO / obj["source"], path.parent))
        set_pose(xform, obj["position"], Quaternion(xyzw_to_wxyz(obj["quaternion_xyzw"])), obj["scale"])
        prim.CreateAttribute("simkit:instance_id", Sdf.ValueTypeNames.Int).Set(obj["instance_id"])
        prim.CreateAttribute("simkit:label", Sdf.ValueTypeNames.String).Set(obj["label"] or "")

    camera = frame["camera"]
    usd_camera = UsdGeom.Camera.Define(stage, "/World/camera")
    rotation = Quaternion(xyzw_to_wxyz(camera["quaternion_xyzw"])).to_matrix() @ BODY_TO_USD_CAMERA
    set_pose(usd_camera, camera["position"], rotation.to_quaternion())
    aperture_mm = 36.0
    usd_camera.CreateHorizontalApertureAttr(aperture_mm)
    usd_camera.CreateVerticalApertureAttr(aperture_mm * camera["height"] / camera["width"])
    usd_camera.CreateFocalLengthAttr(aperture_mm / 2 / math.tan(math.radians(camera["horizontal_fov_deg"]) / 2))
    usd_camera.CreateClippingRangeAttr(Gf.Vec2f(0.05, 200.0))

    for i, light in enumerate(frame["lights"]):
        if light["type"] == "sun":
            usd_light = UsdLux.DistantLight.Define(stage, f"/World/light_{i}")
            usd_light.CreateIntensityAttr(light["intensity"] * WATTS_PER_LUMEN)  # lux -> W/m^2
            # USD lights shine down their -Z axis.
            orientation = Vector((0, 0, -1)).rotation_difference(Vector(light["direction"]))
            set_pose(usd_light, [0, 0, 10], orientation)
        else:
            usd_light = UsdLux.SphereLight.Define(stage, f"/World/light_{i}")
            usd_light.CreateRadiusAttr(0.1)
            usd_light.CreateIntensityAttr(light["intensity"] * WATTS_PER_LUMEN)  # lumens -> W, as Blender reads it
            set_pose(usd_light, light["position"], Quaternion())
        usd_light.CreateEnableColorTemperatureAttr(True)
        usd_light.CreateColorTemperatureAttr(light["color_temperature_k"])
    stage.Save()


def set_pose(xformable, position, quaternion, scale=1.0):
    xformable.AddTranslateOp().Set(Gf.Vec3d(*position))
    xformable.AddOrientOp().Set(Gf.Quatf(quaternion.w, quaternion.x, quaternion.y, quaternion.z))
    xformable.AddScaleOp().Set(Gf.Vec3f(scale, scale, scale))


def xyzw_to_wxyz(q):
    return (q[3], q[0], q[1], q[2])


def render_preview(usd_path, camera, png_path):
    """Import the composed stage into an empty scene and render it from its camera."""
    clear()
    bpy.ops.wm.usd_import(filepath=str(usd_path), mtl_name_collision_mode="MAKE_UNIQUE")
    scene = bpy.context.scene
    scene.camera = next(o for o in scene.objects if o.type == "CAMERA")
    engines = {item.identifier for item in scene.render.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in engines else "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = camera["width"], camera["height"]
    scene.view_settings.view_transform = "Standard"
    world = scene.world or bpy.data.worlds.new("world")
    scene.world = world
    world.color = (0.3, 0.3, 0.3)
    scene.render.filepath = str(png_path)
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main(*sys.argv[sys.argv.index("--") + 1:])
