"""Gazebo (Harmonic, paired with ROS 2 Jazzy) adapter — not yet implemented.

Implementation plan:
  setup   Launch `gz sim <world>.sdf` with the robot from robot/robot.urdf
          (convert with `gz sdf -p robot.urdf > robot.sdf`). Bridge sensor
          topics to ROS 2 with `ros_gz_bridge` per interfaces/ros2_contract.yaml.
  apply   Spawn/remove entities via the /world/<name>/create and /remove
          services, move them with /world/<name>/set_pose, and update the
          sun via the light config service. Convention: identical to ROS.
  capture Subscribe to the bridged camera, depth camera and segmentation
          camera sensor (Gazebo's `segmentation` sensor gives instance masks;
          map its label ids back to ObjectInstance.instance_id).
"""

from ..frames import GAZEBO
from .base import EngineAdapter


class GazeboAdapter(EngineAdapter):
    name = "gazebo"
    convention = GAZEBO

    def setup(self):
        raise NotImplementedError(__doc__)

    def apply(self, frame):
        raise NotImplementedError

    def capture(self):
        raise NotImplementedError
