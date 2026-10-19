"""Client for engines that host simkit's server inside their own process (Unity, Unreal).

Wire format, both directions: uint32 header length and uint32 payload length
(little-endian), then a UTF-8 JSON header, then the payload bytes.

  requests  {"op": "apply", "frame": {...}} or {"op": "capture"}, no payload.
  replies   "error" set (non-empty) on failure. A capture reply lists its
            "buffers" ({name, dtype, shape}) in payload order and sets
            "flip_y" when image rows run bottom to top.
"""

import json
import socket
import struct
import time

import numpy as np
from scipy.spatial.transform import Rotation

from ..frames import pose_to_engine
from .base import launch_engine, resolve_asset, stop_engine

LENGTHS = struct.Struct("<II")


class EngineConnection:
    """Launches an engine with ``command_for_port(port)`` and talks to its simkit server."""

    def __init__(self, command_for_port, engine_name, startup_timeout_s=120):
        port = free_port()
        self.process = launch_engine(command_for_port(port), engine_name)
        self.socket = connect(port, self.process, startup_timeout_s)

    def call(self, op, **fields):
        send_message(self.socket, {"op": op, **fields})
        header, payload = receive_message(self.socket)
        if header.get("error"):
            raise RuntimeError(f"engine failed on {op!r}: {header['error']} (log: {self.process.log_path})")
        return header, payload

    def close(self):
        self.socket.close()  # the server quits when its client disconnects
        stop_engine(self.process)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def connect(port, process, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"engine exited with code {process.returncode}; see {process.log_path}")
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=timeout_s)
        except ConnectionRefusedError:
            time.sleep(0.5)
    raise TimeoutError(f"engine did not open port {port} within {timeout_s} s; see {process.log_path}")


def send_message(sock, header, payload=b""):
    body = json.dumps(header).encode()
    sock.sendall(LENGTHS.pack(len(body), len(payload)) + body + payload)


def receive_message(sock):
    header_length, payload_length = LENGTHS.unpack(receive_exactly(sock, LENGTHS.size))
    header = json.loads(receive_exactly(sock, header_length))
    return header, receive_exactly(sock, payload_length)


def receive_exactly(sock, length):
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(min(length - len(data), 1 << 20))
        if not chunk:
            raise ConnectionError("engine closed the connection")
        data += chunk
    return bytes(data)


def decode_buffers(header, payload):
    """Split a capture payload into named arrays, flipped to top-to-bottom rows."""
    buffers, offset = {}, 0
    for spec in header["buffers"]:
        dtype = np.dtype(spec["dtype"])
        count = int(np.prod(spec["shape"]))
        array = np.frombuffer(payload, dtype, count, offset).reshape(spec["shape"])
        buffers[spec["name"]] = array[::-1].copy() if header.get("flip_y") else array.copy()
        offset += count * dtype.itemsize
    return buffers


def engine_frame(frame, convention, asset_extensions, light_intensity):
    """A FrameSpec as an engine-ready dict: poses and lengths in engine axes and
    units, assets resolved to files, light intensity from ``light_intensity(light)``.
    Every rotation keeps its meaning: camera +x looks forward and light +x is
    the direction the light travels, each mapped onto the engine's own axes."""

    def pose(position, quaternion_xyzw):
        engine_position, engine_quaternion = pose_to_engine(position, quaternion_xyzw, convention)
        return {"position": engine_position.tolist(), "rotation": engine_quaternion.tolist()}

    camera = frame.camera
    return {
        "camera": {
            **pose(camera.position, camera.quaternion_xyzw),
            "width": camera.width,
            "height": camera.height,
            "horizontal_fov_deg": camera.horizontal_fov_deg,
        },
        "objects": [
            {
                **pose(obj.position, obj.quaternion_xyzw),
                "id": obj.instance_id,
                "asset": resolve_asset(obj.source, asset_extensions),
                "scale": obj.scale,
            }
            for obj in frame.objects
        ],
        "lights": [
            {
                **pose(light.position or [0.0, 0.0, 0.0], shine_quaternion(light)),
                "type": light.type,
                "intensity": light_intensity(light),
                "color_temperature_k": light.color_temperature_k,
                "cone_angle_deg": light.cone_angle_deg or 0.0,
                "width": (light.width_m or 0.0) * convention.scale,
                "height": (light.height_m or 0.0) * convention.scale,
            }
            for light in frame.lights
        ],
    }


def shine_quaternion(light):
    """Canonical quaternion whose +x axis is the direction the light travels."""
    if light.quaternion_xyzw is not None:
        return light.quaternion_xyzw
    if light.direction is None:  # point lights shine everywhere
        return [0.0, 0.0, 0.0, 1.0]
    rotation, _ = Rotation.align_vectors([light.direction], [[1.0, 0.0, 0.0]])
    return rotation.as_quat().tolist()
