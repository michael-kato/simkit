"""Unreal Engine 5.8 adapter.

Launches the editor headless (-RenderOffscreen) on unreal/Simkit.uproject,
whose start-up script unreal/Content/Python/simkit_server.py serves the socket
protocol in remote.py from inside the editor's Python. Poses are converted here
(left-handed, X forward / Y right / Z up, centimeters); a converted camera
pose is directly a SceneCapture2D pose, since both look down +X. Unreal has
physical light units, so lux and lumens pass through unchanged, and the
camera's manual exposure is set for REFERENCE_LUX like the other engines' intensities.

The editor binary comes from $SIMKIT_UNREAL_EDITOR (path to UnrealEditor).
"""

import math
import os
from pathlib import Path

import numpy as np

from ..frames import UNREAL
from .base import REFERENCE_LUX, Capture, EngineAdapter
from .remote import EngineConnection, decode_buffers, engine_frame

PROJECT = Path(__file__).resolve().parents[2] / "unreal" / "Simkit.uproject"
MESH_EXTENSIONS = [".usd", ".usdc", ".usda", ".glb", ".gltf", ".fbx"]  # imported with Interchange
MAX_DEPTH_M = 10_000.0  # scene depth of the empty background is huge, not infinite


class UnrealAdapter(EngineAdapter):
    name = "unreal"
    convention = UNREAL

    def setup(self):
        editor = os.path.expanduser(os.environ.get("SIMKIT_UNREAL_EDITOR", ""))
        if not os.path.isfile(editor):
            raise FileNotFoundError("set $SIMKIT_UNREAL_EDITOR to the UnrealEditor binary (UE 5.8)")
        command = lambda port: [
            editor, str(PROJECT), f"-simkitport={port}",
            "-RenderOffscreen", "-unattended", "-nosplash", "-nosound", "-stdout", "-FullStdOutLogOutput",
        ]
        self._engine = EngineConnection(command, self.name, startup_timeout_s=900)  # first launch compiles shaders

    def apply(self, frame):
        message = engine_frame(frame, UNREAL, MESH_EXTENSIONS, lambda light: light.intensity)
        message["camera"]["exposure_ev100"] = math.log2(REFERENCE_LUX / 2.5)  # incident-light meter, C = 250
        self._engine.call("apply", frame=message)

    def capture(self):
        buffers = decode_buffers(*self._engine.call("capture"))
        depth = buffers["depth"] / UNREAL.scale
        depth[depth > MAX_DEPTH_M] = np.inf
        instance_mask = buffers["instances"].astype(np.int32)
        instance_mask[np.isinf(depth)] = 0  # the base-color pass reads grey, not zero, where nothing was hit
        return Capture(rgb=buffers["rgb"], depth=depth, instance_mask=instance_mask)

    def close(self):
        self._engine.close()
