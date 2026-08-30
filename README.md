# Bruno Real Robot — Summit Signal

Simulation-first DualSense teleoperation for one small right-arm gesture on a
29-DOF Unitree G1 suspended from the facility gantry.

The Mac sends normalized goals at 10 Hz through one long-lived Tailscale SSH
connection. A process on the Jetson owns the 500 Hz loop, reads
`rt/lowstate`, and writes `rt/lowcmd`. Network latency never closes the
joint loop.

> This code has passed local tests and the pinned MuJoCo rehearsal. It has not
> yet commanded the remote G1. Run the simulation and facility preflight first.

The hardware contract follows the facility's
[Remote Robot Access — Unitree G1 on Gantry](https://app.notion.com/p/Remote-Robot-Access-Unitree-G1-on-Gantry-3ca864e1a25381ba8589e3f0c3afc1b3)
rules and Unitree's official
[G1 low-level example](https://github.com/unitreerobotics/unitree_sdk2_python/blob/master/example/g1/low_level/g1_low_level_example.py).

## What moves

- Position control is limited to right shoulder pitch (IDL 22), right shoulder
  roll (23), and right elbow (25).
- The commissioning profile limits shoulder excursion to 1 degree and elbow
  excursion to 1.5 degrees around the first measured pose.
- Target slew is limited to 0.1 rad/s and measured controlled-joint speed above
  0.5 rad/s latches shutdown.
- All other joints always receive damping-only commands: zero feed-forward
  torque, `kp=0`, `kd=8`.
- L1 is hold-to-run. Hold R2 and press Circle to latch shutdown. Options exits
  cleanly.
- No learned model, agent, MCP tool, or remote process can provide joint targets.

## Stop and restoration contract

There is no separate remote robot-stop API in developer mode. Every software
stop terminates this controller and takes the same path:

1. `SIGINT`, `SIGTERM`, `SIGHUP`, R2+Circle, joystick removal, malformed
   input, closed SSH/stdin, a 250 ms goal timeout, or stale robot state stops
   motion.
2. The Jetson writes all 29 motors with `tau=0`, `kp=0`, `kd=8` for one
   second, leaving damping as the last latched low-level frame.
3. The session wrapper runs `robot normal` to return ownership to the
   facility's built-in controller.

Available operator stops:

- Hold R2 and press Circle on the DualSense.
- Ctrl-C in the controller terminal.
- `scripts/stop_onboard.sh` from a second Jetson terminal.
- Normal `kill <controller-pid>` (SIGTERM). Never use `kill -9`; it cannot
  be trapped.
- Loss of SSH/goals automatically trips the onboard watchdog.
- The admin's independent killswitch remains the final physical authority.

If the robot is stuck, straining, noisy, faulted, caught in the harness, moving
without a camera view, or loses SSH while moving, contact the admin immediately.

## Architecture

```text
Mac / DualSense                         Jetson / robot-local DDS

calibrated axes                                rt/lowstate
      │                                             │
      ▼                                             ▼
normalized goals ── Tailscale SSH stdin ──► watchdog + limits
10 Hz, no joint targets                            │
                                                   ▼
                                         500 Hz lowcmd frames
                                                   │
                       ┌───────────────────────────┴────────────────────┐
                       ▼                                                ▼
          right arm 22/23/25 position                    all 29 joints damping
          bounded target, ramped gain                    tau=0, kp=0, kd=8
```

## 1. Mac setup and calibration

```bash
git clone https://github.com/KaushikSiva/bruno-real-robot.git
cd bruno-real-robot
uv sync --extra simulation --extra test

uv run summit-signal-operator --list-joysticks
uv run summit-signal-operator \
  --calibrate runtime/dualsense-arm.json \
  --joystick-index 0
```

Default controls are L1 dead-man, left-stick vertical/horizontal for right
shoulder pitch/roll, right-stick vertical for right elbow, R2+Circle emergency
stop, and Options clean exit. The configuration uses the standard SDL
DualSense mapping (Circle button 1 and R2 trigger axis 5); the required
simulation is also the control-mapping check for this Mac.

## 2. Required simulation

Clone and pin Unitree's official model beside this repository:

```bash
git clone https://github.com/unitreerobotics/unitree_mujoco.git ../unitree_mujoco
git -C ../unitree_mujoco checkout 4134cb5dc7ff1ba7f484deda48b5274b58694519
```

Run the exact commissioning profile headlessly:

```bash
uv run summit-signal-smoke \
  --model ../unitree_mujoco/unitree_robots/g1/scene_29dof.xml \
  --hardware-config config/hardware.example.json \
  --seconds 3
```

Then test the joystick on macOS:

```bash
uv run mjpython -m summit_signal.simulator \
  --model ../unitree_mujoco/unitree_robots/g1/scene_29dof.xml \
  --hardware-config config/hardware.example.json \
  --calibration runtime/dualsense-arm.json \
  --joystick-index 0 \
  --record-goals runtime/simulation-goals.ndjson \
  --record-initial-state runtime/simulation-initial-state.json \
  --seconds 30
```

Perform one small shoulder movement, return to center, release L1, then hold R2
and press Circle once. Circle alone must not stop; the chord must stop. The model
braces non-commanded joints, so this validates the arm
mapping and safety envelope—not whole-body balance or the physical gantry.

The checked-in configuration contains complete commissioning numbers and no
`null` values. It works in simulation, but `hardware_activation.enabled`
remains false so it cannot initialize hardware.

## 3. Disposable Jetson conda environment

Connect only through your personal facility Tailscale access. Do not commit the
host, password, camera URL, tailnet identity, or other facility credentials.

```bash
git clone https://github.com/KaushikSiva/bruno-real-robot.git
cd bruno-real-robot

conda create -n YOURNAME-g1 python=3.10 -y
conda activate YOURNAME-g1
pip install cyclonedds==0.10.2
python -c "import cyclonedds, unitree_sdk2py"
```

Do not install or change the system Python. The facility supplies
`unitree_sdk2py` on the system `PYTHONPATH`; this repository runs directly
from `src/` and needs no Jetson package installation.

After the simulation passes and your personal facility access is active, create
the ignored real-robot copy from the exact simulation profile:

```bash
PYTHONPATH=src python -m summit_signal.activation \
  --facility-rules-acknowledged \
  --personal-access-confirmed \
  --simulation-passed
```

This creates private `runtime/hardware.json` with every numeric value already
filled and changes only `hardware_activation.enabled` to true. It records your
acknowledgements; it does not claim that an administrator created or approved
the file. The controller still requires the live access, hours, camera, and
developer-mode flags below.

## 4. Enter developer mode safely

During staffed on-site hours, keep the live camera open and confirm the admin
killswitch is ready. In the activated disposable conda environment:

```bash
./scripts/jetson_preflight.sh
```

The script checks Python 3.10 and both SDK imports, shows `robot status`,
requires typed camera/killswitch confirmation, runs `robot zero` while the
built-in service still owns the robot, then runs `robot dev-mode`. It requires
visual confirmation of the green face light. If preflight is interrupted, it
runs `robot normal`.

Do not start the controller unless preflight completes.

## 5. Run Mac-to-Jetson teleoperation

From the calibrated Mac checkout:

```bash
uv run summit-signal-operator \
  --calibration runtime/dualsense-arm.json \
  --joystick-index 0 \
  --seconds 30 |
ssh -T ROBOT_HOST \
  'cd bruno-real-robot && conda run -n YOURNAME-g1 --no-capture-output \
    ./scripts/run_onboard_session.sh \
      --config runtime/hardware.json \
      --motion-profile commissioning \
      --real-robot \
      --facility-rules-acknowledged \
      --exclusive-access-confirmed \
      --within-onsite-hours-confirmed \
      --camera-confirmed \
      --developer-mode-confirmed'
```

Tailscale is only the SSH transport. Do not pass the Tailscale interface to DDS
or change network configuration. The adapter uses the facility's preconfigured
local SDK/DDS default; `--network-interface` exists only if the admin gives an
explicit robot-local DDS interface.

Keep L1 released until `CONNECTED` appears. Hold L1, make one small
right-shoulder movement, return the stick to center, release L1, and press
Options. Confirm both:

```text
ALL-29 DAMPING SHUTDOWN COMPLETE
Restoring the facility's built-in controller with: robot normal
```

From another Jetson terminal, the safe software-stop command is:

```bash
cd bruno-real-robot
./scripts/stop_onboard.sh
```

## 6. Cleanup

Verify `robot status` shows the built-in controller restored, no Summit Signal
process remains, and the robot is visually safe. Then:

```bash
conda deactivate
conda env remove -n YOURNAME-g1
```

Delete unneeded recordings and the checkout if the facility requires it. Do not
create services, cron jobs, autostart entries, network changes, or persistent
environments.

## Verification

```bash
uv run --extra test pytest
bash -n scripts/*.sh
```

Tests use fake DDS messages and never import the Unitree SDK, initialize DDS,
open SSH, or command hardware. The first I/O boundary is
`UnitreeLowLevelAdapter.connect()`, reached only after all activation gates.
