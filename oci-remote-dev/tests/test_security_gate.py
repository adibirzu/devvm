import unittest
from pathlib import Path
import re
import sys

# Ensure scripts directory is in path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.security_gate import scan_text, is_ignored, PATTERNS


class TestSecurityGate(unittest.TestCase):
    def test_ocid_detection(self) -> None:
        """Verify that various OCI OCIDs are caught by the scanner.

        Fixtures are SYNTHETIC OCIDs (real-format, fake body) so this published test
        leaks nothing — it only needs to match the OCID regex shape.
        """
        violations = scan_text(
            'tenancy_ocid = "ocid1.tenancy.oc1..aaaaaaaaexamplesynthetictenancyfixture0001"',
            "test_file",
        )
        self.assertTrue(len(violations) > 0)
        self.assertIn("Restricted OCI Resource OCID", violations[0][1])

        violations_comp = scan_text(
            'compartment_id = "ocid1.compartment.oc1..aaaaaaaaexamplesyntheticcompartment0001"',
            "test_file",
        )
        self.assertTrue(len(violations_comp) > 0)

    def test_restricted_ip_detection(self) -> None:
        """Verify that the Oracle-published restricted IP ranges are flagged."""
        violations = scan_text('endpoint = "130.61.0.0:51820"', "test_file")
        self.assertTrue(len(violations) > 0)
        self.assertIn("Restricted Infrastructure Public IP range", violations[0][1])

    def test_tenancy_namespace_detection(self) -> None:
        """Verify restricted namespaces are caught — using an INJECTED synthetic one,
        so no real tenancy namespace appears in this committed/published test."""
        from scripts.security_gate import build_patterns
        violations = scan_text(
            'namespace = "examplesyntheticns0001"',
            "test_file",
            patterns=build_patterns(["examplesyntheticns0001"]),
        )
        self.assertTrue(len(violations) > 0)
        self.assertIn("Restricted Tenancy Namespace", violations[0][1])

    def test_namespace_check_skipped_when_none_configured(self) -> None:
        """With no namespaces loaded, the namespace check is simply absent (no crash)."""
        from scripts.security_gate import build_patterns
        labels = [lbl for _, lbl in build_patterns([])]
        self.assertNotIn("Restricted Tenancy Namespace", labels)

    def test_private_key_detection(self) -> None:
        """Verify that raw private keys trigger the scanner."""
        private_key_block = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtcn\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        violations = scan_text(private_key_block, "test_file")
        self.assertTrue(len(violations) > 0)
        self.assertIn("Private Key Block", violations[0][1])

    def test_ignore_rules(self) -> None:
        """Verify that allowed file paths are correctly ignored."""
        self.assertTrue(is_ignored("configs/wireguard/client.conf"))
        self.assertTrue(is_ignored(".env.local"))
        self.assertTrue(is_ignored("README.md"))
        self.assertTrue(is_ignored("scripts/security_gate.py"))
        
        # Staged checks on allowed names
        self.assertFalse(is_ignored("scripts/deploy_multicloud.py"))
        self.assertFalse(is_ignored("templates/cloud-init.yaml.tpl"))


if __name__ == "__main__":
    unittest.main()
