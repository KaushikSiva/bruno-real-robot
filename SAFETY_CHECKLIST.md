# Summit Signal safety checklist

Stop at the first unchecked item. The robot administrator has final authority.

## Before connecting

- [ ] `robot status` identifies the expected 29-DOF Unitree G1.
- [ ] Personal Tailscale access and exclusive operator access are confirmed.
- [ ] The complete commissioning config uses only `rt/lowcmd`/`rt/lowstate`,
      all-29 damping shutdown, and the three right-arm position joints.
- [ ] The checked-in target-slew and measured-speed trip values passed simulation
      and config tests unchanged.
- [ ] The complete joystick gesture passed in the pinned 29-DOF MuJoCo model.
- [ ] Circle alone does not stop, while R2+Circle, Options, joystick removal,
      malformed input, closed input, command timeout, and state timeout all
      produced a damping shutdown in tests.
- [ ] Controller direction was checked in simulation.
- [ ] Both sticks are calibrated on the Mac used for the hardware session.
- [ ] The public recording question was answered separately from access approval.

## Before enabling motion

- [ ] The staffed facility window is active.
- [ ] The live camera is open and the admin's independent killswitch is ready.
- [ ] The gantry, cables, robot arm, and room are visibly clear.
- [ ] The live safety camera is open and current.
- [ ] `robot status` was checked, then `robot zero` was run before developer mode.
- [ ] `robot dev-mode` completed and the green face light is visible.
- [ ] The Jetson reports valid 29-DOF position/velocity state and active 250 ms
      command and low-state watchdogs.
- [ ] L1 is released and both sticks are centered.

## Motion

- [ ] Hold L1 only for one small shoulder/elbow gesture.
- [ ] Do not move the lower body, waist, wrists, hands, or both arms.
- [ ] Do not walk, jump, run, fight the gantry, or repeat unexpected behavior.
- [ ] Release L1 immediately if the camera or telemetry hesitates.
- [ ] Hold R2 and press Circle for the joystick stop; the admin killswitch remains
      authoritative.

## Shutdown

- [ ] Press Options and observe `ALL-29 DAMPING SHUTDOWN COMPLETE`.
- [ ] Confirm the final physical state through the live camera.
- [ ] Confirm the wrapper ran `robot normal` and check `robot status`.
- [ ] Confirm no controller process remains and remove copied data/configuration.
- [ ] Remove the personal conda environment before ending the session.
