"""simkit's socket server, run inside the Unreal Editor's Python (UE 5.8).

Imported at editor start-up (StartupScripts in Config/DefaultEngine.ini); it
starts serving on the first tick when the editor was launched with
-simkitport=N, as source/adapters/unreal.py does. Serves the protocol in
source/adapters/remote.py to one client from a Slate tick callback (so all
`unreal` calls run on the game thread), then quits the editor. Poses arrive
in Unreal axes and centimeters.

Captures, from SceneCapture2D components sharing one pose:
  rgb        linear scene color (radiance in nits, before post-processing);
             the adapter exposes and encodes it like the other engines
  depth      scene depth (cm along the view axis)
  instances  base color, with every object's materials swapped for a flat
             material whose color encodes its instance id (R low byte, G high byte)

What UE 5.8 does in this setup (editor world, -RenderOffscreen), checked by experiment:
  * a new level comes from a template with a floor, sky and lights;
  * importing a USD or glTF file also places its contents in the level, and
    its textures render as placeholders until their async build finishes;
  * scene captures ignore exposure settings, and their tonemapper keeps a toe
    even with the tone curve off, hence linear scene color for RGB;
  * post-process materials have no effect on scene captures, hence base-color
    ids rather than custom stencil.
"""

import json
import re
import socket
import struct

import unreal

CONTENT_DIR = "/Game/Simkit"
CUBE = "/Engine/BasicShapes/Cube.Cube"  # 100 cm, centered
SPHERE = "/Engine/BasicShapes/Sphere.Sphere"  # 100 cm across
# The sky: a uniform sphere far enough to read as background (past the
# adapter's 10 km depth cutoff), captured by a SkyLight for ambient light.
SKY_RADIUS_CM = 2_000_000
LENGTHS = struct.Struct("<II")
TARGET_FORMATS = {
    "rgb": unreal.TextureRenderTargetFormat.RTF_RGBA16F,
    "depth": unreal.TextureRenderTargetFormat.RTF_RGBA32F,  # raw reads reject single-channel R32F
    "instances": unreal.TextureRenderTargetFormat.RTF_RGBA8,  # linear, so id bytes come back exact
}


