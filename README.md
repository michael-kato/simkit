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
| `src/simkit/frames.py` | Canonical frame (ROS REP-103) and tested conversions to every engine's axes, handedness and units, plus camera optical-frame math |
| `src/simkit/scenario.py` | Scenario schema (assets, placement, camera, lights) and deterministic per-frame sampling |
| `src/simkit/adapters/` | `EngineAdapter` contract, a working `mock` engine, and documented stubs for each real engine |
| `src/simkit/dataset.py` | One on-disk dataset layout + COCO annotations, whatever engine made the data |
| `robot/robot.urdf` | Robot description: the single source of truth for the robot body |
| `interfaces/ros2_contract.yaml` | ROS 2 topics, message types and TF frames the sim must provide |
| `scenarios/` | Example scenarios |
| `docs/engines.md` | Conventions table, engine trade-offs, how to add an engine |

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

| Engine | Status |
|---|---|
| mock | ✅ working |
| Gazebo Harmonic | 🧩 stub with implementation plan |
| Isaac Sim | 🧩 stub with implementation plan |
| Unity | 🧩 stub with implementation plan |
| Unreal 5 | 🧩 stub with implementation plan |
| Blender / BlenderProc | 🧩 stub with implementation plan |

## Roadmap

- [ ] First real adapter (Gazebo or Isaac, whichever the project commits to)
- [ ] ROS 2 package: `robot_state_publisher` launch + contract conformance test node
- [ ] rosbag2 (MCAP) recording of closed-loop runs
- [ ] Docker images (ROS 2 Jazzy + Gazebo; Isaac Sim container) and cloud GPU scripts
- [ ] Dataset versioning with DVC
- [ ] Baseline detector training + sim-vs-real evaluation report
- [ ] Sensor noise models (noise, blur, exposure, lens distortion) applied post-render, engine-independent

## License

MIT
