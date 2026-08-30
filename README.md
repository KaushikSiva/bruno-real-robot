# Bruno Real Robot — Summit Signal

Simulation-first DualSense teleoperation for one small right-arm gesture on a
29-DOF Unitree G1 suspended from the facility gantry.

The Mac sends normalized goals at 10 Hz through one long-lived Tailscale SSH
connection. The Jetson owns the 500 Hz loop, subscribes to `rt/lowstate`, and
publishes `rt/lowcmd`. No balance, torque, or position loop crosses the tunnel.

> The software and pinned simulation pass locally and in CI. It has not yet
> commanded the physical G1. Complete every live prompt and stop if uncertain.

## Motion and shutdown contract

- Only right shoulder pitch (IDL 22), shoulder roll (23), and elbow (25)
  receive position gains.
- The commissioning profile permits exactly 5 degrees at each controlled
  shoulder and elbow joint around the first measured pose.
- L1 is hold-to-run. R2+Circle requests emergency shutdown. Circle alone does
  nothing. Options is a normal clean exit.
- Every other joint is damping-only: `tau=0`, `kp=0`, `kd=8`.
- Ctrl-C, SIGTERM, SIGHUP, R2+Circle, joystick removal, malformed/closed input,
  goal timeout, or stale robot state all enter the same shutdown path.
- Shutdown writes `tau=0`, `kp=0`, `kd=8` to all 29 joints for one second.
  The wrapper then runs `robot normal` and requires a successful `robot status`.
- Never use `kill -9`; it cannot run the damping handler.

The facility's admin killswitch remains the independent final authority. It can
fire at any time without warning; the robot goes slack and this controller loses
it mid-motion. Nothing here depends on finishing a motion, and the damping write
still completes against a robot that is already off — keep it that way. If the
robot is stuck, straining, noisy, faulted, caught in the harness, moving without
a live camera, or loses SSH while moving, stop and contact the admin immediately.
Escalate the moment you are unsure. A false alarm costs a message; the
alternative costs a humanoid.

## Simple session: run these scripts in order

Do not put the robot host, password, camera URL, or tailnet identity in this
repository. Supply them only when connecting.

### A. Mac: setup and required simulation

```bash
git clone https://github.com/KaushikSiva/bruno-real-robot.git
cd bruno-real-robot

./scripts/mac_setup.sh
./scripts/mac_calibrate.sh
./scripts/mac_simulate.sh
```

`mac_setup.sh` creates a local uv environment using a framework-enabled macOS
Python (3.10-3.13; Homebrew Python 3.12 is preferred), clones Unitree's
official MuJoCo repository beside this checkout, pins commit
`4134cb5dc7ff1ba7f484deda48b5274b58694519`, and runs a headless smoke test.
`mac_simulate.sh` requires the real joystick gesture and verifies that a
connected R2+Circle emergency goal was recorded.

In MuJoCo:

1. Keep L1 released and center both sticks.
2. Hold L1.
3. Move only the left stick vertically about 25% for one second.
4. Center the stick and release L1.
5. Press Circle alone; it must not stop.
6. Hold R2 and press Circle; simulation must stop.

### B. Jetson: pull, create the disposable environment, and prepare

Connect using your personal Tailscale SSH access, then:

```bash
git clone https://github.com/KaushikSiva/bruno-real-robot.git
cd bruno-real-robot

./scripts/jetson_setup.sh
./scripts/jetson_prepare.sh
```

On later sessions, use `git pull --ff-only` instead of cloning. Setup creates
only your named `kaushik` conda environment with Python 3.10 and
`cyclonedds==0.10.2`. It verifies the exact CycloneDDS version and imports the
facility-provided `unitree_sdk2py`; it never installs into system Python.

Python 3.10 matches the system. Setup installs the facility's exact cached
CPython 3.10 aarch64 wheel directly, so pip cannot silently fall back to a
source build. It stores `/opt/unitree_sdk2_python` and the repository source in
the disposable environment's `PYTHONPATH`, sets `PYTHONNOUSERSITE=1`, and
installs with `--no-user` so nothing can land in `~/.local` where
`conda env remove` would not reach it. It then confirms `cyclonedds` resolves
inside your own prefix. `jetson_preflight.sh` and
`run_onboard_session.sh` re-check that last property, so a session cannot start
from a polluted system Python.

