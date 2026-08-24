"""Behavioral coverage for the OCI SDK deployment completion path."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch


# The SDK is an optional deployment dependency; these tests exercise the local
# post-provisioning boundary without requiring OCI credentials or its package.
sys.modules.setdefault("oci", types.ModuleType("oci"))
sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.deploy_sdk import SDKDeployer


class TestSDKPostProvisioning(unittest.TestCase):
    def test_execute_runs_post_provisioning_before_summary(self) -> None:
        deployer = MagicMock()
        deployer.args = MagicMock(dry_run=False, yes=True)

        SDKDeployer.execute(deployer)

        deployer.run_ansible_playbook.assert_called_once_with()
        self.assertLess(
            deployer.method_calls.index(unittest.mock.call.verify_ssh()),
            deployer.method_calls.index(unittest.mock.call.run_ansible_playbook()),
        )

    def test_post_provisioning_compiles_toolchain_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "configs").mkdir()
            deployer = MagicMock()
            deployer.project_dir = project_dir
            deployer.public_ip = "203.0.113.10"
            deployer.env = {
                "INSTALL_OPENCODE": "true",
                "INSTALL_OLLAMA": "true",
                "OLLAMA_MODELS": "qwen3-coder:30b",
            }
            deployer.runtime.admin_username = "devuser"
            deployer.runtime.ssh_private_key_path = Path("/tmp/id_rsa")
            deployer.runtime.developers = [
                {
                    "name": "devuser",
                    "ssh_key": "ssh-ed25519 AAAA devuser@test",
                    "wg_ip": "10.200.200.2",
                    "code_server_port": 8443,
                }
            ]

            with patch("scripts.deploy_sdk.shutil.which", return_value="/usr/bin/ansible-playbook"), patch(
                "scripts.deploy_sdk.subprocess.run"
            ) as run:
                SDKDeployer.run_ansible_playbook(deployer)

            extra_vars = json.loads((project_dir / "configs" / "ansible_vars.json").read_text())
            self.assertTrue(extra_vars["install_opencode"])
            self.assertTrue(extra_vars["install_ollama"])
            self.assertEqual(extra_vars["ollama_models"], "qwen3-coder:30b")
            self.assertFalse(extra_vars["install_wireguard"])
            run.assert_called_once()

    def test_post_provisioning_failure_is_propagated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deployer = MagicMock()
            deployer.project_dir = Path(tmp)
            deployer.public_ip = "203.0.113.10"
            deployer.env = {}
            deployer.runtime.admin_username = "devuser"
            deployer.runtime.ssh_private_key_path = Path("/tmp/id_rsa")
            deployer.runtime.developers = []

            with patch("scripts.deploy_sdk.shutil.which", return_value="/usr/bin/ansible-playbook"), patch(
                "scripts.deploy_sdk.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, ["ansible-playbook"]),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    SDKDeployer.run_ansible_playbook(deployer)


if __name__ == "__main__":
    unittest.main()
