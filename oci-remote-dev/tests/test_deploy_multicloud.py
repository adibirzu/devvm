import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil

# Ensure scripts directory is in path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.deploy_multicloud import MultiCloudDeployer


class TestMultiCloudDeployer(unittest.TestCase):
    def setUp(self) -> None:
        # Create temp project structure
        self.temp_dir = Path(tempfile.mkdtemp())
        self.project_dir = self.temp_dir / "project"
        self.project_dir.mkdir()
        
        # Create directories
        (self.project_dir / "scripts").mkdir()
        (self.project_dir / "configs").mkdir()
        (self.project_dir / "templates").mkdir()
        
        # Create mock cloud-init.yaml.tpl
        tpl_content = (
            "#cloud-config\n"
            "hostname: {{VM_NAME}}\n"
            "admin: {{ADMIN_USERNAME}}\n"
            "{{USERS_CONFIG}}\n"
            "{{WG_PEERS_CONFIG}}\n"
            "devs: {{DEVELOPERS_LIST}}\n"
            "ports:\n"
            "{{DEVELOPERS_PORTS_MAP}}\n"
            "dash:\n"
            "{{DASHBOARD_HTML}}\n"
        )
        (self.project_dir / "templates" / "cloud-init.yaml.tpl").write_text(tpl_content, encoding="utf-8")
        
        # Create a mock SSH key
        self.ssh_dir = self.temp_dir / "ssh"
        self.ssh_dir.mkdir()
        (self.ssh_dir / "id_rsa.pub").write_text("ssh-rsa AAAAtestkey dev@local", encoding="utf-8")
        (self.ssh_dir / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----", encoding="utf-8")
        
        # Create a mock .env.local content
        self.env_content = (
            "CLOUD_PROVIDER=OCI\n"
            "ADMIN_USERNAME=testowner\n"
            "SSH_PUBLIC_KEY_PATH=" + str(self.ssh_dir / "id_rsa.pub") + "\n"
            "WG_PORT=51820\n"
            "WG_NETWORK=10.200.200.0/24\n"
            "WG_SERVER_IP=10.200.200.1\n"
            "WG_CLIENT_IP=10.200.200.2\n"
            "CODE_SERVER_PORT=8443\n"
            "MULTI_DEV_ENABLED=true\n"
            "DEV_2_NAME=alice\n"
            "DEV_2_SSH_KEY_PATH=" + str(self.ssh_dir / "id_rsa.pub") + "\n"
            "DEV_2_WG_IP=10.200.200.3\n"
            "DEV_2_CODE_SERVER_PORT=8444\n"
            "DEV_4_NAME=charlie\n"
            "DEV_4_SSH_KEY_PATH=" + str(self.ssh_dir / "id_rsa.pub") + "\n"
            "DEV_4_WG_IP=10.200.200.5\n"
            "DEV_4_CODE_SERVER_PORT=8446\n"
        )
        self.env_file = self.temp_dir / ".env.local"
        self.env_file.write_text(self.env_content, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("scripts.deploy_multicloud.run_cmd")
    def test_config_parsing(self, mock_run_cmd: MagicMock) -> None:
        """Verify that configuration parses all OCI and developer details correctly."""
        mock_run_cmd.return_value = "mock_key"
        
        args = MagicMock()
        args.env_file = str(self.env_file)
        
        deployer = MultiCloudDeployer(args)
        # Override project directories to temp structure
        deployer.project_dir = self.project_dir
        
        deployer.build_developers_list()
        
        # Assertions
        self.assertEqual(deployer.provider, "OCI")
        self.assertEqual(len(deployer.developers), 3)
        
        self.assertEqual(deployer.developers[0]["name"], "testowner")
        self.assertEqual(deployer.developers[0]["code_server_port"], 8443)
        self.assertEqual(deployer.developers[0]["wg_ip"], "10.200.200.2")
        
        self.assertEqual(deployer.developers[1]["name"], "alice")
        self.assertEqual(deployer.developers[1]["code_server_port"], 8444)
        self.assertEqual(deployer.developers[1]["wg_ip"], "10.200.200.3")

        self.assertEqual(deployer.developers[2]["name"], "charlie")
        self.assertEqual(deployer.developers[2]["code_server_port"], 8446)
        self.assertEqual(deployer.developers[2]["wg_ip"], "10.200.200.5")

    @patch("scripts.deploy_multicloud.run_cmd")
    def test_wireguard_key_generation(self, mock_run_cmd: MagicMock) -> None:
        """Verify that server and clients keys are generated securely."""
        mock_run_cmd.side_effect = [
            "server_priv", "server_pub",
            "dev1_priv", "dev1_pub",
            "dev2_priv", "dev2_pub",
            "dev4_priv", "dev4_pub",
        ]
        
        args = MagicMock()
        args.env_file = str(self.env_file)
        
        deployer = MultiCloudDeployer(args)
        deployer.project_dir = self.project_dir
        deployer.build_developers_list()
        
        deployer.generate_wireguard_keys()
        
        self.assertEqual(deployer.wg_server_private_key, "server_priv")
        self.assertEqual(deployer.wg_server_public_key, "server_pub")
        self.assertEqual(deployer.developers[0]["private_key"], "dev1_priv")
        self.assertEqual(deployer.developers[1]["private_key"], "dev2_priv")
        self.assertEqual(deployer.developers[2]["private_key"], "dev4_priv")

    @patch("scripts.deploy_multicloud.run_cmd")
    def test_cloud_init_rendering(self, mock_run_cmd: MagicMock) -> None:
        """Verify that cloud-init placeholders are dynamically compiled."""
        mock_run_cmd.return_value = "mock_key"
        
        args = MagicMock()
        args.env_file = str(self.env_file)
        
        deployer = MultiCloudDeployer(args)
        deployer.project_dir = self.project_dir
        deployer.build_developers_list()
        deployer.generate_wireguard_keys()
        
        deployer.generate_cloud_init()
        
        rendered_init = (self.project_dir / "configs" / "cloud-init.yaml").read_text(encoding="utf-8")
        
        # Check that variables were correctly substituted
        self.assertIn("hostname: remote-dev-server", rendered_init)
        self.assertIn("admin: testowner", rendered_init)
        self.assertIn("- name: testowner", rendered_init)
        self.assertIn("- name: alice", rendered_init)
        self.assertIn("- name: charlie", rendered_init)
        self.assertIn('devs: "testowner" "alice" "charlie"', rendered_init)

    def test_sdk_fallback_skips_duplicate_post_provisioning(self) -> None:
        deployer = MagicMock()
        deployer.args = MagicMock(dry_run=False, yes=True)
        deployer.provider = "OCI"
        deployer.post_provisioning_complete = True

        MultiCloudDeployer.execute(deployer)

        deployer.run_ansible_playbook.assert_not_called()

    def test_post_provisioning_failure_is_propagated(self) -> None:
        args = MagicMock()
        args.env_file = str(self.env_file)
        deployer = MultiCloudDeployer(args)
        deployer.project_dir = self.project_dir
        deployer.public_ip = "203.0.113.10"
        deployer.developers = []

        with patch("scripts.deploy_multicloud.shutil.which", return_value="/usr/bin/ansible-playbook"), patch(
            "scripts.deploy_multicloud.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ansible-playbook"]),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                deployer.run_ansible_playbook()


class TestWireGuardClientConfig(unittest.TestCase):
    """Regression fence for the macOS split-tunnel/DNS routing fix."""

    def _render(self, **overrides):
        from scripts.wg_config import render_wg_client_config
        base = dict(
            private_key="PRIV",
            address="10.200.200.3",
            server_public_key="PUB",
            endpoint="203.0.113.1:51820",
            wg_network="10.200.200.0/24",
        )
        base.update(overrides)
        return render_wg_client_config(**base)

    def test_split_tunnel_is_default(self) -> None:
        cfg = self._render()
        self.assertIn("AllowedIPs = 10.200.200.0/24", cfg)
        self.assertNotIn("0.0.0.0/0", cfg)

    def test_no_dns_line_by_default(self) -> None:
        # A DNS line in a split-tunnel config breaks macOS resolution once up.
        cfg = self._render()
        self.assertNotIn("DNS =", cfg)

    def test_full_tunnel_routes_everything(self) -> None:
        cfg = self._render(full_tunnel=True)
        self.assertIn("AllowedIPs = 0.0.0.0/0, ::/0", cfg)

    def test_dns_emitted_only_when_set(self) -> None:
        cfg = self._render(full_tunnel=True, dns="1.1.1.1, 8.8.8.8")
        self.assertIn("DNS = 1.1.1.1, 8.8.8.8", cfg)

    def test_blank_dns_is_ignored(self) -> None:
        cfg = self._render(dns="   ")
        self.assertNotIn("DNS =", cfg)

    def test_required_peer_fields_present(self) -> None:
        cfg = self._render()
        self.assertIn("PrivateKey = PRIV", cfg)
        self.assertIn("PublicKey = PUB", cfg)
        self.assertIn("Endpoint = 203.0.113.1:51820", cfg)
        self.assertIn("PersistentKeepalive = 25", cfg)


if __name__ == "__main__":
    unittest.main()
