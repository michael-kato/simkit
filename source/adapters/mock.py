"""A dependency-free stand-in engine.

Draws each object as a depth-sorted square at its projected position. It is
deliberately ugly: its job is to exercise the full pipeline (sampling, frame
math, projection, labeling, dataset writing) in CI with no GPU or engine.
Lights are ignored.
"""

import zlib

import numpy as np
from scipy.spatial.transform import Rotation

from ..frames import ROS, intrinsics, project
from .base import Capture, EngineAdapter

OBJECT_SIZE_M = 0.5


class MockAdapter(EngineAdapter):
    name = "mock"
    convention = ROS

    def setup(self):
        self._frame = None

    def apply(self, frame):
        self._frame = frame

    def capture(self):
        frame = self._frame
        if frame is None:
            raise RuntimeError("apply() must be called before capture()")
        camera = frame.camera
        w, h = camera.width, camera.height
        K = intrinsics(w, h, camera.horizontal_fov_deg)

        # Background: a vertical gray gradient.
        gradient = np.linspace(200, 60, h)[:, None, None]
        rgb = np.broadcast_to(gradient, (h, w, 3)).astype(np.uint8).copy()
        depth = np.full((h, w), np.inf, dtype=np.float32)
        instance_mask = np.zeros((h, w), dtype=np.int32)

        if not frame.objects:
            return Capture(rgb=rgb, depth=depth, instance_mask=instance_mask)

        centers = np.array([obj.position for obj in frame.objects])
        camera_R = Rotation.from_quat(camera.quaternion_xyzw).as_matrix()
        uv, z = project(centers, camera.position, camera_R, K)

        # Paint far-to-near so nearer objects cover farther ones.
        for i in np.argsort(-z):
            obj = frame.objects[i]
            if z[i] <= 0.05 or obj.instance_id == 0:  # behind or touching the camera, or background
                continue
            half = 0.5 * K[0, 0] * OBJECT_SIZE_M * obj.scale / z[i]
            left, right = int(max(uv[i, 0] - half, 0)), int(min(uv[i, 0] + half, w))
            top, bottom = int(max(uv[i, 1] - half, 0)), int(min(uv[i, 1] + half, h))
            if left >= right or top >= bottom:  # off screen
                continue
            # Same asset name -> same color, derived from a hash of the name.
            color = np.frombuffer(zlib.crc32(obj.asset_name.encode()).to_bytes(4, "little")[:3], np.uint8)
            rgb[top:bottom, left:right] = color
            depth[top:bottom, left:right] = z[i]
            instance_mask[top:bottom, left:right] = obj.instance_id

        return Capture(rgb=rgb, depth=depth, instance_mask=instance_mask)
