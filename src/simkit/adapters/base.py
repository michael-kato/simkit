"""The contract every engine adapter implements."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


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
