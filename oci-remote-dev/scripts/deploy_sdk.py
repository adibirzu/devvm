#!/usr/bin/env python3
"""
OCI Remote Development Server deployment via OCI Python SDK.

This replaces the previous OCI CLI-centric provisioning path with SDK calls for:
 - tenancy/profile validation
 - compartment and AD resolution
 - network setup (VCN/subnet/IGW/route/security-list)
 - instance launch and wait
 - deployment artifacts (cloud-init, wireguard config, deployment info)
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import oci

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.wg_config import render_wg_client_config


HTML_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OCI Developer Collaboration Hub</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0d0f14;
            --card-bg: rgba(22, 28, 45, 0.4);
            --border-color: rgba(255, 255, 255, 0.08);
            --accent-glow: linear-gradient(135deg, #38bdf8, #818cf8);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --status-green: #10b981;
        }
        
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        
        body {
            background-color: var(--bg-color);
            color: var(--text-primary);
            font-family: 'Outfit', sans-serif;
            overflow-x: hidden;
            background-image: radial-gradient(circle at 10% 20%, rgba(56, 189, 248, 0.05) 0%, transparent 40%),
                              radial-gradient(circle at 90% 80%, rgba(129, 140, 248, 0.05) 0%, transparent 40%);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        
        header {
            padding: 2.5rem 2rem;
            max-width: 1200px;
            margin: 0 auto;
            width: 100%;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
        }
        
        .logo {
            font-size: 1.5rem;
            font-weight: 800;
            background: var(--accent-glow);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .status-badge {
            background: rgba(16, 185, 129, 0.1);
            color: var(--status-green);
            padding: 0.5rem 1rem;
            border-radius: 100px;
            font-size: 0.85rem;
            font-weight: 600;
            border: 1px solid rgba(16, 185, 129, 0.2);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background-color: var(--status-green);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 10px var(--status-green);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { transform: scale(0.9); opacity: 0.6; }
            50% { transform: scale(1.1); opacity: 1; }
            100% { transform: scale(0.9); opacity: 0.6; }
        }

        main {
            flex: 1;
            max-width: 1200px;
            margin: 0 auto;
            width: 100%;
            padding: 3rem 2rem;
            display: grid;
            grid-template-columns: 1fr;
            gap: 3rem;
        }

        .hero {
            text-align: center;
            margin-bottom: 2rem;
        }

        .hero h1 {
            font-size: 3rem;
            font-weight: 800;
            letter-spacing: -1px;
            margin-bottom: 1rem;
            background: linear-gradient(to right, #f8fafc, #cbd5e1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .hero p {
            color: var(--text-secondary);
            font-size: 1.2rem;
            max-width: 600px;
            margin: 0 auto;
            line-height: 1.6;
        }

        .section-title {
            font-size: 1.5rem;
            font-weight: 600;
            margin-bottom: 1.5rem;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .section-title::after {
            content: '';
            flex: 1;
            height: 1px;
            background: var(--border-color);
        }

        .grid-devs {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 2rem;
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 2rem;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }

        .card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: linear-gradient(180deg, rgba(56, 189, 248, 0.05) 0%, transparent 100%);
            opacity: 0;
            transition: opacity 0.3s ease;
        }

        .card:hover {
            transform: translateY(-5px);
            border-color: rgba(99, 102, 241, 0.3);
            box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
        }

        .card:hover::before {
            opacity: 1;
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            position: relative;
            z-index: 1;
        }

        .dev-info h3 {
            font-size: 1.35rem;
            font-weight: 600;
            color: #f8fafc;
        }

        .dev-info span {
            font-size: 0.85rem;
            color: var(--text-secondary);
            font-family: 'Fira Code', monospace;
        }

        .dev-badge {
            background: rgba(99, 102, 241, 0.15);
            color: #818cf8;
            border: 1px solid rgba(99, 102, 241, 0.25);
            padding: 0.35rem 0.75rem;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 600;
        }

        .card-body {
            position: relative;
            z-index: 1;
            margin-bottom: 2rem;
        }

        .info-row {
            display: flex;
            justify-content: space-between;
            padding: 0.75rem 0;
            border-bottom: 1px dashed rgba(255, 255, 255, 0.04);
            font-size: 0.95rem;
        }

        .info-row:last-child {
            border-bottom: none;
        }

        .info-label {
            color: var(--text-secondary);
        }

        .info-value {
            font-family: 'Fira Code', monospace;
            font-size: 0.9rem;
        }

        .btn {
            display: block;
            width: 100%;
            text-align: center;
            background: var(--accent-glow);
            color: #0f172a;
            padding: 0.85rem 1.5rem;
            border-radius: 10px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.2s ease;
            position: relative;
            z-index: 1;
            box-shadow: 0 4px 12px rgba(99, 102, 241, 0.2);
        }

        .btn:hover {
            transform: scale(1.02);
            filter: brightness(1.1);
            box-shadow: 0 6px 18px rgba(99, 102, 241, 0.35);
        }

        .pair-section {
            background: linear-gradient(135deg, rgba(22, 28, 45, 0.6) 0%, rgba(13, 15, 20, 0.8) 100%);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 3rem;
            margin-top: 1rem;
        }

        .pair-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 3rem;
            align-items: center;
        }

        @media (max-width: 900px) {
            .pair-grid {
                grid-template-columns: 1fr;
                gap: 2rem;
            }
        }

        .pair-text h2 {
            font-size: 2rem;
            font-weight: 800;
            margin-bottom: 1rem;
            letter-spacing: -0.5px;
        }

        .pair-text p {
            color: var(--text-secondary);
            line-height: 1.6;
            margin-bottom: 1.5rem;
        }

        .code-block {
            background: rgba(13, 15, 20, 0.9);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            font-family: 'Fira Code', monospace;
            font-size: 0.9rem;
            overflow-x: auto;
            position: relative;
        }

        .code-title {
            color: #38bdf8;
            margin-bottom: 0.5rem;
            font-weight: 500;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .code-content {
            color: #e2e8f0;
            line-height: 1.5;
        }

        footer {
            padding: 3rem 2rem;
            text-align: center;
            color: var(--text-secondary);
            font-size: 0.9rem;
            border-top: 1px solid var(--border-color);
            margin-top: 5rem;
            background: rgba(13, 15, 20, 0.5);
        }

        footer a {
            color: #818cf8;
            text-decoration: none;
        }
    </style>
</head>
<body>
    <header>
        <div class="logo">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M12 2L2 22H22L12 2Z" stroke="url(#logo-grad)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                <defs>
                    <linearGradient id="logo-grad" x1="2" y1="22" x2="22" y2="2">
                        <stop stop-color="#38bdf8"/>
                        <stop offset="1" stop-color="#818cf8"/>
                    </linearGradient>
                </defs>
            </svg>
            OCI COLLABORATION HUB
        </div>
        <div class="status-badge">
            <span class="status-dot"></span>
            ACTIVE TENANCY
        </div>
    </header>

    <main>
        <div class="hero">
            <h1>Unified Developer Workspace</h1>
            <p>Welcome to your high-performance remote development environment running on Oracle Cloud Infrastructure Standard E6 shape.</p>
        </div>

        <div>
            <h2 class="section-title">Developer Workspace Portals</h2>
            <div class="grid-devs">
                {{DASHBOARD_CARDS}}
            </div>
        </div>

        <div class="pair-section">
            <div class="pair-grid">
                <div class="pair-text">
                    <h2>AI-Assisted Pair Programming</h2>
                    <p>Every developer has access to <strong>Claude Code CLI</strong>, OpenAI's <strong>Codex</strong>, and Google's <strong>Gemini CLI</strong>. You can collaborate in real-time by sharing the exact same Claude Code session.</p>
                    <p>Using the custom <code>pair-claude</code> utility, multiple developers can securely attach to a single running agent session to inspect, pair-program, or verify changes together!</p>
                </div>
                <div>
                    <div class="code-block">
                        <div class="code-title">Claude Pairing Commands</div>
                        <div class="code-content">
                            <span style="color: #64748b;"># Developer A starts the shared Claude session</span><br>
                            <span style="color: #34d399;">$</span> pair-claude start<br><br>
                            <span style="color: #64748b;"># Developer B joins the active session instantly</span><br>
                            <span style="color: #34d399;">$</span> pair-claude join<br><br>
                            <span style="color: #64748b;"># Inspect paired session status</span><br>
                            <span style="color: #34d399;">$</span> pair-claude status
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <footer>
        <p>Deployed on OCI Standard E6 Flex (4 OCPUs, 32GB RAM) &bull; Managed via Python SDK</p>
    </footer>
</body>
</html>"""


GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
BLUE = "\033[0;34m"
CYAN = "\033[0;36m"
NC = "\033[0m"


def log(msg: str) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{GREEN}[{ts}]{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARNING]{NC} {msg}")


def info(msg: str) -> None:
    print(f"{CYAN}[INFO]{NC} {msg}")


def fail(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}")
    raise RuntimeError(msg)


def parse_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def env_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def run_cmd(args: List[str], check: bool = True, capture: bool = True) -> str:
    proc = subprocess.run(
        args,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    return (proc.stdout or "").strip()


def run_cmd_no_raise(args: List[str]) -> Tuple[int, str, str]:
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def all_results(call: Any, **kwargs: Any) -> List[Any]:
    return oci.pagination.list_call_get_all_results(call, **kwargs).data


def resolve_ssh_key(val: str) -> str:
    if not val:
        return ""
    # Check if it looks like a path and expansion works
    if val.startswith("~") or "/" in val or "\\" in val:
        p = Path(val.replace("~", str(Path.home())))
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8").strip()
    # Otherwise treat as raw string
    return val.strip()


@dataclass
class RuntimeConfig:
    project_dir: Path
    script_dir: Path
    env_file: Path
    profile: str
    region: str
    tenancy_ocid: str
    compartment_name: str
    compartment_ocid: str
    vm_name: str
    vm_shape: str
    vm_ocpus: float
    vm_memory_gb: float
    vm_boot_volume_gb: int
    ubuntu_version: str
    availability_domain_num: int
    existing_vcn_ocid: str
    existing_subnet_ocid: str
    vcn_name: str
    vcn_cidr: str
    subnet_cidr: str
    ssh_public_key_path: Path
    ssh_private_key_path: Path
    wg_port: int
    wg_network: str
    wg_server_ip: str
    wg_client_ip: str
    rdp_port: int
    vnc_port: int
    code_server_port: int
    install_claude_code: bool
    install_codex: bool
    install_gemini: bool
    install_code_server: bool
    install_cursor: bool
    node_version: str
    firewall_strict: bool
    admin_username: str
    multi_dev_enabled: bool
    developers: List[Dict[str, Any]]


class SDKDeployer:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.script_dir = Path(__file__).resolve().parent
        self.project_dir = self.script_dir.parent
        self.env_file = self._resolve_env_file(args.env_file)
        self.env = parse_env_file(self.env_file)
        self.runtime = self._build_runtime_config()

        self.oci_config = oci.config.from_file(
            file_location=str(Path(args.config_file).expanduser()),
            profile_name=self.runtime.profile,
        )
        self.oci_config["region"] = self.runtime.region

        self.identity = oci.identity.IdentityClient(self.oci_config)
        self.compute = oci.core.ComputeClient(self.oci_config)
        self.network = oci.core.VirtualNetworkClient(self.oci_config)

        self.availability_domain_name = ""
        self.image_ocid = ""
        self.compartment_ocid = self.runtime.compartment_ocid
        self.vcn_ocid = ""
        self.subnet_ocid = ""
        self.instance_ocid = ""
        self.public_ip = ""
        self.wg_server_private_key = ""
        self.wg_server_public_key = ""
        self.wg_client_private_key = ""
        self.wg_client_public_key = ""
        self.existing_instance_id = ""
        self.existing_instance_state = ""

    def _resolve_env_file(self, raw_path: str) -> Path:
        requested = Path(raw_path).expanduser()
        if not requested.is_absolute():
            requested = self.project_dir / requested
        if requested.exists() or Path(raw_path).name != ".env":
            return requested
        legacy = self.project_dir / ".env.local"
        if legacy.exists():
            warn("Using legacy .env.local. Prefer copying .env.example to .env for new deployments.")
            return legacy
        return requested

    def _get_env(self, key: str, default: str = "") -> str:
        v = self.env.get(key, default)
        return v if v != "" else default

    def _validate_developer(self, dev: Dict[str, Any]) -> Dict[str, Any]:
        name = str(dev["name"])
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", name):
            fail(
                f"Invalid developer username '{name}'. Use a Linux-safe name: "
                "lowercase letter/underscore first, then lowercase letters, digits, underscores, or hyphens."
            )
        if not str(dev.get("ssh_key", "")).startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-")):
            fail(f"Developer '{name}' has no valid SSH public key configured.")
        return dev

    def _build_runtime_config(self) -> RuntimeConfig:
        profile = self.args.profile or self._get_env("OCI_PROFILE", "DEFAULT")
        tenancy = self._get_env("OCI_TENANCY_OCID")
        compartment_name = self._get_env("OCI_COMPARTMENT_NAME", "")
        compartment_ocid = self._get_env("OCI_COMPARTMENT_OCID")
        region = self.args.region or self._get_env("OCI_REGION", "")
        if not region:
            cfg = oci.config.from_file(file_location=str(Path(self.args.config_file).expanduser()), profile_name=profile)
            region = cfg.get("region", "")

        ssh_pub = Path(self._get_env("SSH_PUBLIC_KEY_PATH", "~/.ssh/id_rsa.pub")).expanduser()
        ssh_priv = Path(str(ssh_pub).removesuffix(".pub")).expanduser()

        ad_raw = self._get_env("AVAILABILITY_DOMAIN", "")
        ad_num = int(ad_raw) if ad_raw.strip() else 1

        multi_dev_enabled = env_bool(self._get_env("MULTI_DEV_ENABLED", "false"), False)
        
        # Build developers list
        developers = []
        
        # Developer 1 (Admin)
        dev1_name = self._get_env("ADMIN_USERNAME", "devuser")
        dev1_ssh = resolve_ssh_key(str(ssh_pub))
        dev1_wg_ip = self._get_env("WG_CLIENT_IP", "10.200.200.2")
        dev1_port = int(self._get_env("CODE_SERVER_PORT", "8443"))
        
        developers.append(self._validate_developer({
            "name": dev1_name,
            "ssh_key": dev1_ssh,
            "wg_ip": dev1_wg_ip,
            "code_server_port": dev1_port,
            "private_key": "",
            "public_key": ""
        }))
        
        if multi_dev_enabled:
            idx = 2
            while True:
                dev_name = self._get_env(f"DEV_{idx}_NAME")
                if not dev_name:
                    has_more = any(self._get_env(f"DEV_{check_idx}_NAME") for check_idx in range(idx + 1, idx + 3))
                    if not has_more:
                        break
                    idx += 1
                    continue

                dev_ssh_path = self._get_env(f"DEV_{idx}_SSH_KEY_PATH", "")
                if dev_ssh_path:
                    developers.append(self._validate_developer({
                        "name": dev_name,
                        "ssh_key": resolve_ssh_key(dev_ssh_path),
                        "wg_ip": self._get_env(f"DEV_{idx}_WG_IP", f"10.200.200.{idx + 1}"),
                        "code_server_port": int(self._get_env(f"DEV_{idx}_CODE_SERVER_PORT", str(8443 + idx - 1))),
                        "private_key": "",
                        "public_key": ""
                    }))
                idx += 1

        return RuntimeConfig(
            project_dir=self.project_dir,
            script_dir=self.script_dir,
            env_file=self.env_file,
            profile=profile,
            region=region,
            tenancy_ocid=tenancy,
            compartment_name=compartment_name,
            compartment_ocid=compartment_ocid,
            vm_name=self._get_env("VM_NAME", "remote-dev-server"),
            vm_shape=self._get_env("VM_SHAPE", "VM.Standard.E6.Flex"),
            vm_ocpus=float(self._get_env("VM_OCPUS", "4")),
            vm_memory_gb=float(self._get_env("VM_MEMORY_GB", "32")),
            vm_boot_volume_gb=int(self._get_env("VM_BOOT_VOLUME_GB", "100")),
            ubuntu_version=self._get_env("UBUNTU_VERSION", "24.04"),
            availability_domain_num=ad_num,
            existing_vcn_ocid=self._get_env("EXISTING_VCN_OCID"),
            existing_subnet_ocid=self._get_env("EXISTING_SUBNET_OCID"),
            vcn_name=self._get_env("VCN_NAME", "remote-dev-vcn"),
            vcn_cidr=self._get_env("VCN_CIDR", "10.0.0.0/16"),
            subnet_cidr=self._get_env("SUBNET_CIDR", "10.0.1.0/24"),
            ssh_public_key_path=ssh_pub,
            ssh_private_key_path=ssh_priv,
            wg_port=int(self._get_env("WG_PORT", "51820")),
            wg_network=self._get_env("WG_NETWORK", "10.200.200.0/24"),
            wg_server_ip=self._get_env("WG_SERVER_IP", "10.200.200.1"),
            wg_client_ip=self._get_env("WG_CLIENT_IP", "10.200.200.2"),
            rdp_port=int(self._get_env("RDP_PORT", "3389")),
            vnc_port=int(self._get_env("VNC_PORT", "5901")),
            code_server_port=int(self._get_env("CODE_SERVER_PORT", "8443")),
            install_claude_code=env_bool(self._get_env("INSTALL_CLAUDE_CODE", "true"), True),
            install_codex=env_bool(self._get_env("INSTALL_CODEX", "true"), True),
            install_gemini=env_bool(self._get_env("INSTALL_GEMINI", "true"), True),
            install_code_server=env_bool(self._get_env("INSTALL_CODE_SERVER", "true"), True),
            install_cursor=env_bool(self._get_env("INSTALL_CURSOR", "true"), True),
            node_version=self._get_env("NODE_VERSION", "20"),
            firewall_strict=env_bool(self._get_env("FIREWALL_STRICT", "true"), True),
            admin_username=dev1_name,
            multi_dev_enabled=multi_dev_enabled,
            developers=developers,
        )

    def check_prerequisites(self) -> None:
        log("Checking prerequisites...")
        if shutil.which("wg") is None:
            fail("WireGuard tools not found. Install wireguard-tools before deploying.")
        if not self.runtime.ssh_public_key_path.exists():
            fail(f"SSH public key not found: {self.runtime.ssh_public_key_path}")
        if not self.runtime.ssh_private_key_path.exists():
            fail(f"SSH private key not found: {self.runtime.ssh_private_key_path}")
        log("Prerequisites OK")

    def resolve_tenancy_and_compartment(self) -> None:
        cfg_tenancy = self.oci_config.get("tenancy", "")
        if not self.runtime.tenancy_ocid:
            self.runtime.tenancy_ocid = cfg_tenancy
        if not self.runtime.tenancy_ocid:
            fail("Could not resolve OCI tenancy OCID from .env or OCI profile.")

        log(f"Profile: {self.runtime.profile}")
        log(f"Tenancy: {self.runtime.tenancy_ocid}")
        log(f"Region: {self.runtime.region}")

        if self.compartment_ocid:
            log(f"Using compartment OCID from config: {self.compartment_ocid}")
            return

        log(f"Resolving compartment by name: {self.runtime.compartment_name}")
        compartments = all_results(
            self.identity.list_compartments,
            compartment_id=self.runtime.tenancy_ocid,
            compartment_id_in_subtree=True,
            access_level="ANY",
        )
        for c in compartments:
            if c.name == self.runtime.compartment_name and c.lifecycle_state == "ACTIVE":
                self.compartment_ocid = c.id
                break
        if not self.compartment_ocid:
            fail(f"Compartment '{self.runtime.compartment_name}' not found in tenancy.")
        log(f"Compartment OCID: {self.compartment_ocid}")

    def check_existing_instance(self) -> None:
        instances = all_results(
            self.compute.list_instances,
            compartment_id=self.compartment_ocid,
            display_name=self.runtime.vm_name,
        )
        non_terminated = [i for i in instances if i.lifecycle_state not in {"TERMINATED", "TERMINATING"}]
        if not non_terminated:
            return

        latest = sorted(non_terminated, key=lambda x: x.time_created, reverse=True)[0]
        self.existing_instance_id = latest.id
        self.existing_instance_state = latest.lifecycle_state
        warn(f"Existing instance found: {latest.id} ({latest.lifecycle_state})")

        if self.args.dry_run:
            return

        if not self.args.replace_existing:
            fail(
                "Existing instance found. Re-run with --replace-existing to terminate it and deploy fresh."
            )

        warn("Terminating existing instance due to --replace-existing")
        self.compute.terminate_instance(
            instance_id=latest.id,
            preserve_boot_volume=False,
        )
        oci.wait_until(
            self.compute,
            self.compute.get_instance(latest.id),
            evaluate_response=lambda r: r.data.lifecycle_state == "TERMINATED",
            max_wait_seconds=900,
            max_interval_seconds=15,
        )
        log("Existing instance terminated")

    def resolve_availability_domain(self) -> None:
        ads = all_results(
            self.identity.list_availability_domains,
            compartment_id=self.runtime.tenancy_ocid,
        )
        idx = max(0, self.runtime.availability_domain_num - 1)
        if idx >= len(ads):
            fail(f"Availability domain index {self.runtime.availability_domain_num} is out of range.")
        self.availability_domain_name = ads[idx].name
        log(f"Using availability domain: {self.availability_domain_name}")

    def resolve_image(self) -> None:
        images = all_results(
            self.compute.list_images,
            compartment_id=self.compartment_ocid,
            operating_system="Canonical Ubuntu",
            operating_system_version=self.runtime.ubuntu_version,
            shape=self.runtime.vm_shape,
            sort_by="TIMECREATED",
            sort_order="DESC",
        )
        filtered = [
            img for img in images
            if "aarch64" not in img.display_name.lower() and "minimal" not in img.display_name.lower()
        ]
        if not filtered:
            fail(f"No Ubuntu {self.runtime.ubuntu_version} image found for shape {self.runtime.vm_shape}")
        self.image_ocid = filtered[0].id
        log(f"Using image OCID: {self.image_ocid}")

    def _validate_existing_network(self, vcn_id: str, subnet_id: str) -> None:
        vcn = self.network.get_vcn(vcn_id).data
        if vcn.lifecycle_state != "AVAILABLE":
            fail(f"Existing VCN not AVAILABLE: {vcn_id} ({vcn.lifecycle_state})")
        subnet = self.network.get_subnet(subnet_id).data
        if subnet.lifecycle_state != "AVAILABLE":
            fail(f"Existing Subnet not AVAILABLE: {subnet_id} ({subnet.lifecycle_state})")
        if subnet.vcn_id != vcn_id:
            fail(f"Subnet {subnet_id} does not belong to VCN {vcn_id}")

    def setup_networking(self) -> None:
        r = self.runtime
        if r.existing_vcn_ocid and r.existing_subnet_ocid:
            log("Using existing VCN/Subnet from config")
            self._validate_existing_network(r.existing_vcn_ocid, r.existing_subnet_ocid)
            self.vcn_ocid = r.existing_vcn_ocid
            self.subnet_ocid = r.existing_subnet_ocid
            return

        if r.existing_vcn_ocid or r.existing_subnet_ocid:
            warn("Only one of EXISTING_VCN_OCID / EXISTING_SUBNET_OCID set; falling back to managed network.")

        vcns = all_results(
            self.network.list_vcns,
            compartment_id=self.compartment_ocid,
            display_name=r.vcn_name,
        )
        if vcns:
            vcn = vcns[0]
            self.vcn_ocid = vcn.id
            log(f"Using existing VCN by name: {self.vcn_ocid}")
        else:
            if self.args.dry_run:
                info(f"[dry-run] Would create VCN '{r.vcn_name}' with CIDR {r.vcn_cidr}")
                self.vcn_ocid = "<to-create>"
                self.subnet_ocid = "<to-create>"
                return
            created = self.network.create_vcn(
                oci.core.models.CreateVcnDetails(
                    compartment_id=self.compartment_ocid,
                    display_name=r.vcn_name,
                    cidr_blocks=[r.vcn_cidr],
                    dns_label="remotedev",
                )
            ).data
            self.vcn_ocid = created.id
            oci.wait_until(
                self.network,
                self.network.get_vcn(self.vcn_ocid),
                evaluate_response=lambda x: x.data.lifecycle_state == "AVAILABLE",
                max_wait_seconds=300,
                max_interval_seconds=5,
            )
            log(f"Created VCN: {self.vcn_ocid}")

        if self.args.dry_run:
            subnets = all_results(
                self.network.list_subnets,
                compartment_id=self.compartment_ocid,
                vcn_id=self.vcn_ocid,
            )
            if subnets:
                self.subnet_ocid = subnets[0].id
            info("[dry-run] Would ensure IGW/route/security-list/subnet configuration")
            return

        vcn = self.network.get_vcn(self.vcn_ocid).data
        igws = all_results(
            self.network.list_internet_gateways,
            compartment_id=self.compartment_ocid,
            vcn_id=self.vcn_ocid,
        )
        if igws:
            igw_id = igws[0].id
        else:
            igw = self.network.create_internet_gateway(
                oci.core.models.CreateInternetGatewayDetails(
                    compartment_id=self.compartment_ocid,
                    vcn_id=self.vcn_ocid,
                    display_name="remote-dev-igw",
                    is_enabled=True,
                )
            ).data
            igw_id = igw.id
            log(f"Created internet gateway: {igw_id}")

        self.network.update_route_table(
            rt_id=vcn.default_route_table_id,
            update_route_table_details=oci.core.models.UpdateRouteTableDetails(
                route_rules=[
                    oci.core.models.RouteRule(
                        destination="0.0.0.0/0",
                        destination_type="CIDR_BLOCK",
                        network_entity_id=igw_id,
                    )
                ]
            ),
        )
        log(f"Updated default route table: {vcn.default_route_table_id}")

        ingress_rules = [
            oci.core.models.IngressSecurityRule(
                source="0.0.0.0/0",
                protocol="6",
                description="SSH",
                tcp_options=oci.core.models.TcpOptions(
                    destination_port_range=oci.core.models.PortRange(min=22, max=22),
                ),
            ),
            oci.core.models.IngressSecurityRule(
                source="0.0.0.0/0",
                protocol="17",
                description="WireGuard VPN",
                udp_options=oci.core.models.UdpOptions(
                    destination_port_range=oci.core.models.PortRange(
                        min=r.wg_port, max=r.wg_port
                    ),
                ),
            ),
            oci.core.models.IngressSecurityRule(
                source="0.0.0.0/0",
                protocol="1",
                description="ICMP",
                icmp_options=oci.core.models.IcmpOptions(type=3, code=4),
            ),
        ]
        egress_rules = [
            oci.core.models.EgressSecurityRule(
                destination="0.0.0.0/0",
                protocol="all",
                description="Allow all outbound",
            )
        ]
        self.network.update_security_list(
            security_list_id=vcn.default_security_list_id,
            update_security_list_details=oci.core.models.UpdateSecurityListDetails(
                ingress_security_rules=ingress_rules,
                egress_security_rules=egress_rules,
            ),
        )
        log(f"Updated default security list: {vcn.default_security_list_id}")

        subnets = all_results(
            self.network.list_subnets,
            compartment_id=self.compartment_ocid,
            vcn_id=self.vcn_ocid,
        )
        if subnets:
            self.subnet_ocid = subnets[0].id
            log(f"Using existing subnet: {self.subnet_ocid}")
        else:
            subnet = self.network.create_subnet(
                oci.core.models.CreateSubnetDetails(
                    compartment_id=self.compartment_ocid,
                    vcn_id=self.vcn_ocid,
                    display_name="remote-dev-subnet",
                    cidr_block=r.subnet_cidr,
                    dns_label="remotedevsubnet",
                    prohibit_public_ip_on_vnic=False,
                )
            ).data
            self.subnet_ocid = subnet.id
            log(f"Created subnet: {self.subnet_ocid}")

    def generate_wireguard_keys(self) -> None:
        keys_dir = self.project_dir / "configs" / "wireguard"
        keys_dir.mkdir(parents=True, exist_ok=True)
        self.wg_server_private_key = run_cmd(["wg", "genkey"])
        self.wg_server_public_key = run_cmd(
            ["bash", "-lc", f"printf '%s' '{self.wg_server_private_key}' | wg pubkey"]
        )

        keys_txt = (
            "# WireGuard Keys - KEEP SECRET!\n"
            f"# Generated: {dt.datetime.now()}\n\n"
            f"Server Private Key: {self.wg_server_private_key}\n"
            f"Server Public Key:  {self.wg_server_public_key}\n\n"
        )

        for dev in self.runtime.developers:
            dev["private_key"] = run_cmd(["wg", "genkey"])
            dev["public_key"] = run_cmd(
                ["bash", "-lc", f"printf '%s' '{dev['private_key']}' | wg pubkey"]
            )
            keys_txt += (
                f"Developer: {dev['name']}\n"
                f"  Private Key: {dev['private_key']}\n"
                f"  Public Key:  {dev['public_key']}\n\n"
            )

        p = keys_dir / "keys.txt"
        p.write_text(keys_txt, encoding="utf-8")
        p.chmod(0o600)
        log("Generated WireGuard key material for server and developers")

    def generate_cloud_init(self) -> None:
        template_path = self.project_dir / "templates" / "cloud-init.yaml.tpl"
        output_path = self.project_dir / "configs" / "cloud-init.yaml"
        ssh_pub = self.runtime.ssh_public_key_path.read_text(encoding="utf-8").strip()
        r = self.runtime

        # 1. Build USERS_CONFIG
        users_yaml = "users:\n"
        for dev in r.developers:
            users_yaml += (
                f"  - name: {dev['name']}\n"
                f"    groups: [sudo, docker, adm, video, audio, plugdev, developers]\n"
                f"    shell: /bin/bash\n"
                f"    sudo: ['ALL=(ALL) NOPASSWD:ALL']\n"
                f"    lock_passwd: false\n"
                f"    ssh_authorized_keys:\n"
                f"      - {dev['ssh_key']}\n"
            )

        # 2. Build WG_PEERS_CONFIG
        wg_peers = ""
        for dev in r.developers:
            wg_peers += (
                f"      [Peer]\n"
                f"      # Developer: {dev['name']}\n"
                f"      PublicKey = {dev['public_key']}\n"
                f"      AllowedIPs = {dev['wg_ip']}/32\n\n"
            )

        # 3. Build DEVELOPERS_LIST
        dev_list = " ".join(f'"{dev["name"]}"' for dev in r.developers)

        # 4. Build DEVELOPERS_PORTS_MAP
        dev_ports_map = ""
        for dev in r.developers:
            dev_ports_map += f'      DEV_PORTS["{dev["name"]}"]={dev["code_server_port"]}\n'

        # 5. Build DASHBOARD_HTML
        cards_html = ""
        for i, dev in enumerate(r.developers):
            badge = "OWNER" if i == 0 else f"DEV {i+1}"
            cards_html += f"""
                <div class="card">
                    <div class="card-header">
                        <div class="dev-info">
                            <h3>{dev['name']}</h3>
                            <span>UNIX: {dev['name']}</span>
                        </div>
                        <span class="dev-badge">{badge}</span>
                    </div>
                    <div class="card-body">
                        <div class="info-row">
                            <span class="info-label">VPN IP</span>
                            <span class="info-value">{dev['wg_ip']}</span>
                        </div>
                        <div class="info-row">
                            <span class="info-label">IDE Port</span>
                            <span class="info-value">{dev['code_server_port']}</span>
                        </div>
                        <div class="info-row">
                            <span class="info-label">SSH Target</span>
                            <span class="info-value">{dev['name']}@{r.wg_server_ip}</span>
                        </div>
                    </div>
                    <a href="http://{r.wg_server_ip}:{dev['code_server_port']}" class="btn" target="_blank">Launch Web IDE</a>
                </div>
            """
        raw_dash = HTML_DASHBOARD_TEMPLATE.replace("{{DASHBOARD_CARDS}}", cards_html)
        dash_lines = raw_dash.splitlines()
        dashboard_html = "\n".join(f"      {line}" for line in dash_lines)

        replacements = {
            "VM_NAME": r.vm_name,
            "ADMIN_USERNAME": r.admin_username,
            "SSH_PUBLIC_KEY": ssh_pub,
            "WG_SERVER_PRIVATE_KEY": self.wg_server_private_key,
            "WG_SERVER_PUBLIC_KEY": self.wg_server_public_key,
            "WG_SERVER_IP": r.wg_server_ip,
            "WG_PORT": str(r.wg_port),
            "WG_NETWORK": r.wg_network,
            "NODE_VERSION": r.node_version,
            "RDP_PORT": str(r.rdp_port),
            "VNC_PORT": str(r.vnc_port),
            "FIREWALL_STRICT": str(r.firewall_strict).lower(),
            "INSTALL_CLAUDE_CODE": str(r.install_claude_code).lower(),
            "INSTALL_CODEX": str(r.install_codex).lower(),
            "INSTALL_GEMINI": str(r.install_gemini).lower(),
            "INSTALL_CODE_SERVER": str(r.install_code_server).lower(),
            "INSTALL_CURSOR": str(r.install_cursor).lower(),
            "VM_PUBLIC_IP": "PENDING",
            "USERS_CONFIG": users_yaml,
            "WG_PEERS_CONFIG": wg_peers,
            "DEVELOPERS_LIST": dev_list,
            "DEVELOPERS_PORTS_MAP": dev_ports_map,
            "DASHBOARD_HTML": dashboard_html,
        }

        text = template_path.read_text(encoding="utf-8")
        for key, value in replacements.items():
            text = text.replace(f"{{{{{key}}}}}", value)
        output_path.write_text(text, encoding="utf-8")
        log(f"Generated cloud-init file: {output_path}")

    def create_instance(self) -> None:
        cloud_init = (self.project_dir / "configs" / "cloud-init.yaml").read_text(encoding="utf-8")
        cloud_b64 = base64.b64encode(cloud_init.encode("utf-8")).decode("ascii")
        ssh_pub = self.runtime.ssh_public_key_path.read_text(encoding="utf-8").strip()
        r = self.runtime

        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=self.compartment_ocid,
            availability_domain=self.availability_domain_name,
            display_name=r.vm_name,
            shape=r.vm_shape,
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=r.vm_ocpus,
                memory_in_gbs=r.vm_memory_gb,
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=self.image_ocid,
                boot_volume_size_in_gbs=r.vm_boot_volume_gb,
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=self.subnet_ocid,
                assign_public_ip=True,
            ),
            metadata={
                "ssh_authorized_keys": ssh_pub + "\n",
                "user_data": cloud_b64,
            },
        )
        instance = self.compute.launch_instance(details).data
        self.instance_ocid = instance.id
        log(f"Created instance: {self.instance_ocid}")

    def wait_for_instance_running(self) -> None:
        oci.wait_until(
            self.compute,
            self.compute.get_instance(self.instance_ocid),
            evaluate_response=lambda r: r.data.lifecycle_state == "RUNNING",
            max_wait_seconds=1800,
            max_interval_seconds=10,
        )
        log("Instance is RUNNING")

    def resolve_public_ip(self) -> None:
        attachments = all_results(
            self.compute.list_vnic_attachments,
            compartment_id=self.compartment_ocid,
            instance_id=self.instance_ocid,
        )
        if not attachments:
            fail("No VNIC attachment found for instance.")
        attachment = sorted(attachments, key=lambda x: x.time_created)[0]
        vnic = self.network.get_vnic(attachment.vnic_id).data
        self.public_ip = vnic.public_ip or ""
        if not self.public_ip:
            fail("Public IP not assigned to instance VNIC.")
        log(f"Public IP: {self.public_ip}")

    def write_client_wireguard_config(self) -> None:
        keys_dir = self.project_dir / "configs" / "wireguard"
        # Shared renderer: split-tunnel + no DNS by default (see scripts/wg_config.py).
        full_tunnel = env_bool(self._get_env("WG_FULL_TUNNEL", "false"), False)
        wg_dns = self._get_env("WG_DNS", "")
        wg_network = self._get_env("WG_NETWORK", "10.200.200.0/24")
        for dev in self.runtime.developers:
            path = keys_dir / f"client_{dev['name']}.conf"
            cfg = render_wg_client_config(
                private_key=dev["private_key"],
                address=dev["wg_ip"],
                server_public_key=self.wg_server_public_key,
                endpoint=f"{self.public_ip}:{self.runtime.wg_port}",
                wg_network=wg_network,
                full_tunnel=full_tunnel,
                dns=wg_dns,
            )
            path.write_text(cfg, encoding="utf-8")
            path.chmod(0o600)
            log(f"Generated WireGuard client config for {dev['name']}: {path}")
            
            if dev['name'] == self.runtime.admin_username:
                shutil.copy(path, keys_dir / "client.conf")
                (keys_dir / "client.conf").chmod(0o600)
                
            if shutil.which("qrencode"):
                qr_path = keys_dir / f"client_{dev['name']}-qr.txt"
                qr = run_cmd(["bash", "-lc", f"qrencode -t ansiutf8 < '{path}'"], check=False)
                if qr:
                    qr_path.write_text(qr, encoding="utf-8")

    def save_deployment_info(self) -> None:
        out = self.project_dir / "configs" / "deployment-info.txt"
        r = self.runtime
        txt = (
            "# OCI Remote Development Server Deployment\n"
            "# ========================================\n"
            f"# Deployed: {dt.datetime.now()}\n\n"
            f"Instance OCID: {self.instance_ocid}\n"
            f"Instance Name: {r.vm_name}\n"
            f"Public IP: {self.public_ip}\n"
            f"Shape: {r.vm_shape} ({int(r.vm_ocpus)} OCPUs, {int(r.vm_memory_gb)}GB RAM)\n\n"
            "# Network\n"
            f"VCN: {self.vcn_ocid}\n"
            f"Subnet: {self.subnet_ocid}\n\n"
            "# VPN & Dashboard Access\n"
            f"WireGuard Port: {r.wg_port}\n"
            f"Dev Dashboard: http://{r.wg_server_ip} (Connect via VPN first)\n\n"
            "# Developer Workspaces\n"
        )
        
        for dev in r.developers:
            txt += (
                f"## Developer: {dev['name']}\n"
                f"  SSH: ssh -i {r.ssh_private_key_path} {dev['name']}@{self.public_ip}\n"
                f"  WireGuard Config: configs/wireguard/client_{dev['name']}.conf\n"
                f"  VPN IP: {dev['wg_ip']}\n"
                f"  code-server (Web IDE): http://{r.wg_server_ip}:{dev['code_server_port']}\n\n"
            )
            
        txt += (
            "# Compartment\n"
            f"Compartment: {r.compartment_name}\n"
            f"Compartment OCID: {self.compartment_ocid}\n"
        )
        out.write_text(txt, encoding="utf-8")
        log(f"Saved deployment info: {out}")

    def verify_ssh(self) -> None:
        if self.args.skip_ssh_verify:
            info("Skipping SSH verification due to --skip-ssh-verify")
            return
        ssh_key = str(self.runtime.ssh_private_key_path)
        target = f"{self.runtime.admin_username}@{self.public_ip}"
        ssh_ctrl_dir = Path(tempfile.mkdtemp(prefix="oci-remote-dev-ssh-"))
        ssh_ctrl_sock = ssh_ctrl_dir / "ctrl-%r@%h:%p"
        ssh_base_args = [
            "ssh",
            "-i",
            ssh_key,
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPath={ssh_ctrl_sock}",
            "-o",
            "ControlPersist=300",
        ]
        log("Verifying SSH connectivity...")
        try:
            for attempt in range(1, 13):
                rc, out, err = run_cmd_no_raise(ssh_base_args + [target, "echo SSH OK"])
                if rc == 0:
                    log("SSH connectivity verified")
                    return
                info(f"Waiting for SSH... (attempt {attempt}/12)")
                time.sleep(10)
        finally:
            run_cmd_no_raise(
                [
                    "ssh",
                    "-o",
                    f"ControlPath={ssh_ctrl_sock}",
                    "-O",
                    "exit",
                    target,
                ]
            )
            shutil.rmtree(ssh_ctrl_dir, ignore_errors=True)
        warn(f"SSH verification timed out. Try manually: ssh -i {ssh_key} {target}")

    def print_summary(self) -> None:
        r = self.runtime
        print("")
        print(f"{GREEN}╔══════════════════════════════════════════════════════════════════╗{NC}")
        print(f"{GREEN}║           DEPLOYMENT COMPLETE!                                   ║{NC}")
        print(f"{GREEN}╠══════════════════════════════════════════════════════════════════╣{NC}")
        print(f"{GREEN}║{NC} Instance: {CYAN}{r.vm_name}{NC}")
        print(f"{GREEN}║{NC} Public IP: {CYAN}{self.public_ip}{NC}")
        print(f"{GREEN}║{NC} Shape: {CYAN}{r.vm_shape} ({int(r.vm_ocpus)} OCPUs, {int(r.vm_memory_gb)}GB RAM){NC}")
        print(f"{GREEN}╠══════════════════════════════════════════════════════════════════╣{NC}")
        print(f"{GREEN}║{NC} Dev Dashboard: {CYAN}http://{r.wg_server_ip}{NC} (Connect via VPN)")
        print(f"{GREEN}║{NC} Owner SSH: {CYAN}ssh -i {r.ssh_private_key_path} {r.admin_username}@{self.public_ip}{NC}")
        print(f"{GREEN}╚══════════════════════════════════════════════════════════════════╝{NC}")
        print("")

    def print_plan(self) -> None:
        r = self.runtime
        print("")
        print(f"{BLUE}╔══════════════════════════════════════════════════════════════════╗{NC}")
        print(f"{BLUE}║                 SDK DEPLOYMENT PLAN                              ║{NC}")
        print(f"{BLUE}╠══════════════════════════════════════════════════════════════════╣{NC}")
        print(f"{BLUE}║{NC} Profile:      {CYAN}{r.profile}{NC}")
        print(f"{BLUE}║{NC} Region:       {CYAN}{r.region}{NC}")
        print(f"{BLUE}║{NC} Compartment:  {CYAN}{r.compartment_name}{NC}")
        print(f"{BLUE}║{NC} VM Name:      {CYAN}{r.vm_name}{NC}")
        print(f"{BLUE}║{NC} Shape:        {CYAN}{r.vm_shape} ({int(r.vm_ocpus)} OCPUs, {int(r.vm_memory_gb)}GB){NC}")
        print(f"{BLUE}║{NC} AD:           {CYAN}{self.availability_domain_name}{NC}")
        print(f"{BLUE}║{NC} Image:        {CYAN}{self.image_ocid[:48]}...{NC}")
        print(f"{BLUE}║{NC} Existing VM:  {CYAN}{self.existing_instance_state or 'none'}{NC}")
        print(f"{BLUE}║{NC} VCN:          {CYAN}{self.vcn_ocid or '(resolved during apply)'}{NC}")
        print(f"{BLUE}║{NC} Subnet:       {CYAN}{self.subnet_ocid or '(resolved during apply)'}{NC}")
        print(f"{BLUE}╚══════════════════════════════════════════════════════════════════╝{NC}")
        print("")

    def execute(self) -> None:
        self.check_prerequisites()
        self.resolve_tenancy_and_compartment()
        self.check_existing_instance()
        self.resolve_availability_domain()
        self.resolve_image()
        self.setup_networking()

        if self.args.dry_run:
            self.print_plan()
            return

        if not self.args.yes:
            self.print_plan()
            answer = input("Run deployment now? (y/N): ").strip().lower()
            if answer not in {"y", "yes"}:
                fail("Deployment cancelled by user.")

        self.generate_wireguard_keys()
        self.generate_cloud_init()
        self.create_instance()
        self.wait_for_instance_running()
        self.resolve_public_ip()
        self.write_client_wireguard_config()
        self.save_deployment_info()
        self.verify_ssh()
        self.print_summary()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deploy OCI remote dev VM via Python SDK")
    p.add_argument("--env-file", default=".env", help="Path to env file")
    p.add_argument("--config-file", default="~/.oci/config", help="Path to OCI config")
    p.add_argument("--profile", default="", help="OCI profile override")
    p.add_argument("--region", default="", help="OCI region override")
    p.add_argument("--yes", action="store_true", help="Run non-interactive without confirmation prompt")
    p.add_argument("--replace-existing", action="store_true", help="Terminate existing instance with same name")
    p.add_argument("--dry-run", action="store_true", help="Resolve and print plan without creating/updating resources")
    p.add_argument("--skip-ssh-verify", action="store_true", help="Skip post-deploy SSH connectivity verification")
    return p


def main() -> int:
    print(f"{BLUE}")
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║     OCI Remote Development Server Deployment (Python SDK)        ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(f"{NC}")
    args = build_parser().parse_args()
    try:
        SDKDeployer(args).execute()
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        print(f"{RED}[FATAL]{NC} {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
