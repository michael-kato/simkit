"""Coordinate-frame conventions and conversions.

simkit's canonical frame is ROS REP-103: right-handed, x forward, y left,
z up, meters. Scenarios, frame specs and dataset metadata are always stored
in this frame. Engine adapters convert at their boundary and nowhere else.

Quaternions are (x, y, z, w) everywhere, matching ROS and SciPy.
Rotation matrices are written R and the camera intrinsics matrix K, as in
most computer-vision references.
"""

from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation


@dataclass(frozen=True, eq=False)
class Convention:
    """How an engine's world axes and units relate to the canonical frame.

    ``basis`` is a 3x3 matrix that maps a canonical vector to engine axes:
    ``engine_vector = basis @ ros_vector``. ``scale`` is engine units per meter.
    """

    name: str
    basis: np.ndarray
    scale: float = 1.0

    @property
    def right_handed(self):
        # A negative determinant means one axis is mirrored (left-handed).
        return bool(np.linalg.det(self.basis) > 0)


ROS = Convention("ros", np.eye(3))
# Gazebo/SDF follows ROS exactly.
GAZEBO = Convention("gazebo", np.eye(3))
# Isaac Sim's default stage is Z-up, meters. Check the stage's metersPerUnit
# when loading third-party USD; older assets are often authored in cm.
ISAAC = Convention("isaac", np.eye(3))
# Blender world: right-handed, Z-up, meters (same axes as ROS).
BLENDER = Convention("blender", np.eye(3))
# Unity: left-handed, x right, y up, z forward, meters.
UNITY = Convention("unity", np.array([[0, -1, 0], [0, 0, 1], [1, 0, 0]], dtype=float))
# Unreal: left-handed, x forward, y right, z up, centimeters.
UNREAL = Convention("unreal", np.diag([1.0, -1.0, 1.0]), scale=100.0)

CONVENTIONS = {convention.name: convention for convention in (ROS, GAZEBO, ISAAC, BLENDER, UNITY, UNREAL)}

# glTF asset files (not an engine): the plain Y-up rotation of a Z-up asset,
# gltf = (x, z, -y), as Blender and most DCC tools export them. So a .glb
# matches its sibling .usd point for point.
GLTF = Convention("gltf", np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float))

# Rotation from a ROS camera body frame (x forward, y left, z up) to the ROS
# optical frame (x right, y down, z forward). Columns are optical axes in body.
R_BODY_OPTICAL = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]], dtype=float)
# Rotation from the ROS optical frame to an OpenGL/USD/Blender camera frame
# (x right, y up, looking down -z).
R_OPTICAL_OPENGL = np.diag([1.0, -1.0, -1.0])


# --- canonical <-> engine ----------------------------------------------------

def point_to_engine(point, convention):
    return convention.scale * (convention.basis @ np.asarray(point, dtype=float))


def point_from_engine(point, convention):
    return convention.basis.T @ np.asarray(point, dtype=float) / convention.scale


def rotation_to_engine(R, convention):
    """Convert a 3x3 rotation matrix into engine axes.

    A rotation can't be converted like a point: the axis change has to be
    applied on both sides (B @ R @ B.T), which also handles mirrored axes.
    """
    return convention.basis @ np.asarray(R, dtype=float) @ convention.basis.T


def rotation_from_engine(R, convention):
    return convention.basis.T @ np.asarray(R, dtype=float) @ convention.basis


def pose_to_engine(position, quaternion_xyzw, convention):
    """Canonical pose -> (engine position, engine quaternion [x, y, z, w])."""
    R = rotation_to_engine(Rotation.from_quat(quaternion_xyzw).as_matrix(), convention)
    return point_to_engine(position, convention), Rotation.from_matrix(R).as_quat(canonical=True)


def pose_from_engine(position, quaternion_xyzw, convention):
    """Engine pose -> (canonical position, canonical quaternion [x, y, z, w])."""
    R = rotation_from_engine(Rotation.from_quat(quaternion_xyzw).as_matrix(), convention)
    return point_from_engine(position, convention), Rotation.from_matrix(R).as_quat(canonical=True)


# --- cameras -----------------------------------------------------------------

def intrinsics(width, height, horizontal_fov_deg):
    """Pinhole intrinsics matrix K (3x3), assuming square pixels, the optical
    center at the image center, and no lens distortion."""
    focal_length_px = (width / 2.0) / np.tan(np.radians(horizontal_fov_deg) / 2.0)
    return np.array([
        [focal_length_px, 0, width / 2.0],
        [0, focal_length_px, height / 2.0],
        [0, 0, 1],
    ])


def project(points_world, camera_position, camera_R, K):
    """Project canonical world points into pixel coordinates.

    Args:
        points_world: N x 3 points in the canonical frame.
        camera_position: camera position [x, y, z] in the canonical frame.
        camera_R: 3x3 rotation of the camera *body* frame (x forward), not the optical frame.
        K: 3x3 intrinsics matrix from ``intrinsics()``.

    Returns:
        (uv, depth): uv is N x 2 pixel coordinates (u right, v down); depth is
        N distances along the optical axis. Points behind the camera have depth <= 0.
    """
    points_world = np.atleast_2d(np.asarray(points_world, dtype=float))
    # Row-vector form of R.T @ (point - position): world frame -> camera body frame.
    points_body = (points_world - np.asarray(camera_position)) @ camera_R
    points_optical = points_body @ R_BODY_OPTICAL
    depth = points_optical[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        uv = (points_optical @ K.T)[:, :2] / depth[:, None]
    return uv, depth
