"""Engine-agnostic scenario spec and deterministic per-frame sampling.

All randomization happens here, in Python, in the canonical (ROS) frame.
Engines never roll their own dice: they receive a fully concrete FrameSpec
and only render it. That makes frame N identical across Gazebo, Isaac,
Unity, Unreal and Blender, and lets any frame be regenerated from
(scenario seed, frame index) alone.

Units: positions in meters (canonical frame), angles in degrees.
Any number in a scenario can be a fixed value or a [min, max] range that is
sampled uniformly for each frame.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import yaml
from pydantic import AfterValidator, BaseModel, Field, model_validator
from scipy.spatial.transform import Rotation


def check_range(value):
    """Reject [min, max] ranges where min > max. Single numbers pass through."""
    if isinstance(value, tuple) and value[0] > value[1]:
        raise ValueError(f"range minimum {value[0]} is greater than maximum {value[1]}")
    return value


def check_count(value):
    """Counts must be non-negative, and ranges must have min <= max."""
    low, high = value if isinstance(value, tuple) else (value, value)
    if low < 0 or low > high:
        raise ValueError(f"count must be >= 0 with min <= max, got {value}")
    return value


# These are type aliases that pydantic uses to validate the YAML.
NumberOrRange = Annotated[float | tuple[float, float], AfterValidator(check_range)]
CountOrRange = Annotated[int | tuple[int, int], AfterValidator(check_count)]
Output = Literal["rgb", "depth", "instance_segmentation", "bbox_2d"]


# --- scenario file schema -----------------------------------------------------

class Placement(BaseModel):
    """Where something goes and how it is oriented.

    Rotations follow the ROS roll/pitch/yaw convention: rotate about the fixed
    x axis (roll), then the fixed y axis (pitch), then the fixed z axis (yaw).
    Positive pitch tips the front (+x) downward.
    """

    x: NumberOrRange
    y: NumberOrRange
    z: NumberOrRange
    roll_deg: NumberOrRange = 0.0
    pitch_deg: NumberOrRange = 0.0
    yaw_deg: NumberOrRange = 0.0


class AssetSpec(BaseModel):
    """A 3D model to place in the scene, possibly several times per frame.

    ``name`` identifies the model ("pallet_wood"). ``label`` is the class the
    detector learns ("pallet"); several assets can share a label. Assets with no
    label are unannotated clutter ("distractors") that teach the model what to ignore.
    """

    name: str
    label: str | None = None
    source: str  # path to the asset file; USD is the canonical format
    count: CountOrRange = 1
    scale: NumberOrRange = 1.0
    placement: Placement


class CameraSpec(BaseModel):
    """A pinhole camera. ``placement`` is the camera body pose (x forward, ROS convention)."""

    name: str = "front_camera"
    width: int = Field(640, gt=0)
    height: int = Field(480, gt=0)
    horizontal_fov_deg: float = Field(90.0, gt=0, lt=180)
    placement: Placement


class LightSpec(BaseModel):
    """One light. Which fields matter depends on ``type``:

    sun     Infinitely far away. Direction from azimuth_deg (0 = +x, 90 = +y) and
            elevation_deg (0 = horizon, 90 = straight down). Intensity in lux.
    point   Shines in all directions from placement's position. Intensity in lumens.
    spot    Shines along its local +x axis from placement, in a cone of
            cone_angle_deg. Intensity in lumens.
    area    A width_m x height_m rectangle at placement, shining along its local
            +x axis. Intensity in lumens.

    Engines use different light units; each adapter converts from these.
    """

    type: Literal["sun", "point", "spot", "area"]
    intensity: NumberOrRange
    color_temperature_k: NumberOrRange = 6500.0
    azimuth_deg: NumberOrRange = 0.0
    elevation_deg: NumberOrRange = 45.0
    placement: Placement | None = None
    cone_angle_deg: NumberOrRange = 45.0
    width_m: NumberOrRange = 1.0
    height_m: NumberOrRange = 1.0

    @model_validator(mode="after")
    def check_placement(self):
        if self.type != "sun" and self.placement is None:
            raise ValueError(f"a {self.type} light needs a placement")
        return self


class Scenario(BaseModel):
    """Top level of a scenario file."""

    name: str
    seed: int = 0
    frames: int = Field(100, gt=0)
    assets: list[AssetSpec]
    camera: CameraSpec
    lights: list[LightSpec] = []
    outputs: list[Output] = ["rgb", "depth", "instance_segmentation", "bbox_2d"]

    @classmethod
    def from_yaml(cls, path):
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def labels(self):
        """Sorted list of annotated class names. Unlabeled assets are excluded."""
        return sorted({asset.label for asset in self.assets if asset.label is not None})

    def sample(self, index):
        return sample_frame(self, index)


# --- concrete values for one frame ---------------------------------------------

@dataclass
class ObjectInstance:
    instance_id: int  # 1-based; 0 means background in instance masks
    asset_name: str
    label: str | None  # None for unlabeled assets (distractors)
    source: str
    position: list[float]
    quaternion_xyzw: list[float]
    scale: float


@dataclass
class Light:
    """A sampled light. Fields that don't apply to its type are None."""

    type: str
    intensity: float
    color_temperature_k: float
    position: list[float] | None = None  # point, spot, area
    quaternion_xyzw: list[float] | None = None  # spot, area
    direction: list[float] | None = None  # unit vector the light travels along: sun, spot, area
    cone_angle_deg: float | None = None  # spot
    width_m: float | None = None  # area
    height_m: float | None = None  # area


