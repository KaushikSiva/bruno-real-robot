import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REQUIRED_SCRIPTS = {
    "mac_setup.sh",
    "mac_calibrate.sh",
    "mac_simulate.sh",
    "mac_teleop.sh",
    "jetson_setup.sh",
    "jetson_prepare.sh",
    "jetson_preflight.sh",
    "run_onboard_session.sh",
    "stop_onboard.sh",
    "jetson_cleanup.sh",
}


def script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def fake_jetson_commands(tmp_path: Path, *, utc_hour: str) -> tuple[dict[str, str], Path]:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    robot_log = tmp_path / "robot.log"
    executable(binary_dir / "python", "#!/usr/bin/env bash\nexit 0\n")
    executable(
        binary_dir / "date",
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"+%H"* ]]; then echo "${FAKE_UTC_HOUR}"; '
        'else echo "2026-01-01 ${FAKE_UTC_HOUR}:00 UTC"; fi\n',
    )
    # The real helper prints its mode on `robot status`, and dev-mode/normal change
    # it. Model that: scripts verify developer mode by reading this output.
    executable(
        binary_dir / "robot",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >>"${FAKE_ROBOT_LOG}"\n'
        'case "$1" in\n'
        '  dev-mode) printf "developer\\n" >"${FAKE_ROBOT_MODE}" ;;\n'
        '  normal) printf "normal\\n" >"${FAKE_ROBOT_MODE}" ;;\n'
        "  status)\n"
        '    mode="normal"\n'
        '    [[ -s "${FAKE_ROBOT_MODE}" ]] && mode="$(<"${FAKE_ROBOT_MODE}")"\n'
        '    printf "Robot mode: %s\\n" "${mode}" ;;\n'
        "esac\n",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{binary_dir}:/usr/bin:/bin",
            "CONDA_PREFIX": str(tmp_path / "conda"),
            "CONDA_DEFAULT_ENV": "operator-g1",
            "FAKE_UTC_HOUR": utc_hour,
            "FAKE_ROBOT_LOG": str(robot_log),
            "FAKE_ROBOT_MODE": str(tmp_path / "robot_mode"),
        }
    )
    return environment, robot_log


def test_all_operator_scripts_are_executable_and_valid_bash() -> None:
    for name in REQUIRED_SCRIPTS:
        path = SCRIPTS / name
        assert path.is_file()
        assert os.access(path, os.X_OK)
    for path in SCRIPTS.rglob("*.sh"):
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_mac_scripts_share_framework_python_selector() -> None:
    for name in ("mac_setup.sh", "mac_calibrate.sh", "mac_simulate.sh", "mac_teleop.sh"):
        source = script(name)
        assert "lib/mac_python.sh" in source
        assert 'mac_python="$(select_mac_python)"' in source
        assert "--python 3.10" not in source


def test_simulation_uses_short_local_confirmation() -> None:
    simulation = script("mac_simulate.sh")
    assert '"${confirmation}" == "S"' in simulation
    assert "READY-FOR-SIM" not in simulation


def test_preflight_enforces_time_owner_zero_dev_and_green_order() -> None:
    source = script("jetson_preflight.sh")
    assert "02:00-09:00 UTC" in source
    assert "summit_signal.sdk_probe" in source
    assert source.index("robot status") < source.index("BUILTIN-OWNER-CONFIRMED")
    assert source.index("BUILTIN-OWNER-CONFIRMED") < source.index("robot zero")
    assert source.index("robot zero") < source.index("robot dev-mode")
    assert source.index("robot dev-mode") < source.index("FACE-GREEN")


def test_session_and_cleanup_require_normal_mode_status_and_disposable_env() -> None:
    session = script("run_onboard_session.sh")
    cleanup = script("jetson_cleanup.sh")
    setup = script("jetson_setup.sh")
    assert "summit_signal.sdk_probe" in session
    assert "02:00-09:00 UTC" in session
    assert session.index("robot normal") < session.rindex("robot status")
    assert "conda create" in setup
    assert "python=3.10" in setup
    assert "cyclonedds==0.10.2" in setup
    assert "conda env remove" in cleanup
    assert cleanup.index("robot normal") < cleanup.index("conda env remove")


# Reading these to confirm the session state is required; creating, editing or
# tearing one down is what must never happen. Any mention must be one of these
# exact read-only forms.
#
# Breaking connectivity locks out everyone including the admin, and the only
# recovery is someone physically walking to the robot with a monitor and
# keyboard. That has already cost a day once.
READ_ONLY_PERSISTENCE_CALLS = (
    "command -v crontab",
    "crontab -l",
    "command -v systemctl",
    "systemctl --user list-unit-files",
    "command -v tailscale",
    "tailscale status",
)
INSPECTION_ONLY_TOOLS = ("crontab", "systemctl", "tailscale")

