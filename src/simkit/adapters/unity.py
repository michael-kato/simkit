"""Unity adapter — not yet implemented.

Unity has no in-process Python, so this adapter is a client: Python sends
each FrameSpec to a small C# component inside the Unity player and gets the
capture back.

Implementation plan:
  setup   Launch the Unity build (or connect to the editor). Transport: the
          ROS-TCP-Connector package (a ROS 2 service carrying the FrameSpec as
          JSON) or a plain TCP/JSON socket. Import the robot with Unity's
          URDF Importer package.
  apply   Convert every pose with frames.pose_to_engine(..., UNITY) before
          sending (left-handed, Y-up). Unity cameras look down +Z.
  capture Render RGB, a depth pass and an instance-id pass (a replacement
          shader writing instance_id as color) to RenderTextures and return
          them as PNG/EXR bytes.
"""

from ..frames import UNITY
from .base import EngineAdapter


class UnityAdapter(EngineAdapter):
    name = "unity"
    convention = UNITY

    def setup(self):
        raise NotImplementedError(__doc__)

    def apply(self, frame):
        raise NotImplementedError

    def capture(self):
        raise NotImplementedError
