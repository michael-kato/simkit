"""Unity 6.6 (URP) adapter.

Unity has no in-process Python, so this adapter launches the player built
from unity/ (see unity/Assets/Simkit/Editor/SimkitBuild.cs) and talks to its
SimkitServer over the socket protocol in remote.py. Poses are converted here
(left-handed, Y-up); a converted camera pose is directly a Unity camera pose,
since body +x lands on Unity's +Z (the way cameras look).

The player path comes from $SIMKIT_UNITY_PLAYER (default: unity/Build/simkit.x86_64).
"""

import os

import numpy as np

from ..frames import UNITY
from .base import Capture, EngineAdapter, relative_intensity
from .remote import EngineConnection, decode_buffers, engine_frame

MESH_EXTENSIONS = [".glb", ".gltf"]  # loaded at runtime with glTFast


class UnityAdapter(EngineAdapter):
    name = "unity"
    convention = UNITY

    def setup(self):
        player = os.path.expanduser(os.environ.get("SIMKIT_UNITY_PLAYER", "unity/Build/simkit.x86_64"))
        if not os.path.isfile(player):
            raise FileNotFoundError(f"no Unity player at {player}; build it with unity/Assets/Simkit/Editor/SimkitBuild.cs")
        self._engine = EngineConnection(lambda port: [player, "-batchmode", "-simkitPort", str(port), "-logFile", "-"], self.name)

    def apply(self, frame):
        message = engine_frame(frame, UNITY, MESH_EXTENSIONS, relative_intensity)
        camera = message["camera"]
        half_width = np.tan(np.radians(camera["horizontal_fov_deg"]) / 2)
        camera["vertical_fov_deg"] = float(np.degrees(2 * np.arctan(half_width * camera["height"] / camera["width"])))
        self._engine.call("apply", frame=message)

    def capture(self):
        buffers = decode_buffers(*self._engine.call("capture"))
        depth = buffers["depth"]
        depth[depth <= 0] = np.inf  # cleared background
        return Capture(rgb=buffers["rgb"][..., :3], depth=depth, instance_mask=buffers["instances"].astype(np.int32))

    def close(self):
        self._engine.close()
