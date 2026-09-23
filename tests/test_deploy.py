import os
import subprocess
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parents[1] / "ops"
DEPLOY = OPS / "deploy.sh"
IMAGE_PREFIX = "ghcr.io/queryplanner/photosarena@sha256:"


class DeployScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.deploy_dir = self.root / "host" / "photosarena"
        self.deploy_dir.mkdir(parents=True)
        (self.deploy_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.docker_log = self.root / "docker.log"
        self.active_image = self.root / "active-image"
        self.write_executable(
            "docker",
            "#!/bin/sh\n"
            "printf '%s|%s\\n' \"${PHOTOSARENA_IMAGE:-}\" \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
            "if [ \"$1\" = 'login' ]; then cat >/dev/null; exit 0; fi\n"
            "if [ \"$1\" = 'ps' ]; then printf '%s' \"${FAKE_PUBLISHED:-}\"; exit 0; fi\n"
            "if [ \"$1\" = 'compose' ]; then\n"
            "  case \" $* \" in\n"
            "    *' ps -q app '*) printf '%s' \"${FAKE_COMPOSE_RUNNING:-}\"; exit 0;;\n"
            "    *' app.backup '*) [ \"${FAKE_BACKUP_FAIL:-0}\" != '1' ]; exit $?;;\n"
            "    *' app.migrate '*) [ \"${FAKE_MIGRATE_FAIL:-0}\" != '1' ]; exit $?;;\n"
            "    *' up -d app '*) printf '%s' \"$PHOTOSARENA_IMAGE\" > \"$FAKE_ACTIVE_IMAGE\";\n"
            "      exit 0;;\n"
            "    *' pull app '*) exit 0;;\n"
            "    *' stop app '*) exit 0;;\n"
            "  esac\n"
            "fi\n"
            "exit 0\n",
        )
        self.write_executable(
            "ss",
            "#!/bin/sh\n"
            "if [ \"$3\" = 'sport = :8182' ]; then printf '%s' \"${FAKE_LISTENER:-}\"; fi\n",
        )
        self.write_executable("flock", "#!/bin/sh\nexit 0\n")
        self.write_executable(
            "curl",
            "#!/bin/sh\n"
            "active=$(cat \"$FAKE_ACTIVE_IMAGE\" 2>/dev/null || true)\n"
            "[ -n \"${FAKE_FAIL_IMAGE:-}\" ] && [ \"$active\" = \"$FAKE_FAIL_IMAGE\" ] && exit 22\n"
            "exit 0\n",
        )

        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
                "PHOTOSARENA_DEPLOY_DIR": str(self.deploy_dir),
                "PHOTOSARENA_IMAGE_STATE_FILE": str(self.deploy_dir / ".deployed-image"),
                "PHOTOSARENA_HEALTH_ATTEMPTS": "1",
                "PHOTOSARENA_HEALTH_INTERVAL": "0",
                "FAKE_DOCKER_LOG": str(self.docker_log),
                "FAKE_ACTIVE_IMAGE": str(self.active_image),
            }
        )

    def write_executable(self, name, content):
        path = self.bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def run_deploy(self, image=None):
        image = image or IMAGE_PREFIX + "a" * 64
        return subprocess.run(
            ["bash", str(DEPLOY), image],
            input="short-lived-ghcr-token\n",
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def log(self):
        if not self.docker_log.exists():
            return []
        return self.docker_log.read_text(encoding="utf-8").splitlines()

    def test_deploy_runs_backup_then_migration_then_start_and_saves_digest(self):
        image = IMAGE_PREFIX + "a" * 64

        result = self.run_deploy(image)

        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log()
        backup_index = next(i for i, command in enumerate(commands) if "app.backup" in command)
        migration_index = next(i for i, command in enumerate(commands) if "app.migrate" in command)
        start_index = next(i for i, command in enumerate(commands) if "up -d app" in command)
        self.assertLess(backup_index, migration_index)
        self.assertLess(migration_index, start_index)
        self.assertIn("PHOTOSARENA_BACKUP_DEST=/data/backups/photosarena-", commands[backup_index])
        self.assertEqual((self.deploy_dir / ".deployed-image").read_text().strip(), image)
        self.assertNotIn("short-lived-ghcr-token", result.stdout + result.stderr)
        self.assertNotIn("short-lived-ghcr-token", "\n".join(commands))

    def test_rejects_tag_or_malformed_digest_before_docker_access(self):
        result = self.run_deploy("ghcr.io/queryplanner/photosarena:latest")

        self.assertEqual(result.returncode, 64)
        self.assertEqual(self.log(), [])

    def test_rejects_port_conflict_from_another_compose_project(self):
        self.env["FAKE_PUBLISHED"] = "blacki-web|blacki\n"

        result = self.run_deploy()

        self.assertEqual(result.returncode, 1)
        self.assertIn("conflicts", result.stderr)
        self.assertFalse(any("app.migrate" in line for line in self.log()))

    def test_rejects_unmanaged_host_listener(self):
        self.env["FAKE_LISTENER"] = "LISTEN 0 128 127.0.0.1:8182 0.0.0.0:*"

        result = self.run_deploy()

        self.assertEqual(result.returncode, 1)
        self.assertIn("listening socket", result.stderr)
        self.assertFalse(any("app.migrate" in line for line in self.log()))

    def test_refuses_running_container_when_rollback_digest_is_missing(self):
        self.env["FAKE_COMPOSE_RUNNING"] = "running-container-id"

        result = self.run_deploy()

        self.assertEqual(result.returncode, 65)
        self.assertIn("saved image digest is missing", result.stderr)
        self.assertFalse(any("app.migrate" in line for line in self.log()))

    def test_backup_failure_leaves_app_and_image_state_untouched(self):
        old_image = IMAGE_PREFIX + "b" * 64
        state_file = self.deploy_dir / ".deployed-image"
        state_file.write_text(old_image + "\n", encoding="utf-8")
        self.env["FAKE_BACKUP_FAIL"] = "1"

        result = self.run_deploy()

        self.assertEqual(result.returncode, 1)
        self.assertIn("SQLite online backup failed", result.stderr)
        self.assertEqual(state_file.read_text().strip(), old_image)
        self.assertFalse(any(" stop app" in line for line in self.log()))
        self.assertFalse(any("app.migrate" in line for line in self.log()))

    def test_failed_health_check_restores_previous_digest_and_service(self):
        old_image = IMAGE_PREFIX + "b" * 64
        new_image = IMAGE_PREFIX + "a" * 64
        state_file = self.deploy_dir / ".deployed-image"
        state_file.write_text(old_image + "\n", encoding="utf-8")
        self.env["FAKE_PUBLISHED"] = "photosarena-app|photosarena\n"
        self.env["FAKE_LISTENER"] = "LISTEN 0 128 127.0.0.1:8182 0.0.0.0:*"
        self.env["FAKE_COMPOSE_RUNNING"] = "running-container-id"
        self.env["FAKE_FAIL_IMAGE"] = new_image

        result = self.run_deploy(new_image)

        self.assertEqual(result.returncode, 1)
        self.assertIn("restored previous image", result.stderr)
        self.assertEqual(state_file.read_text().strip(), old_image)
        self.assertEqual(self.active_image.read_text().strip(), old_image)
        commands = self.log()
        self.assertTrue(
            any(
                command.startswith(f"{old_image}|compose ") and "up -d app" in command
                for command in commands
            )
        )

    def test_failed_migration_restores_previous_service(self):
        old_image = IMAGE_PREFIX + "b" * 64
        state_file = self.deploy_dir / ".deployed-image"
        state_file.write_text(old_image + "\n", encoding="utf-8")
        self.env["FAKE_PUBLISHED"] = "photosarena-app|photosarena\n"
        self.env["FAKE_COMPOSE_RUNNING"] = "running-container-id"
        self.env["FAKE_MIGRATE_FAIL"] = "1"

        result = self.run_deploy()

        self.assertEqual(result.returncode, 1)
        self.assertIn("database migration failed", result.stderr)
        self.assertIn("restored previous image", result.stderr)
        self.assertEqual(state_file.read_text().strip(), old_image)
        self.assertEqual(self.active_image.read_text().strip(), old_image)

    def test_empty_token_stops_before_registry_login(self):
        result = subprocess.run(
            ["bash", str(DEPLOY), IMAGE_PREFIX + "a" * 64],
            input="\n",
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

        self.assertEqual(result.returncode, 65)
        self.assertIn("token was empty", result.stderr)
        self.assertFalse(any(" login ghcr.io " in line for line in self.log()))


if __name__ == "__main__":
    unittest.main()
