import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.project_status import (
    merge_projects,
    parse_branch_header,
    parse_last_commit,
    parse_status,
)


class TestBranchHeader(unittest.TestCase):
    def test_branch_with_ahead_behind(self) -> None:
        h = parse_branch_header("## main...origin/main [ahead 1, behind 2]")
        self.assertEqual(h["branch"], "main")
        self.assertEqual(h["upstream"], "origin/main")
        self.assertEqual(h["ahead"], 1)
        self.assertEqual(h["behind"], 2)

    def test_branch_no_upstream(self) -> None:
        h = parse_branch_header("## feature/x")
        self.assertEqual(h["branch"], "feature/x")
        self.assertIsNone(h["upstream"])
        self.assertEqual(h["ahead"], 0)

    def test_ahead_only(self) -> None:
        h = parse_branch_header("## main...origin/main [ahead 3]")
        self.assertEqual(h["ahead"], 3)
        self.assertEqual(h["behind"], 0)

    def test_detached(self) -> None:
        h = parse_branch_header("## HEAD (no branch)")
        self.assertTrue(h["detached"])


class TestStatus(unittest.TestCase):
    def test_clean_repo(self) -> None:
        s = parse_status("## main...origin/main")
        self.assertTrue(s["clean"])
        self.assertEqual(s["dirty"], 0)
        self.assertEqual(s["untracked"], 0)

    def test_dirty_and_untracked(self) -> None:
        s = parse_status("## main\n M a.py\nA  b.py\n?? c.txt\n?? d.txt")
        self.assertEqual(s["dirty"], 2)       # modified + added
        self.assertEqual(s["untracked"], 2)
        self.assertFalse(s["clean"])

    def test_empty(self) -> None:
        self.assertEqual(parse_status("")["branch"], "?")


class TestLastCommit(unittest.TestCase):
    def test_parse(self) -> None:
        c = parse_last_commit("abc1234\x00fix: the thing\x002 hours ago")
        self.assertEqual(c["hash"], "abc1234")
        self.assertEqual(c["subject"], "fix: the thing")
        self.assertEqual(c["when"], "2 hours ago")

    def test_empty(self) -> None:
        c = parse_last_commit("")
        self.assertEqual(c["hash"], "")


class TestMerge(unittest.TestCase):
    def test_joins_agents_and_git(self) -> None:
        sessions = {"/home/adi/web": [
            {"user": "adi", "agent": "claude", "project": "web", "state": "running"},
        ]}
        git = {"/home/adi/web": {"branch": "main", "clean": False, "dirty": 2}}
        out = merge_projects(sessions, git)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["project"], "web")
        self.assertEqual(out[0]["git"]["dirty"], 2)
        self.assertEqual(out[0]["active_agents"][0]["agent"], "claude")

    def test_repo_without_agents(self) -> None:
        out = merge_projects({}, {"/srv/lib": {"branch": "dev", "clean": True}})
        self.assertEqual(out[0]["project"], "lib")   # falls back to dir name
        self.assertEqual(out[0]["active_agents"], [])

    def test_agents_without_git(self) -> None:
        sessions = {"/tmp/scratch": [{"user": "x", "agent": "codex", "project": "scratch", "state": "dead"}]}
        out = merge_projects(sessions, {})
        self.assertIsNone(out[0]["git"])


if __name__ == "__main__":
    unittest.main()
