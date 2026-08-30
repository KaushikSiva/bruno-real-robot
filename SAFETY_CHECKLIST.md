# Summit Signal safety checklist

Escalate the moment you are unsure — a false alarm costs a message, the
alternative costs a humanoid. The admin's killswitch can fire at any time
without warning; expect the robot to go slack and settle into the harness.

Stop at the first unchecked item. The robot administrator has final authority.

## Before connecting

- [ ] `robot status` identifies the expected 29-DOF Unitree G1.
- [ ] Personal Tailscale access and exclusive operator access are confirmed.
- [ ] No change to Tailscale, netplan, interfaces, DNS, or the firewall is made at
      any point. Breaking connectivity locks out the admin too, and recovery needs
      someone physically at the robot.
- [ ] The disposable non-base environment uses Python 3.10, reports
      `cyclonedds==0.10.2`, and imports the facility Unitree SDK.
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

- [ ] The live camera is open and the admin's independent killswitch is ready.
- [ ] The gantry, cables, robot arm, and room are visibly clear.
- [ ] The live safety camera is open and current.
- [ ] `robot status` shows the built-in service owns the robot.
- [ ] `robot zero` was run only while that built-in service owned the robot, then
      `robot dev-mode` was run.
- [ ] `robot dev-mode` completed and the green face light is visible.
- [ ] The Jetson reports valid 29-DOF position/velocity state, a 250 ms goal
      watchdog, and a 100 ms low-state watchdog.
- [ ] L1 is released and both sticks are centered.

## Motion

- [ ] Hold L1 only for one small shoulder/elbow gesture.
- [ ] Do not move the lower body, waist, wrists, hands, or both arms.
- [ ] Do not walk, jump, run, fight the gantry, or repeat unexpected behavior.
- [ ] Release L1 immediately if the camera or telemetry hesitates.
- [ ] Hold R2 and press Circle for the joystick stop; the admin killswitch remains
      authoritative.

## Shutdown

- [ ] Stop with Options or R2+Circle and observe
      `ALL-29 DAMPING SHUTDOWN COMPLETE`.
- [ ] Confirm the final physical state through the live camera.
- [ ] Confirm the wrapper ran `robot normal` and check `robot status`.
- [ ] The robot is left damped and resting in the harness, not holding a stance.
- [ ] Confirm no controller process remains and remove copied data/configuration.
- [ ] Run `./scripts/jetson_cleanup.sh` and see `JETSON CLEANUP COMPLETE`;
      nothing installed this session may outlive it.
- [ ] Delete recordings and scratch data you do not need. The Orin's disk is small
      and shared.
- [ ] No user cron entry, user unit, or autostart entry survives a power cycle;
      cleanup reports `JETSON CLEANUP INCOMPLETE` if it finds one.
- [ ] Tell the admin about anything that felt off, even if it resolved itself.
