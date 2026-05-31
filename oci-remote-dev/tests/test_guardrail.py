import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.guardrail import decide

CTX = {"home": "/home/adi"}


def d(tool, **inp):
    return decide(tool, inp, CTX)


class TestDeny(unittest.TestCase):
    def test_rm_rf_root(self) -> None:
        a, _, rid = d("Bash", command="rm -rf /")
        self.assertEqual(a, "deny"); self.assertEqual(rid, "rm-rf-root")

    def test_rm_rf_home(self) -> None:
        self.assertEqual(d("Bash", command="rm -rf ~")[0], "deny")

    def test_no_preserve_root(self) -> None:
        self.assertEqual(d("Bash", command="rm -rf --no-preserve-root /")[0], "deny")

    def test_disk_dd(self) -> None:
        self.assertEqual(d("Bash", command="dd if=/dev/zero of=/dev/sda")[0], "deny")

    def test_mkfs(self) -> None:
        self.assertEqual(d("Bash", command="mkfs.ext4 /dev/sdb1")[0], "deny")

    def test_shutdown(self) -> None:
        self.assertEqual(d("Bash", command="sudo shutdown -h now")[0], "deny")

    def test_force_push_main(self) -> None:
        self.assertEqual(d("Bash", command="git push --force origin main")[0], "deny")


class TestAsk(unittest.TestCase):
    def test_oci_delete(self) -> None:
        a, _, rid = d("Bash", command="oci compute instance terminate --instance-id x")
        self.assertEqual(a, "ask"); self.assertEqual(rid, "cloud-destroy")

    def test_kubectl_delete(self) -> None:
        self.assertEqual(d("Bash", command="kubectl delete pod foo")[0], "ask")

    def test_terraform_destroy(self) -> None:
        self.assertEqual(d("Bash", command="terraform destroy -auto-approve")[0], "ask")

    def test_drop_table(self) -> None:
        self.assertEqual(d("Bash", command="psql -c 'DROP TABLE users'")[0], "ask")

    def test_system_install(self) -> None:
        self.assertEqual(d("Bash", command="sudo apt install nginx")[0], "ask")

    def test_secret_read_pem(self) -> None:
        a, _, rid = d("Read", file_path="/home/adi/.ssh/id_ed25519")
        self.assertEqual(a, "ask"); self.assertEqual(rid, "secret-read")

    def test_env_read(self) -> None:
        self.assertEqual(d("Read", file_path="/opt/shared-dev/app/.env")[0], "ask")

    def test_write_outside_roots(self) -> None:
        a, _, rid = d("Write", file_path="/etc/passwd")
        self.assertEqual(a, "ask"); self.assertEqual(rid, "write-outside-roots")


class TestAllow(unittest.TestCase):
    def test_normal_bash(self) -> None:
        self.assertEqual(d("Bash", command="ls -la && git status")[0], "allow")

    def test_normal_git_push(self) -> None:
        # non-force push is fine
        self.assertEqual(d("Bash", command="git push origin feature/x")[0], "allow")

    def test_write_in_home(self) -> None:
        self.assertEqual(d("Write", file_path="/home/adi/project/app.py")[0], "allow")

    def test_write_in_shared(self) -> None:
        self.assertEqual(d("Write", file_path="/opt/shared-dev/x/y.txt")[0], "allow")

    def test_read_normal_file(self) -> None:
        self.assertEqual(d("Read", file_path="/home/adi/notes.md")[0], "allow")

    def test_env_example_is_fine(self) -> None:
        # .env.example must NOT trip the secret rule (only bare .env)
        self.assertEqual(d("Read", file_path="/home/adi/app/.env.example")[0], "allow")

    def test_rm_specific_file_ok(self) -> None:
        self.assertEqual(d("Bash", command="rm build/output.tmp")[0], "allow")


class TestFirstMatchWins(unittest.TestCase):
    def test_deny_beats_ask(self) -> None:
        # a command that is catastrophic should deny, not just ask
        self.assertEqual(d("Bash", command="rm -rf / && oci delete")[0], "deny")


if __name__ == "__main__":
    unittest.main()
