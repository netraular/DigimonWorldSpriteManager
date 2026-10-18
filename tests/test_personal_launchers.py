import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "Scripts"


@unittest.skipUnless(os.name == "posix", "Native process fixtures require POSIX")
class PersonalLauncherTests(unittest.TestCase):
    def run_launcher(self, windows=False, acknowledged=True, exit_code=0, hold=0):
        with tempfile.TemporaryDirectory(prefix="personal launcher ") as temporary:
            root = pathlib.Path(temporary)
            runtime = root / "runtime with spaces"
            runtime.mkdir()
            scratch = root / "fifo"
            scratch.mkdir()
            commands = ["online autosync off"]
            if windows:
                commands.append("online autosync")
            executable = runtime / ("main.exe" if windows else "main")
            executable.write_text(
                f"""#!{sys.executable}
import os, pathlib, select, sys, time
pathlib.Path("child.pid").write_text(str(os.getpid()))
for expected in {commands!r}:
    assert sys.stdin.readline().rstrip("\\n") == expected
assert not select.select([sys.stdin], [], [], 0.05)[0], "stdin closed"
print({"  auto content install: off" if acknowledged else "not acknowledged"!r}, flush=True)
print("runtime=" + str(pathlib.Path.cwd()), flush=True)
time.sleep({hold!r})
sys.exit({exit_code!r})
""",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            if windows:
                launcher = runtime / "Start-Personal.ps1"
                shutil.copy2(SCRIPTS / launcher.name, launcher)
                command = [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File", str(launcher)]
            else:
                launcher = runtime / "start-personal.sh"
                shutil.copy2(SCRIPTS / launcher.name, launcher)
                command = ["bash", str(launcher)]
            start = time.monotonic()
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=65,
                    env={**os.environ, "TMPDIR": str(scratch), "TERM": "dumb", "NO_COLOR": "1"},
                )
                elapsed = time.monotonic() - start
                self.assertIn("runtime=" + str(runtime), result.stdout)
                self.assertEqual(list(scratch.iterdir()), [], "Temporary console FIFO leaked")
                if not windows:
                    self.assertEqual(list((runtime / "var/tmp").iterdir()), [], "Local FIFO leaked")
                pid = int((runtime / "child.pid").read_text())
                try:
                    remaining = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
                except FileNotFoundError:
                    remaining = b""
                self.assertNotIn(os.fsencode(str(executable)), remaining, "Simulator still running")
                return result, elapsed
            finally:
                pid_file = runtime / "child.pid"
                if pid_file.exists():
                    pid = int(pid_file.read_text())
                    command_line = pathlib.Path(f"/proc/{pid}/cmdline")
                    if command_line.exists() and os.fsencode(str(executable)) in command_line.read_bytes():
                        os.kill(pid, signal.SIGKILL)

    def test_linux_keeps_input_open_and_cleans_fifo(self):
        result, _ = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("auto content install: off", result.stdout)

    def test_linux_preserves_child_failure(self):
        result, _ = self.run_launcher(exit_code=7)
        self.assertEqual(result.returncode, 7, result.stderr)

    def test_linux_rejects_existing_console_directory_without_deleting_it(self):
        with tempfile.TemporaryDirectory(prefix="personal collision ") as temporary:
            runtime = pathlib.Path(temporary)
            launcher = runtime / "start-personal.sh"
            shutil.copy2(SCRIPTS / launcher.name, launcher)
            result = subprocess.run(
                [
                    "bash", "-c",
                    'cd "$1"; mkdir -p "var/tmp/personal-console.$$"; '
                    'printf existing > "var/tmp/personal-console.$$/marker"; exec bash "$2"',
                    "fixture", str(runtime), str(launcher),
                ],
                capture_output=True, text=True, timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            markers = list((runtime / "var/tmp").glob("personal-console.*/marker"))
            self.assertEqual(len(markers), 1)
            self.assertEqual(markers[0].read_text(), "existing")

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_windows_keeps_input_open_and_acknowledges(self):
        result, _ = self.run_launcher(windows=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("auto content install: off", result.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_windows_preserves_child_failure(self):
        result, _ = self.run_launcher(windows=True, exit_code=7)
        self.assertEqual(result.returncode, 7, result.stderr)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_windows_rejects_missing_acknowledgement(self):
        result, _ = self.run_launcher(windows=True, acknowledged=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without confirming", result.stderr)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_windows_stops_before_automatic_sync_grace(self):
        result, elapsed = self.run_launcher(windows=True, acknowledged=False, hold=90)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not disable automatic content installation", result.stderr)
        self.assertGreaterEqual(elapsed, 45)
        self.assertLess(elapsed, 65)


if __name__ == "__main__":
    unittest.main()
