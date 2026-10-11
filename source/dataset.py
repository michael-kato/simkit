"""Writes captures to one engine-independent on-disk layout.

    <output_dir>/
      manifest.json        scenario, engine, simkit version
      rgb/000000.png
      depth/000000.exr     OpenEXR, one float32 channel "Z" in meters, inf = no hit
      instances/000000.png uint16 instance ids (0 = background)
      frames.jsonl         one FrameSpec + intrinsics K per line (canonical frame)
      annotations.json     COCO-format detection annotations derived from the masks

Whatever engine produced the data, training code reads only this layout.
"""

import json
from pathlib import Path

import numpy as np
import OpenEXR
from PIL import Image

from . import __version__
from .frames import intrinsics


def write_depth_exr(path, depth):
    """Save an HxW depth map in meters as a losslessly compressed float32 EXR."""
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    with OpenEXR.File(header, {"Z": depth.astype(np.float32)}) as exr_file:
        exr_file.write(str(path))


def read_depth_exr(path):
    """Load a depth map written by write_depth_exr. Returns an HxW float32 array in meters."""
    with OpenEXR.File(str(path)) as exr_file:
        return exr_file.channels()["Z"].pixels


class DatasetWriter:
    def __init__(self, output_dir, scenario, engine_name):
        self.output_dir = Path(output_dir)
        self.scenario = scenario
        for subfolder in ("rgb", "depth", "instances"):
            (self.output_dir / subfolder).mkdir(parents=True, exist_ok=True)
        # COCO category ids start at 1.
        self.category_ids = {label: index + 1 for index, label in enumerate(scenario.labels())}
        self.images = []
        self.annotations = []
        self._frames_file = (self.output_dir / "frames.jsonl").open("w")
        (self.output_dir / "manifest.json").write_text(json.dumps({
            "simkit_version": __version__,
            "engine": engine_name,
            "scenario": scenario.model_dump(mode="json"),
        }, indent=2))

    def write(self, frame, capture):
        """Save one FrameSpec and the Capture rendered from it."""
        file_stem = f"{frame.index:06d}"
        Image.fromarray(capture.rgb).save(self.output_dir / "rgb" / f"{file_stem}.png")
        if capture.depth is not None:
            write_depth_exr(self.output_dir / "depth" / f"{file_stem}.exr", capture.depth)
        if capture.instance_mask is not None:
            mask_image = Image.fromarray(capture.instance_mask.astype(np.uint16))
            mask_image.save(self.output_dir / "instances" / f"{file_stem}.png")
            self._annotate(frame, capture.instance_mask)

        h, w = capture.rgb.shape[:2]
        self.images.append({"id": frame.index, "file_name": f"rgb/{file_stem}.png", "width": w, "height": h})
        record = frame.to_dict()
        record["K"] = intrinsics(w, h, frame.camera.horizontal_fov_deg).tolist()
        self._frames_file.write(json.dumps(record) + "\n")

    def _annotate(self, frame, instance_mask):
        """Add a COCO bounding box for every labeled object visible in the mask."""
        for obj in frame.objects:
            if obj.label is None:
                continue
            pixel_rows, pixel_columns = np.nonzero(instance_mask == obj.instance_id)
            if pixel_columns.size == 0:  # fully occluded or out of view
                continue
            left, top = int(pixel_columns.min()), int(pixel_rows.min())
            width = int(pixel_columns.max()) - left + 1
            height = int(pixel_rows.max()) - top + 1
            self.annotations.append({
                "id": len(self.annotations) + 1,
                "image_id": frame.index,
                "category_id": self.category_ids[obj.label],
                "bbox": [left, top, width, height],
                "area": int(pixel_columns.size),
                "iscrowd": 0,
                "instance_id": obj.instance_id,
            })

    def close(self):
        self._frames_file.close()
        (self.output_dir / "annotations.json").write_text(json.dumps({
            "images": self.images,
            "annotations": self.annotations,
            "categories": [{"id": category_id, "name": label} for label, category_id in self.category_ids.items()],
        }))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
