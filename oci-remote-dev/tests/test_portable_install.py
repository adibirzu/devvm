"""Regression fence for the portable install path.

Pure file/syntax/parse checks plus unit tests of the config compiler — no
network, no package manager, no Ansible run, so this is safe anywhere.
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ANSIBLE = ROOT / "ansible"
SCRIPTS = ROOT / "scripts"
INSTALL = ROOT / "install.sh"

sys.path.append(str(ROOT))

from scripts.deploy_config import (  # noqa: E402
    ConfigError,
    build_ansible_extra_vars,
    build_developers,
    build_inventory,
    parse_env_file,
    resolve_ssh_key,
)


class TestInstallScript(unittest.TestCase):
    def test_install_script_exists_and_is_executable(self) -> None:
        self.assertTrue(INSTALL.is_file(), "install.sh missing — it is the entry point")
        self.assertTrue(INSTALL.stat().st_mode & 0o111, "install.sh is not executable")

    def test_shell_syntax(self) -> None:
        for script in (INSTALL, SCRIPTS / "lib" / "distro.sh"):
            result = subprocess.run(
                ["bash", "-n", str(script)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, f"{script.name}: {result.stderr}")

    def test_help_documents_both_paths(self) -> None:
        out = subprocess.run(
            ["bash", str(INSTALL), "--help"], capture_output=True, text=True
        ).stdout
        for expected in ("--mode local", "--mode cloud", "--unattended", "DEVVM_"):
            self.assertIn(
                expected, out, f"install.sh --help does not mention {expected}"
            )

    def test_unknown_option_fails_loudly(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALL), "--nope"], capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown option", result.stderr)


class TestDistroDetection(unittest.TestCase):
    """The distro map is what keeps this off an Ubuntu-only assumption."""

    def _family(self, distro_id: str, id_like: str = "") -> str:
        script = f'. "{SCRIPTS / "lib" / "distro.sh"}"; distro_family "{distro_id}" "{id_like}"'
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True
        ).stdout.strip()

    def test_debian_family(self) -> None:
        for distro_id in ("ubuntu", "debian", "linuxmint"):
            self.assertEqual(self._family(distro_id), "debian", distro_id)

    def test_redhat_family(self) -> None:
        for distro_id in ("ol", "rhel", "rocky", "almalinux", "centos", "fedora"):
            self.assertEqual(self._family(distro_id), "rhel", distro_id)

    def test_family_from_id_like_when_id_is_unknown(self) -> None:
        self.assertEqual(self._family("mydistro", "rhel fedora"), "rhel")
        self.assertEqual(self._family("mydistro", "ubuntu debian"), "debian")

    def test_unknown_distro_is_reported_not_guessed(self) -> None:
        self.assertEqual(self._family("plan9"), "unknown")


class TestAnsibleAssets(unittest.TestCase):
    def _yaml_files(self):
        return sorted(ANSIBLE.rglob("*.yml"))

    def test_every_ansible_file_parses(self) -> None:
        # --syntax-check only follows statically imported files; include_tasks
        # targets would otherwise break at run time, not at check time.
        for path in self._yaml_files():
            with self.subTest(path=path.name):
                yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_os_family_vars_cover_the_same_keys(self) -> None:
        debian = yaml.safe_load((ANSIBLE / "vars" / "Debian.yml").read_text())
        redhat = yaml.safe_load((ANSIBLE / "vars" / "RedHat.yml").read_text())
        self.assertEqual(
            set(debian) ^ set(redhat),
            set(),
            "Debian.yml and RedHat.yml must define the same variables",
        )
        for name in (
            "os_base_packages",
            "os_desktop_packages",
            "os_sshd_service",
            "os_admin_sudo_group",
            "os_firewall_backend",
        ):
            self.assertIn(name, debian)

    def test_distro_specific_modules_stay_out_of_shared_tasks(self) -> None:
        """apt/yum modules belong in a family-guarded branch, not in shared tasks."""
        allowed = {"playbook.yml", "packages_tasks.yml"}
        for path in self._yaml_files():
            if path.name in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for module in (
                "ansible.builtin.apt:",
                "ansible.builtin.yum:",
                "ansible.builtin.dnf:",
                "ansible.builtin.apt_repository:",
            ):
                self.assertNotIn(
                    module,
                    text,
                    f"{path.name} uses {module} — use ansible.builtin.package with "
                    "os_* vars, or move it into a family-guarded block",
                )

    def test_collection_modules_are_declared_in_requirements(self) -> None:
        """ansible-core has none of these built in; an undeclared one breaks installs."""
        declared = {
            entry["name"]
            for entry in yaml.safe_load(
                (ANSIBLE / "requirements.yml").read_text(encoding="utf-8")
            )["collections"]
        }
        used = set()
        for path in self._yaml_files():
            used.update(
                re.findall(
                    r"^\s+((?:community|ansible)\.(?:general|posix))\.\w+:",
                    path.read_text(encoding="utf-8"),
                    re.MULTILINE,
                )
            )
        self.assertTrue(used, "expected the playbook to use collection modules")
        self.assertFalse(
            used - declared,
            f"used but not in ansible/requirements.yml: {used - declared}",
        )

    def test_shared_workspace_symlink_does_not_chown_through_the_link(self) -> None:
        # Without follow: false the loop chowns /opt/shared-dev itself, leaving it
        # owned by the last developer and locking everyone else out.
        text = (ANSIBLE / "playbook.yml").read_text(encoding="utf-8")
        block = text.split("Link shared workspace in homes", 1)[1][:400]
        self.assertIn("follow: false", block)

    def _playbook_vars(self) -> dict:
        play = yaml.safe_load((ANSIBLE / "playbook.yml").read_text(encoding="utf-8"))
        return play[0]["vars"]

    def test_playbook_install_flags_flow_through_deploy_config(self) -> None:
        """Every install_* toggle in the playbook must be compiled by
        deploy_config.build_ansible_extra_vars, and vice versa — otherwise one
        surface silently drifts from the other."""
        playbook_flags = {
            k for k in self._playbook_vars() if k.startswith("install_")
        }
        devs = build_developers({"ADMIN_USERNAME": "maria"}, require_ssh_key=False)
        extra = build_ansible_extra_vars({}, devs)
        config_flags = {k for k in extra if k.startswith("install_")}
        self.assertEqual(
            playbook_flags,
            config_flags,
            "install_* toggles differ between ansible/playbook.yml vars and "
            "scripts/deploy_config.py build_ansible_extra_vars",
        )

    def test_env_example_documents_every_install_flag(self) -> None:
        """The example configuration maps every install toggle to a compiler value."""
        example = parse_env_file(ROOT / ".env.example")
        devs = build_developers(example, require_ssh_key=False)
        extra = build_ansible_extra_vars(example, devs)
        defaults = build_ansible_extra_vars({}, devs)
        playbook_flags = {
            flag for flag in self._playbook_vars() if flag.startswith("install_")
        }
        for flag in playbook_flags:
            with self.subTest(flag=flag):
                self.assertIn(flag.upper(), example)
                self.assertEqual(extra[flag], defaults[flag])

    def test_agent_tooling_additions_default_off(self) -> None:
        """Tools added after the original product scope are opt-in: an existing
        deployment must not grow new global installs on its next run."""
        devs = build_developers({"ADMIN_USERNAME": "maria"}, require_ssh_key=False)
        extra = build_ansible_extra_vars({}, devs)
        for flag in (
            "install_opencode",
            "install_pi",
            "install_grok",
            "install_cline",
            "install_copilot_cli",
            "install_cursor_agent",
            "install_ollama",
        ):
            self.assertFalse(
                extra[flag],
                f"{flag} defaults to True — new tooling must be opt-in",
            )

class TestConfigCompiler(unittest.TestCase):
    def test_missing_key_path_yields_no_key(self) -> None:
        # The default ~/.ssh/id_rsa.pub is frequently absent; a direct install is
        # valid without a key, so the path must not leak through as "the key".
        self.assertEqual(resolve_ssh_key("/nonexistent/path/id_rsa.pub"), "")

    def test_literal_key_with_slashes_survives(self) -> None:
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1/NTE5AAAAI+slash user@host"
        self.assertEqual(resolve_ssh_key(key), key)

    def test_direct_install_does_not_require_a_key(self) -> None:
        devs = build_developers({"ADMIN_USERNAME": "maria"}, require_ssh_key=False)
        self.assertEqual(devs[0]["name"], "maria")
        self.assertEqual(devs[0]["ssh_key"], "")

    def test_cloud_install_requires_a_key(self) -> None:
        with self.assertRaises(ConfigError):
            build_developers({"ADMIN_USERNAME": "maria"}, require_ssh_key=True)

    def test_admin_override_wins_over_env(self) -> None:
        devs = build_developers(
            {"ADMIN_USERNAME": "devuser"}, require_ssh_key=False, admin_override="ci"
        )
        self.assertEqual(devs[0]["name"], "ci")

    def test_invalid_username_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            build_developers({"ADMIN_USERNAME": "Bad Name"}, require_ssh_key=False)

    def test_extra_vars_carry_the_host_level_toggles(self) -> None:
        devs = build_developers({"ADMIN_USERNAME": "maria"}, require_ssh_key=False)
        extra = build_ansible_extra_vars({}, devs)
        for key in (
            "install_desktop",
            "install_wireguard",
            "configure_firewall",
            "dashboard_port",
            "developers",
        ):
            self.assertIn(key, extra)
        # A direct install must not assume cloud-init already made the tunnel.
        self.assertFalse(extra["install_wireguard"])

    def test_overrides_are_applied(self) -> None:
        devs = build_developers({"ADMIN_USERNAME": "maria"}, require_ssh_key=False)
        extra = build_ansible_extra_vars({}, devs, {"wg_server_ip": "127.0.0.1"})
        self.assertEqual(extra["wg_server_ip"], "127.0.0.1")

    def test_developer_vars_include_the_ssh_key_for_account_creation(self) -> None:
        devs = build_developers(
            {"ADMIN_USERNAME": "maria", "SSH_PUBLIC_KEY_PATH": "ssh-rsa AAAA maria@x"},
            require_ssh_key=False,
        )
        extra = build_ansible_extra_vars({}, devs)
        self.assertEqual(extra["developers"][0]["ssh_key"], "ssh-rsa AAAA maria@x")

    def test_local_inventory_uses_a_local_connection(self) -> None:
        self.assertIn("ansible_connection=local", build_inventory("local"))

    def test_ssh_inventory_needs_a_host(self) -> None:
        with self.assertRaises(ConfigError):
            build_inventory("ssh", host="")

    def test_ssh_inventory_carries_user_and_key(self) -> None:
        inv = build_inventory("ssh", host="10.0.0.5", user="ubuntu", ssh_key="/k/id")
        self.assertIn("ansible_user=ubuntu", inv)
        self.assertIn("ansible_ssh_private_key_file=/k/id", inv)

    def test_extra_vars_are_json_serializable(self) -> None:
        devs = build_developers({"ADMIN_USERNAME": "maria"}, require_ssh_key=False)
        json.dumps(build_ansible_extra_vars({}, devs))


if __name__ == "__main__":
    unittest.main()
