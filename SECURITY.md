# Safety and security boundary

This repository is a manual, three-joint G1 experiment. Do not connect its
operator stream to an agent, model, autonomous planner, MCP tool, or web input.
Do not commit robot hosts, passwords, tailnet identities, camera URLs, or an
activated `runtime/hardware.json`.

Only normal process termination is a software stop. Never use `SIGKILL` or
`kill -9`: it cannot run the mandatory all-29-joint damping handler. Use
R2+Circle, Ctrl-C, `scripts/stop_onboard.sh`, or normal `SIGTERM`. The admin's
independent killswitch remains authoritative.

For a safety defect, do not reproduce it on hardware. Stop the process safely,
restore `robot normal` if the facility procedure allows it, and contact the
facility admin immediately.