class SimkitServer:
    def __init__(self, port):
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).new_level(f"{CONTENT_DIR}/Simkit")
        self.world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        self.actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        template_actors = self.actors.get_all_level_actors()  # the new level comes with a floor, sky and lights
        unreal.log(f"simkit: clearing {[a.get_class().get_name() for a in template_actors]}")
        for actor in template_actors:
            self.actors.destroy_actor(actor)
        self.meshes = {}
        self.objects = []  # (actor, its own materials, its id material) placed this frame
        self.lights = []
        self.size = None
        self.id_material = id_material()
        self.sky_material, self.sky_light = self._sky()
        self.ambient_nits = None
        self.captures = {
            "rgb": self._capture_component(unreal.SceneCaptureSource.SCS_SCENE_COLOR_HDR),
            "depth": self._capture_component(unreal.SceneCaptureSource.SCS_SCENE_DEPTH),
            "instances": self._capture_component(unreal.SceneCaptureSource.SCS_BASE_COLOR),
        }
        self.listener = socket.create_server(("127.0.0.1", port))
        self.listener.setblocking(False)
        self.client = None
        self.buffer = b""
        self.tick_handle = unreal.register_slate_post_tick_callback(self._tick)
        unreal.log(f"simkit: listening on {port}")

    def _tick(self, _delta_s):
        try:
            if self.client is None:
                self.client, _ = self.listener.accept()
                self.client.setblocking(False)
            data = self.client.recv(1 << 20)
            if not data:
                self._disconnect()
                return
            self.buffer += data
        except BlockingIOError:
            pass
        while len(self.buffer) >= LENGTHS.size:
            header_length, payload_length = LENGTHS.unpack_from(self.buffer)
            end = LENGTHS.size + header_length + payload_length
            if len(self.buffer) < end:
                break
            request = json.loads(self.buffer[LENGTHS.size:LENGTHS.size + header_length])
            self.buffer = self.buffer[end:]
            try:
                reply, payload = self._handle(request)
            except Exception as error:  # report every failure to the client
                unreal.log_error(f"simkit: {error!r}")
                reply, payload = {"error": repr(error)}, b""
            body = json.dumps(reply).encode()
            self.client.setblocking(True)
            self.client.sendall(LENGTHS.pack(len(body), len(payload)) + body + payload)
            self.client.setblocking(False)

    def _disconnect(self):
        unreal.unregister_slate_post_tick_callback(self.tick_handle)
        unreal.SystemLibrary.quit_editor()

    def _handle(self, request):
        if request["op"] == "apply":
            self._apply(request["frame"])
            return {}, b""
        if request["op"] == "capture":
            return self._capture()
        raise ValueError(f"unknown op {request['op']!r}")

    # --- scene -----------------------------------------------------------------

    def _apply(self, frame):
        for actor in [actor for actor, _, _ in self.objects] + self.lights:
            self.actors.destroy_actor(actor)
        self.objects = [self._place(obj) for obj in frame["objects"]]
        self.lights = [self._light(light) for light in frame["lights"]]
        for command in ("Editor.AsyncTextureCompilationFinishAll", "Editor.AsyncStaticMeshCompilationFinishAll"):
            unreal.SystemLibrary.execute_console_command(self.world, command)  # imports build these asynchronously

        camera = frame["camera"]
        size = (camera["width"], camera["height"])
        for source, component in self.captures.items():
            component.get_owner().set_actor_transform(transform(camera), False, True)
            component.set_editor_property("fov_angle", camera["horizontal_fov_deg"])
            if size != self.size:
                target = unreal.RenderingLibrary.create_render_target2d(self.world, size[0], size[1], TARGET_FORMATS[source])
                component.set_editor_property("texture_target", target)
        self.size = size
        if frame["ambient_nits"] != self.ambient_nits:
            nits = frame["ambient_nits"]
            self.sky_material.set_vector_parameter_value("Radiance", unreal.LinearColor(nits, nits, nits, 1.0))
            self.sky_light.recapture_sky()  # happens on the next engine tick, before the capture request arrives
            self.ambient_nits = nits

    def _sky(self):
        sphere = self.actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector())
        component = sphere.static_mesh_component
        component.set_static_mesh(unreal.load_asset(SPHERE))
        component.set_cast_shadow(False)  # it encloses the sun
        sphere.set_actor_scale3d(unreal.Vector(*[SKY_RADIUS_CM / 50] * 3))
        material = component.create_dynamic_material_instance(0, sky_material())
        light = self.actors.spawn_actor_from_class(unreal.SkyLight, unreal.Vector()).light_component
        light.set_mobility(unreal.ComponentMobility.MOVABLE)
        light.set_editor_property("source_type", unreal.SkyLightSourceType.SLS_CAPTURED_SCENE)
        light.set_editor_property("lower_hemisphere_is_black", False)  # even fill, as in the other engines
        return material, light

    def _place(self, obj):
        actor = self.actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector())
        component = actor.static_mesh_component
        component.set_mobility(unreal.ComponentMobility.MOVABLE)
        component.set_static_mesh(self._mesh(obj["asset"]))
        actor.set_actor_transform(transform(obj, obj["scale"]), False, True)
        materials = [component.get_material(i) for i in range(component.get_num_materials())]
        id_material = component.create_dynamic_material_instance(0, self.id_material)
        component.set_material(0, materials[0])  # creating it assigned it; swapped in only for the instance capture
        low, high = obj["id"] % 256, obj["id"] // 256
        id_material.set_vector_parameter_value("Id", unreal.LinearColor(low / 255, high / 255, 0.0, 1.0))
        return actor, materials, id_material

    def _light(self, message):
        light_class, units = {
            "sun": (unreal.DirectionalLight, None),  # lux
            "point": (unreal.PointLight, unreal.LightUnits.LUMENS),
            "spot": (unreal.SpotLight, unreal.LightUnits.LUMENS),
            "area": (unreal.RectLight, unreal.LightUnits.LUMENS),
        }[message["type"]]
        actor = self.actors.spawn_actor_from_class(light_class, unreal.Vector())
        actor.set_actor_transform(transform(message), False, True)
        component = actor.light_component
        component.set_mobility(unreal.ComponentMobility.MOVABLE)
        if units is not None:
            component.set_editor_property("intensity_units", units)
        component.set_intensity(message["intensity"])
        component.set_editor_property("use_temperature", True)
        component.set_editor_property("temperature", message["color_temperature_k"])
        if message["type"] == "spot":
            component.set_editor_property("outer_cone_angle", message["cone_angle_deg"] / 2)  # Unreal's is the half-angle
        if message["type"] == "area":
            component.set_editor_property("source_width", message["width"])
            component.set_editor_property("source_height", message["height"])
        return actor

    def _mesh(self, asset):
        if asset == "primitive:cube":
            return unreal.load_asset(CUBE)
        if asset not in self.meshes:
            task = unreal.AssetImportTask()
            task.set_editor_property("filename", asset)
            task.set_editor_property("destination_path", f"{CONTENT_DIR}/Imported/{len(self.meshes)}")
            task.set_editor_property("automated", True)
            task.set_editor_property("save", False)
            before = set(self.actors.get_all_level_actors())
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
            for actor in self.actors.get_all_level_actors():
                if actor not in before:  # the import also placed the file's contents in the level
                    self.actors.destroy_actor(actor)
            meshes = [m for m in map(unreal.load_asset, task.get_editor_property("imported_object_paths")) if isinstance(m, unreal.StaticMesh)]
            if not meshes:
                raise ValueError(f"no static mesh imported from {asset}")
            self.meshes[asset] = meshes[0]
        return self.meshes[asset]

    # --- capture ---------------------------------------------------------------

    def _capture_component(self, source):
        actor = self.actors.spawn_actor_from_class(unreal.SceneCapture2D, unreal.Vector())
        component = actor.capture_component2d
        component.set_editor_property("capture_source", source)
        component.set_editor_property("capture_every_frame", False)
        component.set_editor_property("capture_on_movement", False)
        return component

    def _capture(self):
        width, height = self.size
        self.captures["rgb"].capture_scene()
        self.captures["depth"].capture_scene()
        self._set_materials(lambda materials, id_material: [id_material] * len(materials))
        self.captures["instances"].capture_scene()
        self._set_materials(lambda materials, id_material: materials)
        rgb = self._read("rgb", unreal.RenderingLibrary.read_render_target_raw)
        depth = self._read("depth", unreal.RenderingLibrary.read_render_target_raw)
        ids = self._read("instances", unreal.RenderingLibrary.read_render_target)
        payload = (
            struct.pack(f"<{3 * len(rgb)}f", *(value for color in rgb for value in (color.r, color.g, color.b)))
            + struct.pack(f"<{len(depth)}f", *(sample.r for sample in depth))
            + struct.pack(f"<{len(ids)}H", *(color.r + 256 * color.g for color in ids))
        )
        return {
            "buffers": [
                {"name": "rgb", "dtype": "float32", "shape": [height, width, 3]},
                {"name": "depth", "dtype": "float32", "shape": [height, width]},
                {"name": "instances", "dtype": "uint16", "shape": [height, width]},
            ],
        }, payload

    def _set_materials(self, choose):
        for actor, materials, id_material in self.objects:
            for slot, material in enumerate(choose(materials, id_material)):
                actor.static_mesh_component.set_material(slot, material)

    def _read(self, source, read):
        samples = read(self.world, self.captures[source].texture_target, False)
        if samples is None:
            raise RuntimeError(f"could not read the {source} render target")
        return samples


