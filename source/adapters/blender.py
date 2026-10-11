"""Blender / BlenderProc adapter — not yet implemented.

Open-loop image generation only (no physics stepping or ROS). Cycles renders
on NVIDIA (CUDA/OptiX), AMD (HIP) and CPU.

Implementation plan:
  setup   Run under `blenderproc run` (or Blender's bundled Python). Load
          static scene and assets (USD import, or glTF siblings).
  apply   World axes match the canonical frame. Blender cameras look down -Z:
          compose the body pose with frames.R_BODY_OPTICAL and
          frames.R_OPTICAL_OPENGL. Set sun strength/angle and color temperature
          (via a Blackbody node).
  capture Enable depth and segmentation outputs (BlenderProc:
          `bproc.renderer.enable_depth_output`, `enable_segmentation_output`
          with the instance attribute set to instance_id).
"""

from ..frames import BLENDER
from .base import EngineAdapter


class BlenderAdapter(EngineAdapter):
    name = "blender"
    convention = BLENDER

    def setup(self):
        raise NotImplementedError(__doc__)

    def apply(self, frame):
        raise NotImplementedError

    def capture(self):
        raise NotImplementedError
