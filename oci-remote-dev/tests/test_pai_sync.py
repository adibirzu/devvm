import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.pai_sync import (
    SENSITIVE_SUBDIRS,
    build_age_decrypt_cmd,
    build_age_encrypt_cmd,
    encrypted_target,
    is_within,
    parse_recipients,
    require_recipients,
    sensitive_paths,
)


class TestSensitivePaths(unittest.TestCase):
    def test_sensitive_subdirs_are_memory_and_user(self) -> None:
        self.assertEqual(SENSITIVE_SUBDIRS, ("MEMORY", "USER"))

    def test_sensitive_paths_under_base(self) -> None:
        base = Path("/home/adi/.claude/PAI")
        paths = sensitive_paths(base)
        self.assertEqual([p.name for p in paths], ["MEMORY", "USER"])
        for p in paths:
            self.assertTrue(is_within(p, base))


class TestRecipients(unittest.TestCase):
    def test_parse_comma_space_newline(self) -> None:
        raw = "age1aaa, age1bbb\nage1ccc age1ddd"
        self.assertEqual(parse_recipients(raw), ["age1aaa", "age1bbb", "age1ccc", "age1ddd"])

    def test_parse_empty(self) -> None:
        self.assertEqual(parse_recipients(""), [])

    def test_require_recipients_refuses_empty(self) -> None:
        # ISC-17: never silently fall back to plaintext.
        with self.assertRaises(SystemExit):
            require_recipients([])

    def test_require_recipients_ok_when_present(self) -> None:
        require_recipients(["age1aaa"])  # should not raise


class TestEncryptedTarget(unittest.TestCase):
    def test_encrypted_target_inside_repo(self) -> None:
        base = Path("/home/adi/.claude/PAI")
        repo = Path("/home/adi/.pai-memory")
        out = encrypted_target(base / "MEMORY", repo, base)
        # ISC-16: encrypted output lives inside the repo, never beside the plaintext.
        self.assertTrue(is_within(out, repo))
        self.assertFalse(is_within(out, base))
        self.assertEqual(out.name, "MEMORY.tar.age")


class TestIsWithin(unittest.TestCase):
    def test_within_true(self) -> None:
        self.assertTrue(is_within(Path("/a/b/c"), Path("/a")))

    def test_within_false_for_sibling(self) -> None:
        self.assertFalse(is_within(Path("/a/b"), Path("/x")))

    def test_within_false_for_escape(self) -> None:
        self.assertFalse(is_within(Path("/a/../etc/passwd"), Path("/a")))


class TestAgeCommands(unittest.TestCase):
    def test_encrypt_cmd_includes_all_recipients(self) -> None:
        cmd = build_age_encrypt_cmd(["age1aaa", "age1bbb"], Path("/r/encrypted/MEMORY.tar.age"))
        self.assertEqual(cmd[0], "age")
        self.assertIn("age1aaa", cmd)
        self.assertIn("age1bbb", cmd)
        self.assertIn("-o", cmd)
        self.assertIn("/r/encrypted/MEMORY.tar.age", cmd)

    def test_decrypt_cmd_uses_identity(self) -> None:
        cmd = build_age_decrypt_cmd(Path("/home/adi/.config/pai/age.key"), Path("/r/encrypted/MEMORY.tar.age"))
        self.assertEqual(cmd[:3], ["age", "-d", "-i"])
        self.assertIn("/home/adi/.config/pai/age.key", cmd)


if __name__ == "__main__":
    unittest.main()
