"""Unreal Engine 5 adapter — not yet implemented.

Two workable routes:
  * Editor scripting: run inside the editor's Python (`import unreal`) and
    spawn actors, then render with Movie Render Queue. Simple, slower.
  * Runtime plugin: a C++/Blueprint plugin in a packaged build receives
    FrameSpecs over a socket (or ROS 2 via rclUE) and renders with
    SceneCapture2D components. Faster, more work.

Implementation plan:
  apply   Convert poses with frames.pose_to_engine(..., UNREAL): left-handed,
          X forward / Y right / Z up, centimeters. Convert the rotation
          matrix to an FRotator on the Unreal side.
  capture Final color for RGB; SceneDepth for depth (convert cm -> m);
          instance masks via Custom Depth-Stencil (stencil value =
          instance_id; it is 8-bit, so >255 objects needs a second pass or
          an object-id material).
"""

from ..frames import UNREAL
from .base import EngineAdapter


class UnrealAdapter(EngineAdapter):
    name = "unreal"
    convention = UNREAL

    def setup(self):
        raise NotImplementedError(__doc__)

    def apply(self, frame):
        raise NotImplementedError

    def capture(self):
        raise NotImplementedError
