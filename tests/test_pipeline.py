import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from simkit.adapters import ADAPTERS, get_adapter
from simkit.adapters.base import Capture
from simkit.cli import generate, main
from simkit.dataset import read_depth_exr, write_depth_exr
from simkit.scenario import Scenario

EXAMPLE = Path(__file__).parents[1] / "scenarios" / "warehouse_pallets.yaml"
MINIMAL_CAMERA = {"placement": {"x": 0, "y": 0, "z": 1}}


@pytest.fixture
def scenario():
    return Scenario.from_yaml(EXAMPLE)


def minimal_scenario(**overrides):
    fields = {"name": "test", "assets": [], "camera": MINIMAL_CAMERA, **overrides}
    return Scenario.model_validate(fields)


def test_sampling_is_deterministic_per_frame(scenario):
    assert scenario.sample(7).to_dict() == scenario.sample(7).to_dict()
    assert scenario.sample(7).to_dict() != scenario.sample(8).to_dict()


def test_instance_ids_are_unique_and_one_based(scenario):
    for index in range(20):
        instance_ids = [obj.instance_id for obj in scenario.sample(index).objects]
        assert instance_ids == list(range(1, len(instance_ids) + 1))


def test_fixed_values_and_ranges():
    asset = {"name": "box", "source": "box.usd", "count": 3, "placement": {"x": [1, 2], "y": 0.5, "z": 4}}
    frame = minimal_scenario(assets=[asset]).sample(0)
    assert len(frame.objects) == 3
    for obj in frame.objects:
        assert 1 <= obj.position[0] <= 2
        assert obj.position[1:] == [0.5, 4.0]


@pytest.mark.parametrize("bad_asset_field", [{"count": [3, 1]}, {"count": -1}, {"scale": [2.0, 1.0]}])
def test_invalid_ranges_rejected(bad_asset_field):
    asset = {"name": "box", "source": "box.usd", "placement": {"x": 0, "y": 0, "z": 0}, **bad_asset_field}
    with pytest.raises(ValueError):
        minimal_scenario(assets=[asset])


def test_non_sun_light_requires_placement():
    with pytest.raises(ValueError):
        minimal_scenario(lights=[{"type": "point", "intensity": 1000}])


def test_light_types_sample_their_fields():
    placement = {"x": 0, "y": 0, "z": 3, "pitch_deg": 90}  # pointing straight down
    lights = [
        {"type": "sun", "intensity": 1000, "elevation_deg": 90},
        {"type": "point", "intensity": 1000, "placement": placement},
        {"type": "spot", "intensity": 1000, "placement": placement, "cone_angle_deg": [20, 40]},
        {"type": "area", "intensity": 1000, "placement": placement, "width_m": 2.0},
    ]
    sun, point, spot, area = minimal_scenario(lights=lights).sample(0).lights
    assert np.allclose(sun.direction, [0, 0, -1])
    assert point.position == [0, 0, 3] and point.direction is None
    assert np.allclose(spot.direction, [0, 0, -1]) and 20 <= spot.cone_angle_deg <= 40
    assert area.width_m == 2.0 and area.cone_angle_deg is None


@pytest.mark.parametrize("name", sorted(set(ADAPTERS) - {"mock"}))
def test_engine_stubs_import_and_declare_conventions(name):
    adapter_class = get_adapter(name)
    assert adapter_class.name == name and adapter_class.convention is not None


def test_mock_generate_writes_dataset(scenario, tmp_path):
    generate(scenario, "mock", tmp_path, frame_count=5)

    dataset = json.loads((tmp_path / "annotations.json").read_text())
    assert len(dataset["images"]) == 5
    assert {category["name"] for category in dataset["categories"]} == {"pallet", "forklift"}
    assert dataset["annotations"], "expected at least one visible labeled object"
    for annotation in dataset["annotations"]:
        left, top, width, height = annotation["bbox"]
        assert 0 <= left and 0 <= top and left + width <= 640 and top + height <= 480

    # An annotation's bbox must match the pixels of its instance in the mask.
    annotation = dataset["annotations"][0]
    mask = np.array(Image.open(tmp_path / "instances" / f"{annotation['image_id']:06d}.png"))
    pixel_rows, pixel_columns = np.nonzero(mask == annotation["instance_id"])
    assert annotation["bbox"][:2] == [pixel_columns.min(), pixel_rows.min()]
    assert annotation["area"] == pixel_columns.size

    lines = (tmp_path / "frames.jsonl").read_text().splitlines()
    assert len(lines) == 5 and "K" in json.loads(lines[0])
    depth = read_depth_exr(tmp_path / "depth" / "000000.exr")
    assert depth.shape == (480, 640) and depth.dtype == np.float32


def test_depth_exr_round_trip_is_exact(tmp_path):
    depth = np.full((4, 5), np.inf, dtype=np.float32)
    depth[1, 2] = 3.25
    write_depth_exr(tmp_path / "depth.exr", depth)
    assert np.array_equal(read_depth_exr(tmp_path / "depth.exr"), depth)


@pytest.mark.parametrize("bad_field", [
    {"rgb": np.zeros((4, 5), np.uint8)},  # missing color channels
    {"rgb": np.zeros((4, 5, 3), np.float32)},  # wrong dtype
    {"depth": np.zeros((4, 6), np.float32)},  # wrong size
    {"instance_mask": np.zeros((4, 5), np.float32)},  # not integers
])
def test_capture_rejects_malformed_arrays(bad_field):
    fields = {"rgb": np.zeros((4, 5, 3), np.uint8), **bad_field}
    with pytest.raises(ValueError):
        Capture(**fields)


def test_cli_validate(capsys):
    assert main(["validate", str(EXAMPLE)]) == 0
    assert "warehouse_pallets: OK" in capsys.readouterr().out
