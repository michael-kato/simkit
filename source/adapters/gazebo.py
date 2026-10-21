"""Gazebo Jetty (gz-sim 10) adapter.

Runs a headless `gz sim` server on a generated world and drives it over
gz-transport with the `gz.transport` / `gz.msgs` Python bindings (from
`gz-jetty`). The server stays paused; each frame spawns that frame's objects
and lights, moves the camera, steps the world and waits for the sensor
images stamped at the new sim time.

  RGB + depth   one rgbd_camera sensor (depth is float32 meters along the optical axis)
  instances     a semantic segmentation sensor, with each object's label set
                to its instance id, so the label map is the instance mask (max 255)

Gazebo already uses the ROS convention, and its cameras look down the body +x
axis, so poses pass through unchanged.
"""

import shutil
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from scipy.spatial.transform import Rotation

from ..frames import GAZEBO, GLTF
from .base import AMBIENT, PRIMITIVE_CUBE, Capture, EngineAdapter, launch_engine, relative_intensity, resolve_asset, stop_engine

WORLD = "simkit"
STEP_S = 0.05
STEPS_PER_FRAME = 2  # one to apply spawns and poses, one to render them
STARTUP_TIMEOUT_S = 60
IMAGE_TIMEOUT_S = 30
MESH_EXTENSIONS = [".glb", ".gltf", ".dae", ".obj", ".stl"]
# Gazebo loads glTF without converting it from Y-up; this visual rotation does.
GLTF_TO_ROS_XYZW = Rotation.from_matrix(GLTF.basis.T).as_quat().tolist()
TOPICS = {"rgb": "/simkit/rgbd/image", "depth": "/simkit/rgbd/depth_image", "labels": "/simkit/segmentation/labels_map"}


class GazeboAdapter(EngineAdapter):
    name = "gazebo"
    convention = GAZEBO

    def setup(self):
        from gz.transport import Node

        if shutil.which("gz") is None:
            raise RuntimeError("`gz` not found on PATH; install gz-jetty")
        self._world_dir = tempfile.TemporaryDirectory(prefix="simkit-gazebo-")
        world_path = Path(self._world_dir.name) / "world.sdf"
        world_path.write_text(world_sdf(self.scenario.camera))
        self._server = launch_engine(["gz", "sim", "-s", "--headless-rendering", str(world_path)], self.name)
        self._node = Node()
        self._latest = {}
        self._image_arrived = threading.Condition()
        for key, topic in TOPICS.items():
            self._subscribe(key, topic)
        self._wait_for_server()
        self._spawned = []
        self._steps = 0

    def apply(self, frame):
        if max((obj.instance_id for obj in frame.objects), default=0) > 255:
            raise ValueError("the segmentation label map is 8-bit: at most 255 instances per frame")
        for name, entity_type in self._spawned:
            self._call("remove", entity_msg(name, entity_type))
        prefix = f"simkit_f{frame.index}"
        models = {f"{prefix}_o{i}": object_sdf(f"{prefix}_o{i}", obj) for i, obj in enumerate(frame.objects)}
        lights = {f"{prefix}_l{i}": light_sdf(f"{prefix}_l{i}", light) for i, light in enumerate(frame.lights)}
        self._spawn(models, lights)
        self._spawned = [(name, "MODEL") for name in models] + [(name, "LIGHT") for name in lights]
        self._call("set_pose", pose_msg("simkit_camera", frame.camera.position, frame.camera.quaternion_xyzw))
        self._step(STEPS_PER_FRAME)

    def capture(self):
        target_ns = round(self._steps * STEP_S * 1e9)
        deadline = time.monotonic() + IMAGE_TIMEOUT_S
        with self._image_arrived:
            while not all(key in self._latest and self._latest[key][0] >= target_ns for key in TOPICS):
                if not self._image_arrived.wait(deadline - time.monotonic()):
                    raise TimeoutError(f"no sensor images at sim time {target_ns / 1e9:.2f} s; see {self._server.log_path}")
            images = {key: self._latest[key][1] for key in TOPICS}
        depth = decode_image(images["depth"], np.float32)
        depth[~(depth > 0) | ~np.isfinite(depth)] = np.inf
        return Capture(
            rgb=decode_image(images["rgb"], np.uint8, channels=3),
            depth=depth,
            instance_mask=decode_image(images["labels"], np.uint8, channels=3)[..., 0].astype(np.int32),
        )

    def close(self):
        stop_engine(self._server)
        self._world_dir.cleanup()

    # --- gz-transport ----------------------------------------------------------

    def _subscribe(self, key, topic):
        from gz.msgs.image_pb2 import Image

        def on_image(msg):
            with self._image_arrived:
                self._latest[key] = (msg.header.stamp.sec * 10**9 + msg.header.stamp.nsec, msg)
                self._image_arrived.notify_all()

        if not self._node.subscribe(Image, topic, on_image):
            raise RuntimeError(f"could not subscribe to {topic}")

    def _call(self, service, request, response_type=None, timeout_ms=5000):
        from gz.msgs.boolean_pb2 import Boolean

        ok, response = self._node.request(f"/world/{WORLD}/{service}", request, type(request), response_type or Boolean, timeout_ms)
        if not ok or (response_type is None and not response.data):
            raise RuntimeError(f"gz service /world/{WORLD}/{service} failed; see {self._server.log_path}")
        return response

    def _wait_for_server(self):
        from gz.msgs.world_control_pb2 import WorldControl

        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._server.poll() is not None:
                raise RuntimeError(f"gz sim exited with code {self._server.returncode}; see {self._server.log_path}")
            try:
                self._call("control", WorldControl(pause=True), timeout_ms=1000)
                return
            except RuntimeError:
                time.sleep(0.5)
        raise TimeoutError(f"gz sim did not start within {STARTUP_TIMEOUT_S} s; see {self._server.log_path}")

    def _spawn(self, models, lights):
        from gz.msgs.entity_factory_v_pb2 import EntityFactory_V

        request = EntityFactory_V()
        for sdf in [*models.values(), *lights.values()]:
            request.data.add(sdf=sdf)
        if request.data:
            self._call("create_multiple", request)

    def _step(self, steps):
        from gz.msgs.world_control_pb2 import WorldControl

        self._call("control", WorldControl(pause=True, multi_step=steps))
        self._steps += steps


