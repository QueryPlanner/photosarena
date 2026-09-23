import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parents[1] / "ops"
ENTRYPOINT = OPS / "remote-entrypoint.sh"
IMAGE = "ghcr.io/queryplanner/photosarena@sha256:" + "a" * 64


class RemoteEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ops = self.root / "ops"
        self.ops.mkdir()
        shutil.copy2(ENTRYPOINT, self.ops / "remote-entrypoint.sh")

        self.arguments = self.root / "arguments.txt"
        self.stdin = self.root / "stdin.txt"
        deploy = self.ops / "deploy.sh"
        deploy.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" > \"$CAPTURE_ARGUMENTS\"\n"
            "cat > \"$CAPTURE_STDIN\"\n",
            encoding="utf-8",
        )
        deploy.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            {
                "CAPTURE_ARGUMENTS": str(self.arguments),
                "CAPTURE_STDIN": str(self.stdin),
            }
        )

    def run_entrypoint(self, command):
        env = self.env.copy()
        if command is None:
            env.pop("SSH_ORIGINAL_COMMAND", None)
        else:
            env["SSH_ORIGINAL_COMMAND"] = command
        return subprocess.run(
            ["bash", str(self.ops / "remote-entrypoint.sh")],
            input="short-lived-ghcr-token\n",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_accepts_digest_and_forwards_arguments_and_stdin_separately(self):
        result = self.run_entrypoint(f"deploy {IMAGE} queryplanner")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments.read_text().splitlines(), [IMAGE, "queryplanner"])
        self.assertEqual(self.stdin.read_text(), "short-lived-ghcr-token\n")
        self.assertNotIn("short-lived-ghcr-token", result.stdout + result.stderr)

    def test_defaults_username_when_omitted(self):
        result = self.run_entrypoint(f"deploy {IMAGE}")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments.read_text().splitlines(), [IMAGE, "queryplanner"])

    def test_rejects_shell_commands_tags_and_extra_arguments(self):
        invalid_commands = [
            "id",
            f"deploy {IMAGE}; id",
            "deploy ghcr.io/queryplanner/photosarena:latest",
            f"deploy {IMAGE} queryplanner extra",
        ]

        for command in invalid_commands:
            with self.subTest(command=command):
                result = self.run_entrypoint(command)
                self.assertEqual(result.returncode, 64)
                self.assertFalse(self.arguments.exists())

    def test_rejects_interactive_connection_without_command(self):
        result = self.run_entrypoint(None)

        self.assertEqual(result.returncode, 64)
        self.assertFalse(self.arguments.exists())


if __name__ == "__main__":
    unittest.main()
