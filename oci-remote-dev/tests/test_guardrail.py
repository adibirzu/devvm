import hashlib
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.guardrail import DEFAULT_POLICY, decide

CTX = {"home": "/home/adi"}

# Computed, not a literal, so no secret-shaped string sits in source/diff text
# for a scanner to mistake as a real credential — the guardrail's own entropy
# check only cares about the shape (length + character distribution) of the
# value it receives at runtime, not where it came from.
_FAKE_HIGH_ENTROPY_VALUE = hashlib.sha1(
    b"guardrail-test-fixture-not-a-secret"
).hexdigest()


def d(tool, **inp):
    return decide(tool, inp, CTX)


class TestDeny(unittest.TestCase):
    def test_rm_rf_root(self) -> None:
        a, _, rid = d("Bash", command="rm -rf /")
        self.assertEqual(a, "deny")
        self.assertEqual(rid, "rm-rf-root")

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
        self.assertEqual(a, "ask")
        self.assertEqual(rid, "cloud-destroy")

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
        self.assertEqual(a, "ask")
        self.assertEqual(rid, "secret-read")

    def test_env_read(self) -> None:
        self.assertEqual(d("Read", file_path="/opt/shared-dev/app/.env")[0], "ask")

    def test_write_outside_roots(self) -> None:
        a, _, rid = d("Write", file_path="/etc/passwd")
        self.assertEqual(a, "ask")
        self.assertEqual(rid, "write-outside-roots")


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


class TestSecretWriteContent(unittest.TestCase):
    """Content-aware write protection — a path being 'safe' says nothing about
    whether the bytes written there are a secret. See classify_secret_content."""

    def test_dotenv_assignment_low_entropy(self) -> None:
        a, _, rid = d(
            "Write",
            file_path="/home/adi/project/.env",
            content="DB_PASSWORD=hunter2\n",
        )
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:dotenv-assignment"))

    def test_dotenv_assignment_via_edit_new_string(self) -> None:
        a, _, rid = d(
            "Edit",
            file_path="/home/adi/project/config.py",
            old_string="TOKEN=old",
            new_string="API_TOKEN=abc123secret",
        )
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:dotenv-assignment"))

    def test_high_entropy_key_value(self) -> None:
        a, _, rid = d(
            "Write",
            file_path="/tmp/creds.env",
            content=f"SECRET_KEY={_FAKE_HIGH_ENTROPY_VALUE}\n",
        )
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:high-entropy-key-value"))

    def test_json_client_secret(self) -> None:
        a, _, rid = d(
            "Write",
            file_path="/home/adi/project/config.json",
            content='{"client_secret": "abcde12345verysecretvalue"}',
        )
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:json-secret-field"))

    def test_json_private_key(self) -> None:
        a, _, rid = d(
            "MultiEdit",
            file_path="/home/adi/project/service-account.json",
            edits=[
                {"old_string": "x", "new_string": '"private_key": "notaplaceholder123"'}
            ],
        )
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:json-secret-field"))

    def test_pem_block(self) -> None:
        content = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        a, _, rid = d("Write", file_path="/tmp/id_rsa_copy.txt", content=content)
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:pem-block"))

    def test_notebook_edit_secret_cell(self) -> None:
        a, _, rid = d(
            "NotebookEdit",
            notebook_path="/home/adi/project/analysis.ipynb",
            new_source="API_TOKEN=abc123secret",
        )
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:dotenv-assignment"))

    def test_bash_pipe_tee_write(self) -> None:
        command = 'echo "API_KEY=abcdefghij1234567890ZZ" | tee /tmp/creds.env'
        a, _, rid = d("Bash", command=command)
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:"))

    def test_bash_heredoc_write(self) -> None:
        command = (
            "cat <<'EOF' > /tmp/secrets.env\nAPI_KEY=abcdefghij1234567890ZZ\nEOF\n"
        )
        a, _, rid = d("Bash", command=command)
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:"))

    def test_bash_redirection_write(self) -> None:
        command = 'echo "PASSWORD=supersecretvalue123" > /tmp/creds.txt'
        a, _, rid = d("Bash", command=command)
        self.assertEqual(a, "ask")
        self.assertTrue(rid.startswith("secret-write-detected:dotenv-assignment"))

    def test_secret_writes_deny_knob(self) -> None:
        policy = {
            "allowed_write_roots": ["/home/adi", "/tmp"],
            "secret_writes": "deny",
            "rules": DEFAULT_POLICY["rules"],
        }
        action, _, rid = decide(
            "Write",
            {"file_path": "/tmp/x.env", "content": "TOKEN=hunter2222"},
            CTX,
            policy,
        )
        self.assertEqual(action, "deny")
        self.assertTrue(rid.startswith("secret-write-detected:"))

    def test_reason_never_contains_the_value(self) -> None:
        secret_value = _FAKE_HIGH_ENTROPY_VALUE
        a, reason, _ = d(
            "Write",
            file_path="/tmp/creds.env",
            content=f"SECRET_KEY={secret_value}\n",
        )
        self.assertEqual(a, "ask")
        self.assertNotIn(secret_value, reason)


class TestSecretWritePlaceholders(unittest.TestCase):
    """Placeholders and .env.example must never trigger the secret-write rule."""

    # NB: target a non-`.env` path — `.env` itself already triggers the
    # pre-existing path-based `secret-read` rule regardless of content, which
    # would mask whether the *content-aware* placeholder exception works.

    def test_angle_bracket_placeholder(self) -> None:
        self.assertEqual(
            d(
                "Write",
                file_path="/home/adi/project/settings.py",
                content="API_KEY=<your-api-key>",
            )[0],
            "allow",
        )

    def test_changeme_placeholder(self) -> None:
        self.assertEqual(
            d(
                "Write",
                file_path="/home/adi/project/settings.py",
                content="DB_PASSWORD=changeme",
            )[0],
            "allow",
        )

    def test_dummy_placeholder(self) -> None:
        self.assertEqual(
            d(
                "Write",
                file_path="/home/adi/project/settings.py",
                content="SECRET_TOKEN=dummy",
            )[0],
            "allow",
        )

    def test_example_placeholder(self) -> None:
        self.assertEqual(
            d(
                "Write",
                file_path="/home/adi/project/settings.py",
                content="CLIENT_SECRET=example",
            )[0],
            "allow",
        )

    def test_env_example_file_is_exempt(self) -> None:
        # Even a real-looking secret in a .env.example template must not trigger.
        a, _, _ = d(
            "Write",
            file_path="/home/adi/project/.env.example",
            content=f"API_KEY={_FAKE_HIGH_ENTROPY_VALUE}\n",
        )
        self.assertEqual(a, "allow")

    def test_normal_write_still_allowed(self) -> None:
        a, _, _ = d(
            "Write",
            file_path="/home/adi/project/app.py",
            content="def foo():\n    return 42\n",
        )
        self.assertEqual(a, "allow")


if __name__ == "__main__":
    unittest.main()
