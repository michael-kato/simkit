"""NVIDIA Isaac Sim adapter — not yet implemented.

Requires an NVIDIA RTX GPU. Must run inside Isaac Sim's Python
(start a `SimulationApp` before importing omni.* modules).

Implementation plan:
  setup   Open the stage; import the robot with the URDF importer extension.
          Check the stage's metersPerUnit (Isaac's default is meters, Z-up,
          which matches the canonical frame).
  apply   Use omni.replicator.core: create prims from each ObjectInstance.source
          (USD), set their transforms, set the distant light's intensity,
          color temperature and orientation, and pose the camera. USD cameras
          look down -Z: compose the body pose with frames.R_BODY_OPTICAL and
          frames.R_OPTICAL_OPENGL.
  capture Attach annotators "rgb", "distance_to_image_plane" and
          "instance_segmentation"; map their instance ids to
          ObjectInstance.instance_id via each prim's path.
"""

from ..frames import ISAAC
from .base import EngineAdapter


class IsaacAdapter(EngineAdapter):
    name = "isaac"
    convention = ISAAC

    def setup(self):
        raise NotImplementedError(__doc__)

    def apply(self, frame):
        raise NotImplementedError

    def capture(self):
        raise NotImplementedError
