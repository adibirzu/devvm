#!/usr/bin/env python3
"""
Unified Multi-Cloud Dev Server Deployer
======================================
Orchestrates virtual machine provisioning across OCI, AWS, GCP, or Azure.
Guarantees identical environment provisioning by rendering a unified cloud-init,
generating multi-user WireGuard keys, and compiling connection configurations.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.wg_config import render_wg_client_config

# Colors for terminal output
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


class MultiCloudDeployer:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.script_dir = Path(__file__).resolve().parent
        self.project_dir = self.script_dir.parent
        self.env_file = Path(args.env_file).expanduser()
        
        # Load environment
        self.env = self._parse_env_file(self.env_file)
        self.provider = self.env.get("CLOUD_PROVIDER", "OCI").upper()
        
        self.public_ip = ""
        self.instance_id = ""
        self.wg_server_private_key = ""
        self.wg_server_public_key = ""
        self.developers: List[Dict[str, Any]] = []

    def _parse_env_file(self, path: Path) -> Dict[str, str]:
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

    def _get_env(self, key: str, default: str = "") -> str:
        return self.env.get(key, default) or default

    def _env_bool(self, key: str, default: bool = False) -> bool:
        val = self._get_env(key)
        if val == "":
            return default
        return val.lower() in {"1", "true", "yes", "y", "on"}

    def _resolve_ssh_key(self, val: str) -> str:
        if not val:
            return ""
        if val.startswith("~") or "/" in val or "\\" in val:
            p = Path(val.replace("~", str(Path.home())))
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8").strip()
        return val.strip()

    def check_prerequisites(self) -> None:
        log(f"Checking prerequisites for cloud provider: {self.provider}...")
        if shutil.which("wg") is None:
            fail("WireGuard tools not found. Install wireguard-tools before deploying.")
            
        ssh_pub_path = Path(self._get_env("SSH_PUBLIC_KEY_PATH", "~/.ssh/id_rsa.pub")).expanduser()
        if not ssh_pub_path.exists():
            fail(f"SSH public key not found: {ssh_pub_path}")
            
        # Verify provider specific CLI tools if not using OCI
        if self.provider == "AWS":
            if shutil.which("aws") is None:
                warn("AWS CLI not found in PATH. Ensure credentials exist or boto3 is available.")
        elif self.provider == "GCP":
            if shutil.which("gcloud") is None:
                fail("gcloud CLI not found in PATH. Google Cloud deployments require the gcloud CLI tool.")
        elif self.provider == "AZURE":
            if shutil.which("az") is None:
                fail("az CLI not found in PATH. Azure deployments require the az CLI tool.")
                
        log("Prerequisites OK")

    def build_developers_list(self) -> None:
        ssh_pub = Path(self._get_env("SSH_PUBLIC_KEY_PATH", "~/.ssh/id_rsa.pub")).expanduser()
        dev1_name = self._get_env("ADMIN_USERNAME", "devuser")
        dev1_ssh = self._resolve_ssh_key(str(ssh_pub))
        dev1_wg_ip = self._get_env("WG_CLIENT_IP", "10.200.200.2")
        dev1_port = int(self._get_env("CODE_SERVER_PORT", "8443"))
        
        self.developers.append({
            "name": dev1_name,
            "ssh_key": dev1_ssh,
            "wg_ip": dev1_wg_ip,
            "code_server_port": dev1_port,
            "private_key": "",
            "public_key": ""
        })
        
        if self._env_bool("MULTI_DEV_ENABLED", False):
            # Parse arbitrary developers dynamically: DEV_2_NAME, DEV_3_NAME, etc.
            idx = 2
            while True:
                name_key = f"DEV_{idx}_NAME"
                ssh_key = f"DEV_{idx}_SSH_KEY_PATH"
                wg_key = f"DEV_{idx}_WG_IP"
                port_key = f"DEV_{idx}_CODE_SERVER_PORT"
                
                dev_name = self._get_env(name_key)
                if not dev_name:
                    # Look ahead up to 2 slots to allow sparse definitions or break if no more devs
                    has_more = False
                    for check_idx in range(idx + 1, idx + 3):
                        if self._get_env(f"DEV_{check_idx}_NAME"):
                            has_more = True
                            break
                    if not has_more:
                        break
                    idx += 1
                    continue
                
                dev_ssh_path = self._get_env(ssh_key)
                if dev_ssh_path:
                    self.developers.append({
                        "name": dev_name,
                        "ssh_key": self._resolve_ssh_key(dev_ssh_path),
                        "wg_ip": self._get_env(wg_key, f"10.200.200.{idx + 1}"),
                        "code_server_port": int(self._get_env(port_key, str(8443 + idx - 1))),
                        "private_key": "",
                        "public_key": ""
                    })
                idx += 1

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

        for dev in self.developers:
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
        log("Generated WireGuard key material")

    def generate_cloud_init(self) -> None:
        template_path = self.project_dir / "templates" / "cloud-init.yaml.tpl"
        output_path = self.project_dir / "configs" / "cloud-init.yaml"
        ssh_pub = Path(self._get_env("SSH_PUBLIC_KEY_PATH", "~/.ssh/id_rsa.pub")).expanduser().read_text(encoding="utf-8").strip()

        # 1. Build USERS_CONFIG
        users_yaml = "users:\n"
        for dev in self.developers:
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
        for dev in self.developers:
            wg_peers += (
                f"      [Peer]\n"
                f"      # Developer: {dev['name']}\n"
                f"      PublicKey = {dev['public_key']}\n"
                f"      AllowedIPs = {dev['wg_ip']}/32\n\n"
            )

        # 3. Build DEVELOPERS_LIST
        dev_list = " ".join(f'"{dev["name"]}"' for dev in self.developers)

        # 4. Build DEVELOPERS_PORTS_MAP
        dev_ports_map = ""
        for dev in self.developers:
            dev_ports_map += f'      DEV_PORTS["{dev["name"]}"]={dev["code_server_port"]}\n'

        # 5. Build DASHBOARD_HTML
        cards_html = ""
        for i, dev in enumerate(self.developers):
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
                            <span class="info-value">{dev['name']}@{self._get_env("WG_SERVER_IP", "10.200.200.1")}</span>
                        </div>
                    </div>
                    <a href="http://{self._get_env("WG_SERVER_IP", "10.200.200.1")}:{dev['code_server_port']}" class="btn" target="_blank">Launch Web IDE</a>
                </div>
            """
            
        # Get dashboard template from SDK module
        try:
            from scripts.deploy_sdk import HTML_DASHBOARD_TEMPLATE
        except ImportError:
            HTML_DASHBOARD_TEMPLATE = "<h1>Dashboard cards placeholder</h1>\n{{DASHBOARD_CARDS}}"
            
        raw_dash = HTML_DASHBOARD_TEMPLATE.replace("{{DASHBOARD_CARDS}}", cards_html)
        dash_lines = raw_dash.splitlines()
        dashboard_html = "\n".join(f"      {line}" for line in dash_lines)

        replacements = {
            "VM_NAME": self._get_env("VM_NAME", "remote-dev-server"),
            "ADMIN_USERNAME": self._get_env("ADMIN_USERNAME", "devuser"),
            "SSH_PUBLIC_KEY": ssh_pub,
            "WG_SERVER_PRIVATE_KEY": self.wg_server_private_key,
            "WG_SERVER_PUBLIC_KEY": self.wg_server_public_key,
            "WG_SERVER_IP": self._get_env("WG_SERVER_IP", "10.200.200.1"),
            "WG_PORT": self._get_env("WG_PORT", "51820"),
            "WG_NETWORK": self._get_env("WG_NETWORK", "10.200.200.0/24"),
            "NODE_VERSION": self._get_env("NODE_VERSION", "20"),
            "RDP_PORT": self._get_env("RDP_PORT", "3389"),
            "VNC_PORT": self._get_env("VNC_PORT", "5901"),
            "FIREWALL_STRICT": self._get_env("FIREWALL_STRICT", "true").lower(),
            "INSTALL_CLAUDE_CODE": self._get_env("INSTALL_CLAUDE_CODE", "true").lower(),
            "INSTALL_CODEX": self._get_env("INSTALL_CODEX", "true").lower(),
            "INSTALL_GEMINI": self._get_env("INSTALL_GEMINI", "true").lower(),
            "INSTALL_CODE_SERVER": self._get_env("INSTALL_CODE_SERVER", "true").lower(),
            "INSTALL_CURSOR": self._get_env("INSTALL_CURSOR", "true").lower(),
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

    def deploy_oci_cli(self) -> None:
        """Deploy to OCI using native OCI CLI for maximum CAP profile compatibility."""
        log("Initiating Oracle Cloud Infrastructure (OCI) deployment via local CLI...")
        profile = self._get_env("OCI_PROFILE", "DEFAULT")
        compartment_name = self._get_env("OCI_COMPARTMENT_NAME", "Adrian_Birzu")
        vm_name = self._get_env("VM_NAME", "remote-dev-server")
        shape = self._get_env("VM_SHAPE", "VM.Standard.E6.Flex")
        ocpus = self._get_env("VM_OCPUS", "4")
        memory = self._get_env("VM_MEMORY_GB", "32")
        boot_volume_size = self._get_env("VM_BOOT_VOLUME_GB", "100")
        
        # 1. Resolve Tenancy and Compartment
        tenancy_ocid = self._get_env("OCI_TENANCY_OCID")
        if not tenancy_ocid:
            try:
                tenancy_ocid = run_cmd(["oci", "iam", "region-subscription", "list", 
                                        "--query", "data[0].\"tenancy-id\"", "--raw-output", "--profile", profile])
            except Exception:
                fail(f"Could not resolve tenancy OCID. Verify OCI CLI profile '{profile}' exists.")
                
        log(f"Resolved Tenancy OCID: {tenancy_ocid}")
        
        comp_ocid = self._get_env("OCI_COMPARTMENT_OCID")
        if not comp_ocid:
            try:
                comp_ocid = run_cmd([
                    "oci", "iam", "compartment", "list",
                    "--compartment-id", tenancy_ocid,
                    "--compartment-id-in-subtree", "true",
                    "--all",
                    "--query", f"data[?name=='{compartment_name}'].id | [0]",
                    "--raw-output",
                    "--profile", profile
                ])
            except Exception as exc:
                fail(f"Failed to query compartment: {exc}")
                
        if not comp_ocid or comp_ocid == "None" or comp_ocid == "null":
            comp_ocid = tenancy_ocid
            
        log(f"Resolved Compartment OCID: {comp_ocid}")
        
        # 2. Resolve Availability Domain
        ad = self._get_env("AVAILABILITY_DOMAIN")
        if not ad:
            try:
                ad = run_cmd([
                    "oci", "iam", "availability-domain", "list",
                    "--compartment-id", comp_ocid,
                    "--query", "data[0].name",
                    "--raw-output",
                    "--profile", profile
                ])
            except Exception as exc:
                fail(f"Failed to query Availability Domain: {exc}")
        log(f"Selected Availability Domain: {ad}")
        
        # 3. Networking Setup (VCN and Subnet)
        vcn_ocid = self._get_env("EXISTING_VCN_OCID")
        subnet_ocid = self._get_env("EXISTING_SUBNET_OCID")
        
        import json
        if not vcn_ocid:
            log("Checking for existing VCN named 'remote-dev-vcn'...")
            try:
                existing_vcn = run_cmd([
                    "oci", "network", "vcn", "list",
                    "--compartment-id", comp_ocid,
                    "--display-name", "remote-dev-vcn",
                    "--query", "data[0].id",
                    "--raw-output",
                    "--profile", profile
                ])
            except Exception:
                existing_vcn = ""
                
            if existing_vcn and existing_vcn != "None" and existing_vcn != "null":
                vcn_ocid = existing_vcn
                log(f"Found existing VCN: {vcn_ocid}")
            else:
                log("Creating new VCN 'remote-dev-vcn'...")
                try:
                    vcn_json = run_cmd([
                        "oci", "network", "vcn", "create",
                        "--compartment-id", comp_ocid,
                        "--cidr-block", "10.0.0.0/16",
                        "--display-name", "remote-dev-vcn",
                        "--dns-label", "remotedev",
                        "--profile", profile
                    ])
                    vcn_ocid = json.loads(vcn_json)["data"]["id"]
                    log(f"Created VCN: {vcn_ocid}")
                    
                    log("Creating Internet Gateway...")
                    igw_json = run_cmd([
                        "oci", "network", "internet-gateway", "create",
                        "--compartment-id", comp_ocid,
                        "--vcn-id", vcn_ocid,
                        "--is-enabled", "true",
                        "--display-name", "remote-dev-igw",
                        "--profile", profile
                    ])
                    igw_ocid = json.loads(igw_json)["data"]["id"]
                    
                    rt_ocid = json.loads(vcn_json)["data"]["default-route-table-id"]
                    log("Adding route rules for Internet Gateway...")
                    run_cmd([
                        "oci", "network", "route-table", "update",
                        "--rt-id", rt_ocid,
                        "--route-rules", json.dumps([{"cidrBlock": "0.0.0.0/0", "networkEntityId": igw_ocid}]),
                        "--force",
                        "--profile", profile
                    ])
                except Exception as exc:
                    fail(f"VCN Networking setup failed: {exc}")
                    
        if not subnet_ocid:
            log("Checking for existing Subnet 'remote-dev-subnet'...")
            try:
                existing_subnet = run_cmd([
                    "oci", "network", "subnet", "list",
                    "--compartment-id", comp_ocid,
                    "--vcn-id", vcn_ocid,
                    "--display-name", "remote-dev-subnet",
                    "--query", "data[0].id",
                    "--raw-output",
                    "--profile", profile
                ])
            except Exception:
                existing_subnet = ""
                
            if existing_subnet and existing_subnet != "None" and existing_subnet != "null":
                subnet_ocid = existing_subnet
                log(f"Found existing Subnet: {subnet_ocid}")
            else:
                log("Creating new Subnet 'remote-dev-subnet'...")
                try:
                    seclist_json = run_cmd([
                        "oci", "network", "security-list", "create",
                        "--compartment-id", comp_ocid,
                        "--vcn-id", vcn_ocid,
                        "--display-name", "remote-dev-seclist",
                        "--ingress-security-rules", json.dumps([
                            {"protocol": "6", "source": "0.0.0.0/0", "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}}},
                            {"protocol": "6", "source": "0.0.0.0/0", "tcpOptions": {"destinationPortRange": {"min": 80, "max": 80}}},
                            {"protocol": "6", "source": "0.0.0.0/0", "tcpOptions": {"destinationPortRange": {"min": 3389, "max": 3389}}},
                            {"protocol": "17", "source": "0.0.0.0/0", "udpOptions": {"destinationPortRange": {"min": 51820, "max": 51820}}}
                        ]),
                        "--egress-security-rules", json.dumps([
                            {"protocol": "all", "destination": "0.0.0.0/0"}
                        ]),
                        "--profile", profile
                    ])
                    seclist_ocid = json.loads(seclist_json)["data"]["id"]
                    
                    subnet_json = run_cmd([
                        "oci", "network", "subnet", "create",
                        "--compartment-id", comp_ocid,
                        "--vcn-id", vcn_ocid,
                        "--cidr-block", "10.0.1.0/24",
                        "--display-name", "remote-dev-subnet",
                        "--dns-label", "devsub",
                        "--security-list-ids", json.dumps([seclist_ocid]),
                        "--profile", profile
                    ])
                    subnet_ocid = json.loads(subnet_json)["data"]["id"]
                    log(f"Created Subnet: {subnet_ocid}")
                except Exception as exc:
                    fail(f"Subnet creation failed: {exc}")
                    
        # 4. Resolve Image OCID
        region = self._get_env("OCI_REGION", "us-ashburn-1")
        log(f"Resolving Ubuntu 24.04 LTS Image OCID for region {region}...")
        try:
            images_json = run_cmd([
                "oci", "compute", "image", "list",
                "--compartment-id", comp_ocid,
                "--operating-system", "Canonical Ubuntu",
                "--operating-system-version", "24.04",
                "--shape", shape,
                "--query", "data[0].id",
                "--raw-output",
                "--profile", profile
            ])
            image_ocid = images_json.strip()
        except Exception:
            image_ocid = ""
            
        if not image_ocid or image_ocid == "None" or image_ocid == "null":
            image_map = {
                "us-ashburn-1": "ocid1.image.oc1.iad.aaaaaaaam5p7y7r2ykygukvtrccqj4m5n76q7ykwepvxlr33lgluqz4kqnfa",
                "us-phoenix-1": "ocid1.image.oc1.phx.aaaaaaaa3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a",
                "eu-frankfurt-1": "ocid1.image.oc1.fra.aaaaaaaa4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a"
            }
            image_ocid = image_map.get(region, "ocid1.image.oc1.iad.aaaaaaaam5p7y7r2ykygukvtrccqj4m5n76q7ykwepvxlr33lgluqz4kqnfa")
            
        log(f"Selected Image OCID: {image_ocid}")
        
        # 5. Launch OCI Instance
        cloud_init_file = self.project_dir / "configs" / "cloud-init.yaml"
        
        log(f"Launching compute instance '{vm_name}' via OCI CLI...")
        launch_cmd = [
            "oci", "compute", "instance", "launch",
            "--compartment-id", comp_ocid,
            "--availability-domain", ad,
            "--display-name", vm_name,
            "--image-id", image_ocid,
            "--shape", shape,
            "--subnet-id", subnet_ocid,
            "--user-data-file", str(cloud_init_file),
            "--profile", profile
        ]
        
        if "Flex" in shape:
            launch_cmd += ["--shape-config", json.dumps({"ocpus": float(ocpus), "memoryInGBs": float(memory)})]
            
        try:
            launch_json = run_cmd(launch_cmd)
            self.instance_id = json.loads(launch_json)["data"]["id"]
            log(f"Created OCI Instance: {self.instance_id}")
            
            log("Waiting for instance to transition to RUNNING state...")
            for _ in range(30):
                state = run_cmd([
                    "oci", "compute", "instance", "get",
                    "--instance-id", self.instance_id,
                    "--query", "data.\"lifecycle-state\"",
                    "--raw-output",
                    "--profile", profile
                ])
                if state == "RUNNING":
                    log("Instance is RUNNING")
                    break
                time.sleep(10)
                
            vnic_json = run_cmd([
                "oci", "compute", "vnic-attachment", "list",
                "--compartment-id", comp_ocid,
                "--instance-id", self.instance_id,
                "--profile", profile
            ])
            vnic_id = json.loads(vnic_json)["data"][0]["vnic-id"]
            
            ip_json = run_cmd([
                "oci", "network", "vnic", "get",
                "--vnic-id", vnic_id,
                "--profile", profile
            ])
            self.public_ip = json.loads(ip_json)["data"]["public-ip"]
            log(f"OCI VM Public IP resolved: {self.public_ip}")
            
        except subprocess.CalledProcessError as exc:
            stderr_msg = exc.stderr.strip() if exc.stderr else str(exc)
            fail(f"OCI CLI deployment failed with command error:\n{stderr_msg}")
        except Exception as exc:
            fail(f"OCI CLI deployment failed: {exc}")

    def deploy_oci(self) -> None:
        """Run OCI deployment path, prioritizing the native CLI wrapper."""
        profile = self._get_env("OCI_PROFILE", "DEFAULT")
        if profile.lower() == "cap" or shutil.which("oci") is not None:
            try:
                self.deploy_oci_cli()
                return
            except Exception as exc:
                warn(f"OCI CLI deployment failed: {exc}. Falling back to OCI SDK deployment...")
                
        log("Delegating deployment to native OCI Python SDK path...")
        try:
            from scripts.deploy_sdk import SDKDeployer
            sys.argv = [sys.argv[0], "--yes"]
            if self.args.replace_existing:
                sys.argv.append("--replace-existing")
            if self.args.skip_ssh_verify:
                sys.argv.append("--skip-ssh-verify")
                
            deployer = SDKDeployer(self.args)
            deployer.wg_server_private_key = self.wg_server_private_key
            deployer.wg_server_public_key = self.wg_server_public_key
            deployer.runtime.developers = self.developers
            
            deployer.execute()
            self.public_ip = deployer.public_ip
            self.instance_id = deployer.instance_ocid
        except Exception as exc:
            fail(f"OCI SDK deployment failed: {exc}")

    def deploy_aws(self) -> None:
        """Deploy to AWS EC2 using boto3 SDK or CLI wrapper."""
        log("Initiating Amazon Web Services (AWS) deployment sequence...")
        region = self._get_env("AWS_REGION", "us-east-1")
        instance_type = self._get_env("AWS_INSTANCE_TYPE", "t3.xlarge")
        key_name = self._get_env("AWS_KEY_PAIR_NAME", "remote-dev-key")
        
        # Load user data
        cloud_init = (self.project_dir / "configs" / "cloud-init.yaml").read_text(encoding="utf-8")
        
        try:
            import boto3
            log("Using boto3 Python SDK for AWS deployment...")
            ec2 = boto3.resource("ec2", region_name=region)
            client = boto3.client("ec2", region_name=region)
            
            # Resolve or create Security Group
            sg_name = "remote-dev-security-group"
            try:
                sgs = client.describe_security_groups(GroupNames=[sg_name])
                sg_id = sgs["SecurityGroups"][0]["GroupId"]
                log(f"Reusing existing AWS Security Group: {sg_id}")
            except Exception:
                sg = client.create_security_group(
                    GroupName=sg_name,
                    Description="Security group for OCI dev server",
                )
                sg_id = sg["GroupId"]
                log(f"Created AWS Security Group: {sg_id}")
                # Add rules
                client.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[
                        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                        {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                        {"IpProtocol": "udp", "FromPort": int(self._get_env("WG_PORT", "51820")), 
                         "ToPort": int(self._get_env("WG_PORT", "51820")), "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                    ]
                )
                log("Configured AWS Security Group rules")

            # Launch Instance
            log(f"Launching EC2 instance ({instance_type}) in region {region}...")
            # Use standard Ubuntu 24.04 AMI for us-east-1 as default
            # Note: real enterprise deployments would query the AMI dynamically
            ami_map = {
                "us-east-1": "ami-0866a3c8686eaeeba", # Ubuntu 24.04 LTS
                "us-west-2": "ami-00c5c4e7da90e4016",
                "eu-central-1": "ami-0084a47cc718c111a"
            }
            ami_id = ami_map.get(region, "ami-0866a3c8686eaeeba")
            
            instances = ec2.create_instances(
                ImageId=ami_id,
                MinCount=1,
                MaxCount=1,
                InstanceType=instance_type,
                KeyName=key_name,
                SecurityGroupIds=[sg_id],
                UserData=cloud_init,
                BlockDeviceMappings=[
                    {
                        "DeviceName": "/dev/sda1",
                        "Ebs": {
                            "VolumeSize": int(self._get_env("VM_BOOT_VOLUME_GB", "100")),
                            "VolumeType": "gp3"
                        }
                    }
                ]
            )
            
            inst = instances[0]
            self.instance_id = inst.id
            log(f"Created EC2 Instance: {self.instance_id}")
            
            log("Waiting for instance to obtain running state...")
            inst.wait_until_running()
            inst.reload()
            
            self.public_ip = inst.public_ip_address
            log(f"EC2 Instance is running. Public IP: {self.public_ip}")
            
        except ImportError:
            log("boto3 not found. Falling back to AWS CLI execution...")
            # Fallback to aws CLI command
            # Create SG
            sg_cmd = ["aws", "ec2", "create-security-group", "--group-name", "remote-dev-sg", 
                      "--description", "Security group for remote dev", "--region", region]
            # In a real environment, we'd run these and fetch outputs.
            # To be robust, print commands or call them.
            fail("AWS SDK (boto3) is required for AWS deployments. Run: pip install boto3")

    def deploy_gcp(self) -> None:
        """Deploy to Google Cloud Platform using gcloud CLI."""
        log("Initiating Google Cloud Platform (GCP) deployment sequence...")
        project = self._get_env("GCP_PROJECT_ID")
        zone = self._get_env("GCP_ZONE", "us-central1-a")
        machine_type = self._get_env("GCP_MACHINE_TYPE", "e2-standard-4")
        vm_name = self._get_env("VM_NAME", "remote-dev-server")
        boot_disk_size = self._get_env("VM_BOOT_VOLUME_GB", "100")
        
        if not project:
            fail("GCP Project ID is required. Add GCP_PROJECT_ID to .env.local")
            
        # Write cloud-init to temp file
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
            tf.write((self.project_dir / "configs" / "cloud-init.yaml").read_bytes())
            temp_cloud_init = tf.name
            
        try:
            # Provision GCP Firewall rules first
            log("Creating GCP Firewall rule for SSH, WireGuard and Port 80 Dashboard...")
            run_cmd([
                "gcloud", "compute", "firewall-rules", "create", "allow-remote-dev",
                "--allow", f"tcp:22,tcp:80,udp:{self._get_env('WG_PORT', '51820')}",
                "--project", project, "--description", "Allow dev server traffic",
                "--direction", "INGRESS", "--priority", "1000", "--network", "default"
            ], check=False)
            
            # Provision Instance
            log(f"Launching GCP VM instance ({machine_type}) in project {project}...")
            cmd = [
                "gcloud", "compute", "instances", "create", vm_name,
                "--project", project,
                "--zone", zone,
                "--machine-type", machine_type,
                "--image-family", "ubuntu-2404-lts-amd64",
                "--image-project", "ubuntu-os-cloud",
                "--boot-disk-size", f"{boot_disk_size}GB",
                "--boot-disk-type", "pd-balanced",
                "--metadata-from-file", f"user-data={temp_cloud_init}"
            ]
            run_cmd(cmd)
            log("GCP instance provisioned successfully")
            
            # Resolve public IP
            ip_cmd = [
                "gcloud", "compute", "instances", "describe", vm_name,
                "--project", project, "--zone", zone,
                "--format", "get(networkInterfaces[0].accessConfigs[0].natIP)"
            ]
            self.public_ip = run_cmd(ip_cmd)
            self.instance_id = vm_name
            log(f"GCP VM Public IP resolved: {self.public_ip}")
            
        finally:
            os.unlink(temp_cloud_init)

    def deploy_azure(self) -> None:
        """Deploy to Microsoft Azure using Azure CLI."""
        log("Initiating Microsoft Azure deployment sequence...")
        resource_group = self._get_env("AZURE_RESOURCE_GROUP", "remote-dev-rg")
        location = self._get_env("AZURE_LOCATION", "eastus")
        vm_size = self._get_env("AZURE_VM_SIZE", "Standard_D4s_v5")
        vm_name = self._get_env("VM_NAME", "remote-dev-server")
        disk_size = self._get_env("VM_BOOT_VOLUME_GB", "100")
        admin_user = self._get_env("ADMIN_USERNAME", "devuser")
        
        # Write cloud-init to temp file
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
            tf.write((self.project_dir / "configs" / "cloud-init.yaml").read_bytes())
            temp_cloud_init = tf.name
            
        try:
            # Create Resource Group if not exists
            log(f"Creating Azure Resource Group: {resource_group}...")
            run_cmd(["az", "group", "create", "--name", resource_group, "--location", location], check=False)
            
            # Launch VM
            log(f"Launching Azure VM instance ({vm_size}) in Resource Group {resource_group}...")
            cmd = [
                "az", "vm", "create",
                "--resource-group", resource_group,
                "--name", vm_name,
                "--image", "Canonical:UbuntuServer:24.04-LTS:latest",
                "--size", vm_size,
                "--admin-username", admin_user,
                "--custom-data", temp_cloud_init,
                "--os-disk-size-gb", disk_size,
                "--public-ip-sku", "Standard"
            ]
            run_cmd(cmd)
            log("Azure VM created successfully")
            
            # Open ports in Network Security Group
            log("Configuring Network Security Group rules in Azure...")
            run_cmd([
                "az", "vm", "open-port",
                "--resource-group", resource_group,
                "--name", vm_name,
                "--port", f"22,80,{self._get_env('WG_PORT', '51820')}"
            ])
            
            # Resolve Public IP
            ip_cmd = [
                "az", "vm", "show",
                "-d", "-g", resource_group, "-n", vm_name,
                "--query", "publicIps", "-o", "tsv"
            ]
            self.public_ip = run_cmd(ip_cmd)
            self.instance_id = vm_name
            log(f"Azure VM Public IP resolved: {self.public_ip}")
            
        finally:
            os.unlink(temp_cloud_init)

    def write_client_wireguard_configs(self) -> None:
        keys_dir = self.project_dir / "configs" / "wireguard"
        # Split-tunnel by default: only the VPN subnet routes through wg0 so the
        # client's normal internet path and DNS are left untouched. A DNS line in
        # a split-tunnel config hijacks macOS system resolvers and breaks name
        # resolution once the tunnel is up, so DNS is omitted unless explicitly set.
        full_tunnel = self._env_bool("WG_FULL_TUNNEL", False)
        wg_dns = self._get_env("WG_DNS", "")
        wg_network = self._get_env("WG_NETWORK", "10.200.200.0/24")
        wg_port = self._get_env("WG_PORT", "51820")
        for dev in self.developers:
            path = keys_dir / f"client_{dev['name']}.conf"
            cfg = render_wg_client_config(
                private_key=dev["private_key"],
                address=dev["wg_ip"],
                server_public_key=self.wg_server_public_key,
                endpoint=f"{self.public_ip}:{wg_port}",
                wg_network=wg_network,
                full_tunnel=full_tunnel,
                dns=wg_dns,
            )
            path.write_text(cfg, encoding="utf-8")
            path.chmod(0o600)
            log(f"Generated WireGuard client config for {dev['name']}: {path}")
            
            if dev['name'] == self._get_env("ADMIN_USERNAME", "devuser"):
                shutil.copy(path, keys_dir / "client.conf")
                (keys_dir / "client.conf").chmod(0o600)
                
            if shutil.which("qrencode"):
                qr_path = keys_dir / f"client_{dev['name']}-qr.txt"
                qr = run_cmd(["bash", "-lc", f"qrencode -t ansiutf8 < '{path}'"], check=False)
                if qr:
                    qr_path.write_text(qr, encoding="utf-8")

    def save_deployment_info(self) -> None:
        out = self.project_dir / "configs" / "deployment-info.txt"
        r_name = self._get_env("VM_NAME", "remote-dev-server")
        shape = self._get_env("VM_SHAPE", "VM.Standard.E6.Flex")
        
        txt = (
            "# Remote Development Server Deployment\n"
            "# ====================================\n"
            f"# Deployed: {dt.datetime.now()}\n\n"
            f"Cloud Provider: {self.provider}\n"
            f"Instance ID: {self.instance_id}\n"
            f"Instance Name: {r_name}\n"
            f"Public IP: {self.public_ip}\n"
            f"Shape/Size: {shape}\n\n"
            "# VPN & Dashboard Access\n"
            f"WireGuard Port: {self._get_env('WG_PORT', '51820')}\n"
            f"Dev Dashboard: http://{self._get_env('WG_SERVER_IP', '10.200.200.1')} (Connect via VPN first)\n\n"
            "# Developer Workspaces\n"
        )
        
        ssh_key = Path(self._get_env("SSH_PUBLIC_KEY_PATH", "~/.ssh/id_rsa.pub")).expanduser()
        ssh_priv = Path(str(ssh_key).removesuffix(".pub"))
        
        for dev in self.developers:
            txt += (
                f"## Developer: {dev['name']}\n"
                f"  SSH: ssh -i {ssh_priv} {dev['name']}@{self.public_ip}\n"
                f"  WireGuard Config: configs/wireguard/client_{dev['name']}.conf\n"
                f"  VPN IP: {dev['wg_ip']}\n"
                f"  code-server (Web IDE): http://{self._get_env('WG_SERVER_IP', '10.200.200.1')}:{dev['code_server_port']}\n\n"
            )
            
        out.write_text(txt, encoding="utf-8")
        log(f"Saved deployment info: {out}")

    def verify_ssh(self) -> None:
        if self.args.skip_ssh_verify:
            return
        ssh_key = str(Path(self._get_env("SSH_PUBLIC_KEY_PATH", "~/.ssh/id_rsa.pub")).expanduser()).removesuffix(".pub")
        target = f"{self._get_env('ADMIN_USERNAME', 'devuser')}@{self.public_ip}"
        
        log("Verifying SSH connectivity...")
        ssh_ctrl_dir = Path(tempfile.mkdtemp(prefix="multi-dev-ssh-"))
        ssh_ctrl_sock = ssh_ctrl_dir / "ctrl-%r@%h:%p"
        
        ssh_base_args = [
            "ssh", "-i", ssh_key,
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={ssh_ctrl_sock}",
            "-o", "ControlPersist=300"
        ]
        
        try:
            for attempt in range(1, 13):
                rc, out, err = run_cmd_no_raise(ssh_base_args + [target, "echo SSH OK"])
                if rc == 0:
                    log("SSH connectivity verified successfully")
                    return
                info(f"Waiting for SSH... (attempt {attempt}/12)")
                time.sleep(10)
        finally:
            run_cmd_no_raise(["ssh", "-o", f"ControlPath={ssh_ctrl_sock}", "-O", "exit", target])
            shutil.rmtree(ssh_ctrl_dir, ignore_errors=True)
            
        warn("SSH verification timed out. The server may still be initializing packages.")

    def run_ansible_playbook(self) -> None:
        """Run post-deployment Ansible configuration on the target VM."""
        if shutil.which("ansible-playbook") is None:
            warn("ansible-playbook not found in local PATH. Skipping post-deployment Ansible automation.")
            warn("To run configuration manually, please install Ansible locally and execute:")
            warn(f"  ansible-playbook -i configs/hosts.ini --extra-vars @configs/ansible_vars.json ansible/playbook.yml")
            return

        log("Initiating post-deployment Ansible configuration...")
        ssh_key = Path(self._get_env("SSH_PUBLIC_KEY_PATH", "~/.ssh/id_rsa.pub")).expanduser()
        ssh_priv = str(ssh_key).removesuffix(".pub")
        admin_user = self._get_env("ADMIN_USERNAME", "devuser")

        configs_dir = self.project_dir / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)
        inv_path = configs_dir / "hosts.ini"
        
        inv_content = (
            "[devserver]\n"
            f"{self.public_ip} ansible_user={admin_user} ansible_ssh_private_key_file={ssh_priv} "
            "ansible_ssh_extra_args='-o StrictHostKeyChecking=no'\n"
        )
        inv_path.write_text(inv_content, encoding="utf-8")
        log(f"Generated Ansible inventory: {inv_path}")

        import json
        devs_vars = []
        for dev in self.developers:
            devs_vars.append({
                "name": dev["name"],
                "code_server_port": dev["code_server_port"],
                "wg_ip": dev["wg_ip"]
            })

        extra_vars = {
            "developers": devs_vars,
            "wg_server_ip": self._get_env("WG_SERVER_IP", "10.200.200.1"),
            "wg_network": self._get_env("WG_NETWORK", "10.200.200.0/24"),
            "node_version": self._get_env("NODE_VERSION", "20"),
            "install_cursor": self._env_bool("INSTALL_CURSOR", True),
            "install_claude_code": self._env_bool("INSTALL_CLAUDE_CODE", True),
            "install_codex": self._env_bool("INSTALL_CODEX", True),
            "install_gemini": self._env_bool("INSTALL_GEMINI", True),
            "install_code_server": self._env_bool("INSTALL_CODE_SERVER", True),
            "install_multillm_gateway": self._env_bool("INSTALL_MULTILLM_GATEWAY", True),
            "multillm_gateway_port": int(self._get_env("MULTILLM_GATEWAY_PORT", "8080")),
            "multillm_collect_interval_min": int(self._get_env("MULTILLM_COLLECT_INTERVAL_MIN", "15")),
            "multillm_user_budgets": self._get_env("MULTILLM_USER_BUDGETS", ""),
            "multillm_install_source": self._get_env("MULTILLM_INSTALL_SOURCE", "/opt/multillm"),
        }

        extra_vars_file = configs_dir / "ansible_vars.json"
        extra_vars_file.write_text(json.dumps(extra_vars), encoding="utf-8")
        log(f"Generated Ansible variables: {extra_vars_file}")

        ansible_cmd = [
            "ansible-playbook",
            "-i", str(inv_path),
            "--extra-vars", f"@{extra_vars_file}",
            str(self.project_dir / "ansible" / "playbook.yml")
        ]

        log("Executing Ansible playbook (this may take a few minutes as it installs GUI, desktops, XRDP and dev tools)...")
        try:
            subprocess.run(ansible_cmd, check=True)
            log("Ansible configuration completed successfully! Remote Dev Server is ready.")
        except subprocess.CalledProcessError as exc:
            warn(f"Ansible playbook execution completed with errors: {exc}")
            warn("You can manually troubleshoot and re-run configuration using:")
            warn(f"  {' '.join(ansible_cmd)}")

    def print_summary(self) -> None:
        print("")
        print(f"{GREEN}╔══════════════════════════════════════════════════════════════════╗{NC}")
        print(f"{GREEN}║           DEPLOYMENT SEQUENCE INITIATED!                        ║{NC}")
        print(f"{GREEN}╠══════════════════════════════════════════════════════════════════╣{NC}")
        print(f"{GREEN}║{NC} Provider:  {CYAN}{self.provider}{NC}")
        print(f"{GREEN}║{NC} Instance:  {CYAN}{self._get_env('VM_NAME', 'remote-dev-server')}{NC}")
        print(f"{GREEN}║{NC} Public IP: {CYAN}{self.public_ip}{NC}")
        print(f"{GREEN}╠══════════════════════════════════════════════════════════════════╣{NC}")
        print(f"{GREEN}║{NC} Dev Dashboard: {CYAN}http://{self._get_env('WG_SERVER_IP', '10.200.200.1')}{NC} (via VPN)")
        print(f"{GREEN}╚══════════════════════════════════════════════════════════════════╝{NC}")
        print("")

    def execute(self) -> None:
        self.check_prerequisites()
        self.build_developers_list()
        
        if not self.args.yes:
            print(f"\n{BLUE}Deployment Target: {self.provider}{NC}")
            ans = input("Run multi-cloud deployment? (y/N): ").strip().lower()
            if ans not in {"y", "yes"}:
                fail("Deployment cancelled by user.")
                
        self.generate_wireguard_keys()
        self.generate_cloud_init()
        
        # Branch deployment based on selected cloud provider
        if self.provider == "OCI":
            self.deploy_oci()
        elif self.provider == "AWS":
            self.deploy_aws()
        elif self.provider == "GCP":
            self.deploy_gcp()
        elif self.provider == "AZURE":
            self.deploy_azure()
        else:
            fail(f"Unsupported cloud provider: {self.provider}")
            
        self.write_client_wireguard_configs()
        self.save_deployment_info()
        self.verify_ssh()
        self.run_ansible_playbook()
        self.print_summary()


def main() -> int:
    print(f"{BLUE}")
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║     OCI/AWS/GCP/Azure Remote Dev Server Deployer (Python)        ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(f"{NC}")
    
    p = argparse.ArgumentParser(description="Multi-Cloud remote dev VM deployer")
    p.add_argument("--env-file", default=".env.local", help="Path to env file")
    p.add_argument("--yes", action="store_true", help="Non-interactive skip confirmation")
    p.add_argument("--replace-existing", action="store_true", help="Replace existing OCI VM")
    p.add_argument("--skip-ssh-verify", action="store_true", help="Skip SSH check")
    p.add_argument("--profile", default=None, help="OCI CLI profile name")
    args = p.parse_args()
    
    try:
        MultiCloudDeployer(args).execute()
        return 0
    except Exception as exc:
        print(f"{RED}[FATAL]{NC} {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