# --- SDF and messages (pure) --------------------------------------------------

def world_sdf(camera):
    fov_rad = np.radians(camera.horizontal_fov_deg)
    camera_xml = f"""<camera>
            <horizontal_fov>{fov_rad}</horizontal_fov>
            <image><width>{camera.width}</width><height>{camera.height}</height></image>
            <clip><near>0.05</near><far>200</far></clip>
            {{extra}}
          </camera>"""
    return f"""<?xml version="1.0"?>
<sdf version="1.12">
  <world name="{WORLD}">
    <physics name="simkit" type="dart"><max_step_size>{STEP_S}</max_step_size><real_time_factor>0</real_time_factor></physics>
    <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
    <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
    <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
    <scene><ambient>{AMBIENT} {AMBIENT} {AMBIENT} 1</ambient><background>0.6 0.6 0.6 1</background><grid>false</grid></scene>
    <model name="simkit_camera">
      <link name="link">
        <gravity>false</gravity>
        <sensor name="rgbd" type="rgbd_camera">
          <topic>simkit/rgbd</topic>
          <update_rate>{1 / STEP_S}</update_rate>
          <always_on>true</always_on>
          {camera_xml.format(extra="")}
        </sensor>
        <sensor name="segmentation" type="segmentation">
          <topic>simkit/segmentation</topic>
          <update_rate>{1 / STEP_S}</update_rate>
          <always_on>true</always_on>
          {camera_xml.format(extra="<segmentation_type>semantic</segmentation_type>")}
        </sensor>
      </link>
    </model>
  </world>
</sdf>"""


def pose_xml(position, quaternion_xyzw):
    return f'<pose rotation_format="quat_xyzw">{" ".join(map(str, [*position, *quaternion_xyzw]))}</pose>'