# Anything that can drop the network, change addressing, or change name
# resolution. The facility owns all of it.
PROHIBITED_COMMANDS = (
    "--network-interface",
    "netplan",
    "systemctl enable",
    "systemctl start",
    "systemctl --user enable",
    "tailscale up",
    "tailscale down",
    "tailscale logout",
    "tailscale set",
    "ifconfig",
    "nmcli",
    "iptables",
    "nft ",
    "ufw ",
    "dhclient",
    "resolvectl",
    "systemd-networkd",
    "ip link set",
    "ip addr add",
    "ip addr del",
    "ip route",
    "/etc/resolv.conf",
    "/etc/hosts",
    "reboot",
    "shutdown -h",
    "crontab -e",
    "crontab -r",
)


def test_scripts_do_not_offer_network_or_persistent_service_changes() -> None:
    # Every shell script ships to the robot, not only the ones named above, and
    # helpers live in subdirectories such as scripts/lib/, so a newly added script
    # anywhere under scripts/ is covered by this contract automatically.
    shell_scripts = sorted(SCRIPTS.rglob("*.sh"))
    assert REQUIRED_SCRIPTS.issubset({path.name for path in shell_scripts})
    combined = "\n".join(path.read_text(encoding="utf-8") for path in shell_scripts)

    for command in PROHIBITED_COMMANDS:
        assert command not in combined, f"prohibited command in scripts/: {command}"

    # Belt and braces: every mention of these tools is a read-only inspection.
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for tool in INSPECTION_ONLY_TOOLS:
            if tool in stripped:
                assert any(call in stripped for call in READ_ONLY_PERSISTENCE_CALLS), (
                    f"non-read-only {tool} usage: {stripped}"
                )


def test_preflight_happy_path_calls_robot_commands_in_safe_order(tmp_path: Path) -> None:
    environment, robot_log = fake_jetson_commands(tmp_path, utc_hour="03")
    result = subprocess.run(
        [str(SCRIPTS / "jetson_preflight.sh")],
        input=("BUILTIN-OWNER-CONFIRMED\nEXCLUSIVE-CAMERA-KILLSWITCH-READY\nFACE-GREEN\n"),
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert robot_log.read_text(encoding="utf-8").splitlines() == [
        "status",
        "zero",
        "dev-mode",
        "status",
    ]


def test_preflight_refuses_outside_myt_hours_before_robot_io(tmp_path: Path) -> None:
    environment, robot_log = fake_jetson_commands(tmp_path, utc_hour="10")
    result = subprocess.run(
        [str(SCRIPTS / "jetson_preflight.sh")],
        input="",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 2
    assert "outside staffed hours" in result.stderr
    assert not robot_log.exists()


def test_session_validation_failure_after_preflight_restores_normal(tmp_path: Path) -> None:
    environment, robot_log = fake_jetson_commands(tmp_path, utc_hour="10")
    copied_project = tmp_path / "project"
    copied_scripts = copied_project / "scripts"
    copied_scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "run_onboard_session.sh", copied_scripts)

    result = subprocess.run(
        [str(copied_scripts / "run_onboard_session.sh")],
        input="",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 2
    assert "outside staffed hours" in result.stderr
    assert robot_log.read_text(encoding="utf-8").splitlines() == ["normal", "status"]


def test_session_refuses_to_launch_while_built_in_service_owns_the_robot(
    tmp_path: Path,
) -> None:
    """Commanding lowcmd against the running motion service makes the robot vibrate.

    The operator's --developer-mode-confirmed flag is only an assertion, so the
    wrapper must read `robot status` at launch time and refuse on a mismatch.
    """

    environment, robot_log = fake_jetson_commands(tmp_path, utc_hour="03")
    copied_project = tmp_path / "project"
    copied_scripts = copied_project / "scripts"
    copied_scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "run_onboard_session.sh", copied_scripts)
    # Mode file absent => `robot status` reports normal, i.e. no developer mode.

    result = subprocess.run(
        [str(copied_scripts / "run_onboard_session.sh")],
        input="",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 2
    assert "developer mode is not confirmed" in result.stderr
    assert "VIBRATE VIOLENTLY" in result.stderr
    # It must refuse before starting the controller, and still restore normal mode.
    assert "Onboard controller PID" not in result.stderr
    assert robot_log.read_text(encoding="utf-8").splitlines()[-2:] == ["normal", "status"]


def test_session_launches_once_developer_mode_is_reported(tmp_path: Path) -> None:
    environment, robot_log = fake_jetson_commands(tmp_path, utc_hour="03")
    Path(environment["FAKE_ROBOT_MODE"]).write_text("developer\n", encoding="utf-8")
    copied_project = tmp_path / "project"
    copied_scripts = copied_project / "scripts"
    copied_scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "run_onboard_session.sh", copied_scripts)

    result = subprocess.run(
        [str(copied_scripts / "run_onboard_session.sh")],
        input="",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert "developer mode is not confirmed" not in result.stderr
    assert "Onboard controller PID" in result.stderr
    del robot_log
