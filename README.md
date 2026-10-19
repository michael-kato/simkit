# simkit

**Engine-agnostic synthetic data tooling for robotics.** Describe a scenario
once, render it in Gazebo, Isaac Sim, Unity, Unreal or Blender, and get the
same dataset format and the same ROS 2 interface every time.

Picking a simulator is a big decision, and it's often made before you know
whether you need physics fidelity, photorealism or ROS integration most.
simkit keeps everything *around* the simulator portable, so the engine can
be swapped later without rewriting the pipeline.

```
                 ┌──────────────────────────┐
 scenario.yaml ─▶│  Scenario + sampler      │  all randomization, canonical ROS frame,
                 │  (seed, frame) → FrameSpec│  reproducible per frame
                 └────────────┬─────────────┘
                              ▼
                 ┌──────────────────────────┐
                 │  EngineAdapter           │  the ONLY engine-specific code:
                 │  gazebo | isaac | unity  │  frame conversion, spawning, rendering
                 │  unreal | blender | mock │
                 └────────────┬─────────────┘
                              ▼
                 ┌──────────────────────────┐
                 │  DatasetWriter           │  rgb / depth / instances / COCO /
                 │                          │  per-frame metadata
                 └────────────┬─────────────┘
                              ▼
                    training & evaluation (engine-blind)

 robot/robot.urdf ─────────▶ converted per engine (docs/engines.md)
 interfaces/ros2_contract ─▶ topics every sim and the real robot must expose
```

## What's in here

| Path | What it defines |
|---|---|
| `source/frames.py` | Canonical frame (ROS REP-103) and tested conversions to every engine's axes, handedness and units, plus camera optical-frame math |
| `source/scenario.py` | Scenario schema (assets, placement, camera, lights) and deterministic per-frame sampling |
| `source/adapters/` | `EngineAdapter` contract and one adapter per engine (`remote.py`: socket protocol for engines that host simkit in-process) |
| `source/dataset.py` | One on-disk dataset layout + COCO annotations, whatever engine made the data |
| `unity/`, `unreal/` | Engine-side projects that host simkit's server for those adapters |
| `robot/robot.urdf` | Robot description: the single source of truth for the robot body |
| `interfaces/ros2_contract.yaml` | ROS 2 topics, message types and TF frames the sim must provide |
| `scenarios/` | Example scenarios |
| `docs/engines.md` | Conventions table, engine trade-offs, how to add an engine |
| `docs/state/` | State map: the whole pipeline as diagrams, each page tied to the source it describes |

## Quickstart

```bash
pip install -e ".[dev]"
pytest
simkit validate scenarios/warehouse_pallets.yaml
simkit generate scenarios/warehouse_pallets.yaml --engine mock --out data/demo --frames 20
```

The `mock` engine needs no GPU or simulator. It draws boxes so the whole
pipeline (sampling → projection → labels → dataset) can be tested in CI.

## Design rules

1. **One canonical frame.** Everything stored or exchanged is ROS REP-103
   (x forward, y left, z up, meters). Conversion happens only inside adapters.
2. **Randomness lives in Python, not in the engine.** Frame *N* is fully
   determined by `(seed, N)`, so it's identical across engines and can be
   regenerated, sharded (`--start`) or compared sim-to-sim.
3. **Training code never knows which engine made the data.**
4. **Autonomy code never knows whether it's talking to a sim or a robot.** It
   only sees `interfaces/ros2_contract.yaml`.

## Engine status

| Engine | Status | Needs |
|---|---|---|
| mock | ✅ working | nothing |
| Gazebo Jetty (gz-sim 10) | ✅ working | `gz-jetty` (its `gz.transport` / `gz.msgs` Python bindings) |
| Unity 6.6 (URP) | ✅ working | the player built from `unity/` (below); `$SIMKIT_UNITY_PLAYER` to override its path |
| Unreal 5.8 | ✅ working | `$SIMKIT_UNREAL_EDITOR` pointing at `UnrealEditor`; runs the editor headless on `unreal/` |
| Isaac Sim | 🧩 stub with implementation plan | |
| Blender / BlenderProc | 🧩 stub with implementation plan | |

Build the Unity player once (and after changing `unity/`):

```bash
Unity -batchmode -quit -projectPath unity -executeMethod Simkit.Editor.SimkitBuild.Linux
```

Engines load meshes from a sibling export of each asset's `source`
(`pallet.usd` → `pallet.glb`): Gazebo and Unity take glTF, Unreal takes USD first.

## Roadmap

- [ ] ROS 2 package: `robot_state_publisher` launch + contract conformance test node
- [ ] rosbag2 (MCAP) recording of closed-loop runs
- [ ] Docker images (ROS 2 Jazzy + Gazebo; Isaac Sim container) and cloud GPU scripts
- [ ] Dataset versioning with DVC
- [ ] Baseline detector training + sim-vs-real evaluation report
- [ ] Sensor noise models (noise, blur, exposure, lens distortion) applied post-render, engine-independent

## License

MIT