Preparation creates private, ignored `runtime/hardware.json` from the complete
checked-in commissioning profile. There are no `null` values. This records your
rules/access/simulation acknowledgement; it does not claim an administrator
created the configuration.

### C. Jetson: preflight and enter developer mode

Keep the live camera open, then run:

```bash
./scripts/jetson_preflight.sh
```

The script refuses to continue unless all of these are true:

- the disposable environment is active through `conda run`;
- Python is exactly 3.10 and CycloneDDS is exactly 0.10.2;
- `robot status` was inspected and built-in ownership was explicitly confirmed;
- exclusive access, live camera, and admin killswitch readiness were confirmed;
- `robot zero` runs before `robot dev-mode`;
- the green face light is confirmed on camera.

If preflight fails after ownership changes begin, it attempts `robot normal`.
Do not start teleoperation unless `PREFLIGHT COMPLETE` is printed.

### D. Mac: run the 30-second teleoperation

From the Mac checkout:

```bash
./scripts/mac_teleop.sh ROBOT_SSH_HOST
```

The script checks Tailscale, prompts for the camera URL without saving it, opens
the camera, requires green-face/live-camera confirmation, and starts the SSH
goal stream. The remote repository is expected at `~/bruno-real-robot`.

For the first real test, repeat only the bounded 5-degree vertical left-stick
movement from simulation. Stop after one movement.

## Stop options

- Hold R2 and press Circle.
- Press Ctrl-C in the Mac teleoperation terminal.
- Press Options for a normal clean finish.
- From a second Jetson terminal:

  ```bash
  cd bruno-real-robot
  ./scripts/stop_onboard.sh
  ```

- Or send normal SIGTERM: `kill -TERM "$(cat runtime/onboard.pid)"`.
- The admin can use the independent physical killswitch at any time.

Wait for both messages before treating a software stop as complete:

```text
ALL-29 DAMPING SHUTDOWN COMPLETE
Restoring the facility's built-in controller with: robot normal
```

If the camera dies, the robot vibrates, a joint faults, SSH is lost during
motion, or either message is missing, contact the admin immediately.

### E. Jetson: deactivate and remove everything from the session

If you manually activated the environment, deactivate it first:

```bash
conda deactivate
```

Then, after checking the robot on camera:

```bash
cd bruno-real-robot
./scripts/jetson_cleanup.sh
```

Cleanup refuses to proceed if the controller is still running or if the target
environment is active. It requires camera confirmation, runs `robot normal`,
requires `robot status` and a second camera confirmation, removes the named
conda environment, and deletes the private hardware configuration and PID file.
Delete unneeded recordings and the checkout too if required by the facility.

No script modifies Tailscale, netplan, interfaces, DNS, firewall, systemd,
shared configuration, the gantry, reboot state, cron, or autostart.

## Script index

| Machine | Script | Purpose |
| --- | --- | --- |
| Mac | `mac_setup.sh` | Framework Python, pinned model, headless smoke test |
| Mac | `mac_calibrate.sh` | Detect and calibrate the DualSense |
| Mac | `mac_simulate.sh` | Required gesture and R2+Circle rehearsal |
| Jetson | `jetson_setup.sh` | Disposable `kaushik` conda environment |
| Jetson | `jetson_prepare.sh` | Private activated commissioning config |
| Jetson | `jetson_preflight.sh` | Hours/status/zero/dev-mode/green checks |
| Mac | `mac_teleop.sh HOST` | Camera-confirmed SSH teleoperation |
| Jetson | `stop_onboard.sh` | Validated SIGTERM from a second terminal |
| Jetson | `jetson_cleanup.sh` | Normal mode, status, env and runtime removal |

## Verification for developers

```bash
uv run --extra test pytest -q
uvx --from ruff==0.12.10 ruff check .
uvx --from ruff==0.12.10 ruff format --check .
bash -n scripts/*.sh
```

Tests use fake DDS messages. Unitree SDK imports and DDS initialization remain
lazy; the first robot I/O occurs only after the full activation gate and a
centered, disarmed live goal.

## References

- [Facility remote-access rules](https://app.notion.com/p/Remote-Robot-Access-Unitree-G1-on-Gantry-3ca864e1a25381ba8589e3f0c3afc1b3)
- [Official Unitree Python G1 low-level example](https://github.com/unitreerobotics/unitree_sdk2_python/blob/master/example/g1/low_level/g1_low_level_example.py)
- [Official Unitree MuJoCo repository](https://github.com/unitreerobotics/unitree_mujoco)
