import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from simkit import frames

rng = np.random.default_rng(0)
ALL_CONVENTIONS = list(frames.CONVENTIONS.values())


def random_quaternion():
    quaternion = rng.normal(size=4)
    return quaternion / np.linalg.norm(quaternion)


def to_matrix(quaternion_xyzw):
    return Rotation.from_quat(quaternion_xyzw).as_matrix()


def same_rotation(quaternion_a, quaternion_b):
    # q and -q are the same rotation, so compare matrices instead of quaternions.
    return np.allclose(to_matrix(quaternion_a), to_matrix(quaternion_b), atol=1e-9)


@pytest.mark.parametrize("convention", ALL_CONVENTIONS, ids=lambda convention: convention.name)
def test_pose_round_trip(convention):
    for _ in range(50):
        position, rotation = rng.normal(size=3), random_quaternion()
        engine_position, engine_rotation = frames.pose_to_engine(position, rotation, convention)
        back_position, back_rotation = frames.pose_from_engine(engine_position, engine_rotation, convention)
        assert np.allclose(position, back_position)
        assert same_rotation(rotation, back_rotation)


@pytest.mark.parametrize("convention", ALL_CONVENTIONS, ids=lambda convention: convention.name)
def test_rotating_then_converting_commutes(convention):
    # Rotating a vector in ROS then converting must equal converting then rotating in the engine.
    for _ in range(50):
        vector, rotation = rng.normal(size=3), random_quaternion()
        _, engine_rotation = frames.pose_to_engine(np.zeros(3), rotation, convention)
        rotate_then_convert = frames.point_to_engine(to_matrix(rotation) @ vector, convention)
        convert_then_rotate = to_matrix(engine_rotation) @ frames.point_to_engine(vector, convention)
        assert np.allclose(rotate_then_convert, convert_then_rotate)


def test_axis_mappings():
    forward, left, up = np.eye(3)
    assert np.allclose(frames.point_to_engine(forward, frames.UNITY), [0, 0, 1])
    assert np.allclose(frames.point_to_engine(left, frames.UNITY), [-1, 0, 0])
    assert np.allclose(frames.point_to_engine(up, frames.UNITY), [0, 1, 0])
    assert np.allclose(frames.point_to_engine(left, frames.UNREAL), [0, -100, 0])
    assert np.allclose(frames.point_to_engine(up, frames.UNREAL), [0, 0, 100])
    assert not frames.UNITY.right_handed
    assert not frames.UNREAL.right_handed
    assert frames.ISAAC.right_handed


def test_unity_matches_ros_tcp_connector():
    # Unity's ROS-TCP-Connector converts FLU -> RUF quaternions as (-y, z, x, -w).
    for _ in range(50):
        x, y, z, w = random_quaternion()
        _, engine_rotation = frames.pose_to_engine(np.zeros(3), [x, y, z, w], frames.UNITY)
        assert same_rotation(engine_rotation, [-y, z, x, -w])


def test_ros_optical_frame_matches_standard_roll_pitch_yaw():
    # The usual camera_link -> camera_optical_frame rotation is roll -90, pitch 0, yaw -90.
    standard = Rotation.from_euler("xyz", [-90, 0, -90], degrees=True).as_matrix()
    assert np.allclose(frames.R_BODY_OPTICAL, standard)


def test_projection():
    K = frames.intrinsics(640, 480, 90)
    assert np.isclose(K[0, 0], 320)
    points = [[5, 0, 0], [5, 1, 0], [5, 0, 1], [-5, 0, 0]]
    uv, depth = frames.project(points, [0, 0, 0], np.eye(3), K)
    assert np.allclose(uv[0], [320, 240]) and np.isclose(depth[0], 5)
    assert uv[1, 0] < 320  # +y is left -> smaller u
    assert uv[2, 1] < 240  # +z is up -> smaller v
    assert depth[3] < 0  # behind the camera