@dataclass
class CameraPose:
    name: str
    position: list[float]
    quaternion_xyzw: list[float]  # camera body frame (x forward), not the optical frame
    width: int
    height: int
    horizontal_fov_deg: float


@dataclass
class FrameSpec:
    scenario: str
    index: int
    camera: CameraPose
    lights: list[Light] = field(default_factory=list)
    objects: list[ObjectInstance] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# --- sampling -------------------------------------------------------------------

def sample_uniform(rng, value):
    """Return ``value`` if it is a single number, otherwise a uniform sample from [min, max]."""
    if isinstance(value, tuple):
        return float(rng.uniform(value[0], value[1]))
    return float(value)


def sample_count(rng, count):
    """Return ``count`` if it is a single number, otherwise an integer from [min, max] inclusive."""
    if isinstance(count, tuple):
        return int(rng.integers(count[0], count[1] + 1))
    return count


def sample_pose(rng, placement):
    """Sample a Placement. Returns (position [x, y, z], quaternion [x, y, z, w])."""
    position = [sample_uniform(rng, placement.x), sample_uniform(rng, placement.y), sample_uniform(rng, placement.z)]
    roll_pitch_yaw = [
        sample_uniform(rng, placement.roll_deg),
        sample_uniform(rng, placement.pitch_deg),
        sample_uniform(rng, placement.yaw_deg),
    ]
    # Lowercase "xyz" means fixed (extrinsic) axes, which is the ROS roll/pitch/yaw convention.
    rotation = Rotation.from_euler("xyz", roll_pitch_yaw, degrees=True)
    return position, rotation.as_quat(canonical=True).tolist()


def sample_light(rng, light_spec):
    light = Light(
        type=light_spec.type,
        intensity=sample_uniform(rng, light_spec.intensity),
        color_temperature_k=sample_uniform(rng, light_spec.color_temperature_k),
    )

    if light_spec.type == "sun":
        azimuth = np.radians(sample_uniform(rng, light_spec.azimuth_deg))
        elevation = np.radians(sample_uniform(rng, light_spec.elevation_deg))
        toward_sun = np.array([np.cos(elevation) * np.cos(azimuth), np.cos(elevation) * np.sin(azimuth), np.sin(elevation)])
        light.direction = (-toward_sun).tolist()
        return light

    light.position, quaternion = sample_pose(rng, light_spec.placement)
    if light_spec.type == "point":
        return light

    light.quaternion_xyzw = quaternion
    light.direction = Rotation.from_quat(quaternion).apply([1.0, 0.0, 0.0]).tolist()
    if light_spec.type == "spot":
        light.cone_angle_deg = sample_uniform(rng, light_spec.cone_angle_deg)
    else:
        light.width_m = sample_uniform(rng, light_spec.width_m)
        light.height_m = sample_uniform(rng, light_spec.height_m)
    return light


def sample_frame(scenario, index):
    """Turn a Scenario's ranges into exact values for frame number ``index``."""
    # Seeding with (seed, index) makes each frame independent of the others,
    # so frames can be generated out of order, in parallel, or regenerated.
    rng = np.random.default_rng([scenario.seed, index])

    objects = []
    for asset in scenario.assets:
        for _ in range(sample_count(rng, asset.count)):
            position, quaternion = sample_pose(rng, asset.placement)
            objects.append(ObjectInstance(
                instance_id=len(objects) + 1,
                asset_name=asset.name,
                label=asset.label,
                source=asset.source,
                position=position,
                quaternion_xyzw=quaternion,
                scale=sample_uniform(rng, asset.scale),
            ))

    camera_spec = scenario.camera
    camera_position, camera_quaternion = sample_pose(rng, camera_spec.placement)
    camera = CameraPose(
        name=camera_spec.name,
        position=camera_position,
        quaternion_xyzw=camera_quaternion,
        width=camera_spec.width,
        height=camera_spec.height,
        horizontal_fov_deg=camera_spec.horizontal_fov_deg,
    )

    lights = [sample_light(rng, light_spec) for light_spec in scenario.lights]

    return FrameSpec(scenario=scenario.name, index=index, camera=camera, lights=lights, objects=objects)
