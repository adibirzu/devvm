"""Regression coverage for adi's checked-in Git identity."""

import json
import unittest
from pathlib import Path


class TestAdiGitIdentity(unittest.TestCase):
    def test_adi_git_identity(self) -> None:
        config_path = (
            Path(__file__).resolve().parent.parent / "configs" / "ansible_vars.json"
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        adi = next(
            developer
            for developer in config["developers"]
            if developer["name"] == "adi"
        )

        self.assertEqual(adi["git_name"], "Adrian Birzu")
        self.assertEqual(adi["git_email"], "60743810+adibirzu@users.noreply.github.com")
        self.assertEqual(adi["github_user"], "adibirzu")