def object_sdf(name, obj):
    asset = resolve_asset(obj.source, MESH_EXTENSIONS)
    s = obj.scale
    visual_pose = material = ""
    if asset == PRIMITIVE_CUBE:
        geometry = f"<box><size>{s} {s} {s}</size></box>"
        material = "<material><ambient>0.6 0.6 0.6 1</ambient><diffuse>0.7 0.7 0.7 1</diffuse><specular>0.1 0.1 0.1 1</specular></material>"
    else:
        geometry = f"<mesh><uri>file://{asset}</uri><scale>{s} {s} {s}</scale></mesh>"
        if asset.endswith((".glb", ".gltf")):
            visual_pose = pose_xml([0, 0, 0], GLTF_TO_ROS_XYZW)
    label = ""  # unlabeled visuals are background in the label map
    if obj.instance_id:
        label = f'<plugin filename="gz-sim-label-system" name="gz::sim::systems::Label"><label>{obj.instance_id}</label></plugin>'
    return f"""<sdf version="1.12">
  <model name="{name}">
    <static>true</static>
    {pose_xml(obj.position, obj.quaternion_xyzw)}
    <link name="link"><visual name="visual">
      {visual_pose}
      <geometry>{geometry}</geometry>
      {material}
    </visual></link>
    {label}
  </model>
</sdf>"""


def light_sdf(name, light):
    gz_type = {"sun": "directional", "point": "point", "spot": "spot"}.get(light.type)
    if gz_type is None:
        raise ValueError(f"Gazebo has no {light.type} lights")
    r, g, b = kelvin_to_rgb(light.color_temperature_k)
    position = light.position or [0.0, 0.0, 100.0]
    direction = light.direction or [0.0, 0.0, -1.0]
    spot = ""
    if light.type == "spot":
        spot = f"<spot><inner_angle>0</inner_angle><outer_angle>{np.radians(light.cone_angle_deg)}</outer_angle><falloff>1</falloff></spot>"
    return f"""<sdf version="1.12">
  <light name="{name}" type="{gz_type}">
    {pose_xml(position, [0, 0, 0, 1])}
    <cast_shadows>true</cast_shadows>
    <diffuse>{r} {g} {b} 1</diffuse>
    <specular>{r * 0.2} {g * 0.2} {b * 0.2} 1</specular>
    <intensity>{relative_intensity(light)}</intensity>
    <direction>{" ".join(map(str, direction))}</direction>
    <attenuation><range>1000</range><constant>0</constant><linear>0</linear><quadratic>1</quadratic></attenuation>
    {spot}
  </light>
</sdf>"""


def kelvin_to_rgb(kelvin):
    """Approximate color of a black body at ``kelvin``, as 0-1 RGB (Tanner Helland's fit)."""
    t = np.clip(kelvin, 1000, 40000) / 100
    r = 255 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * np.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255 if t >= 66 else (0 if t <= 19 else 138.5177312231 * np.log(t - 10) - 305.0447927307)
    return tuple(float(np.clip(c, 0, 255) / 255) for c in (r, g, b))


def entity_msg(name, entity_type):
    from gz.msgs.entity_pb2 import Entity

    return Entity(name=name, type=getattr(Entity, entity_type))


def pose_msg(name, position, quaternion_xyzw):
    from gz.msgs.pose_pb2 import Pose

    msg = Pose(name=name)
    msg.position.x, msg.position.y, msg.position.z = position
    msg.orientation.x, msg.orientation.y, msg.orientation.z, msg.orientation.w = quaternion_xyzw
    return msg


def decode_image(msg, dtype, channels=1):
    """gz.msgs.Image -> HxW or HxWxC array, dropping any row padding."""
    rows = np.frombuffer(msg.data, np.uint8).reshape(msg.height, msg.step)
    row_bytes = msg.width * channels * np.dtype(dtype).itemsize
    image = rows[:, :row_bytes].copy().view(dtype)
    return image.reshape(msg.height, msg.width, channels) if channels > 1 else image.reshape(msg.height, msg.width)
