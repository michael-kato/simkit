"""The contract every engine adapter implements, plus helpers adapters share."""

import os
import signal
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

PRIMITIVE_CUBE = "primitive:cube"  # a 1 m cube centered on its pose, times ObjectInstance.scale
# Engines without physical light units get intensities relative to this
# illuminance: a sun of REFERENCE_LUX, or a lamp giving REFERENCE_LUX at 1 m, has intensity 1.
REFERENCE_LUX = 1000.0


@dataclass
class Capture:
    """One rendered frame, already converted back to simkit conventions.

    rgb:            HxWx3 uint8
    depth:          HxW float32, meters along the optical axis; inf = no hit
    instance_mask:  HxW int32; 0 = background, otherwise ObjectInstance.instance_id
    """

    rgb: np.ndarray
    depth: np.ndarray | None = None
    instance_mask: np.ndarray | None = None

    def __post_init__(self):
        # Dataclasses call this right after __init__. Checking here makes a buggy
        # adapter fail immediately, instead of deep inside the dataset writer.
        if self.rgb.ndim != 3 or self.rgb.shape[2] != 3 or self.rgb.dtype != np.uint8:
            raise ValueError(f"rgb must be an HxWx3 uint8 array, got shape {self.rgb.shape} and dtype {self.rgb.dtype}")
        height, width = self.rgb.shape[:2]
        if self.depth is not None:
            if self.depth.shape != (height, width) or not np.issubdtype(self.depth.dtype, np.floating):
                raise ValueError(f"depth must be a {height}x{width} float array, got shape {self.depth.shape} and dtype {self.depth.dtype}")
        if self.instance_mask is not None:
            if self.instance_mask.shape != (height, width) or not np.issubdtype(self.instance_mask.dtype, np.integer):
                raise ValueError(f"instance_mask must be a {height}x{width} integer array, got shape {self.instance_mask.shape} and dtype {self.instance_mask.dtype}")


class EngineAdapter(ABC):
    """Renders FrameSpecs. Owns all engine-specific code and frame conversion.

    Lifecycle: ``setup()`` once, then ``apply(frame)`` + ``capture()`` per frame,
    then ``close()``. Adapters must not sample randomness themselves.

    Subclasses set two class attributes: ``name`` (the engine's name in the CLI)
    and ``convention`` (a frames.Convention describing the engine's axes).
    """

    name = None
    convention = None

    def __init__(self, scenario):
        self.scenario = scenario

    @abstractmethod
    def setup(self):
        """Start/connect to the engine and load the static world."""

    @abstractmethod
    def apply(self, frame):
        """Spawn/move objects, set lights and camera to match a FrameSpec."""

    @abstractmethod
    def capture(self):
        """Render the current state and return a Capture in simkit conventions."""

    def close(self):
        pass

    def __enter__(self):
        self.setup()
        return self

    def __exit__(self, *exc):
        self.close()


def resolve_asset(source, extensions):
    """Return ``source`` itself for primitives, otherwise the absolute path of
    its sibling export with the first of ``extensions`` that exists
    (``pallet.usd`` -> ``pallet.glb``). Relative paths are from the working directory."""
    if source.startswith("primitive:"):
        if source != PRIMITIVE_CUBE:
            raise ValueError(f"unknown primitive {source!r}; only {PRIMITIVE_CUBE!r} exists")
        return source
    for extension in extensions:
        path = Path(source).with_suffix(extension).resolve()
        if path.is_file():
            return str(path)
    raise FileNotFoundError(f"no {'/'.join(extensions)} export next to {source}")


def relative_intensity(light):
    """Unitless intensity for engines without physical light units (see REFERENCE_LUX).

    Point and spot lights are converted from lumens to candela, so an engine
    with inverse-square falloff lights a surface 1 m away like the real lamp would.
    """
    if light.type == "sun":
        return light.intensity / REFERENCE_LUX
    if light.type == "point":
        return light.intensity / (4 * np.pi) / REFERENCE_LUX
    if light.type == "spot":
        solid_angle = 2 * np.pi * (1 - np.cos(np.radians(light.cone_angle_deg) / 2))
        return light.intensity / solid_angle / REFERENCE_LUX
    raise ValueError(f"{light.type} lights need an engine with physical light units")


def launch_engine(command, engine_name):
    """Start an engine process in its own process group, logging to logs/<run>/<engine>.log."""
    log_path = Path("logs") / datetime.now().strftime("%Y-%m-%d_%H%M%S") / f"{engine_name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log_file:
        process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    process.log_path = log_path
    return process


def stop_engine(process, timeout_s=10):
    """Stop the whole process group: engines often spawn helper processes."""
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