def transform(message, scale=1.0):
    x, y, z, w = message["rotation"]
    return unreal.Transform(unreal.Vector(*message["position"]), unreal.Quat(x, y, z, w).rotator(), unreal.Vector(scale, scale, scale))


def id_material():
    """A material whose base color is its "Id" vector parameter."""
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset("M_SimkitId", CONTENT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    color = unreal.MaterialEditingLibrary.create_material_expression(material, unreal.MaterialExpressionVectorParameter)
    color.set_editor_property("parameter_name", "Id")
    unreal.MaterialEditingLibrary.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR)
    unreal.MaterialEditingLibrary.recompile_material(material)
    return material


def sky_material():
    """An unlit, two-sided material that glows with its "Radiance" vector parameter, in nits."""
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset("M_SimkitSky", CONTENT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", True)
    radiance = unreal.MaterialEditingLibrary.create_material_expression(material, unreal.MaterialExpressionVectorParameter)
    radiance.set_editor_property("parameter_name", "Radiance")
    unreal.MaterialEditingLibrary.connect_material_property(radiance, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    unreal.MaterialEditingLibrary.recompile_material(material)
    return material


def start_on_first_tick(port):
    def start(_delta_s):
        global server
        unreal.unregister_slate_post_tick_callback(handle)
        server = SimkitServer(port)

    handle = unreal.register_slate_post_tick_callback(start)


port = re.search(r"-simkitport=(\d+)", unreal.SystemLibrary.get_command_line())
if port:
    start_on_first_tick(int(port.group(1)))
