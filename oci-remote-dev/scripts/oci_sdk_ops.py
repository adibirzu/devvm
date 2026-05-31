#!/usr/bin/env python3
"""
OCI Python SDK helper for oci-remote-dev workflows.

This script is intentionally focused on the high-signal operations that were
error-prone with ad-hoc OCI CLI usage:
 - Profile/tenancy validation
 - Instance lifecycle status + start/stop
 - Primary VNIC and public/private IP discovery
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import oci


def _read_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        data[key] = value
    return data


def _read_deployment_info(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def _sdk_config(
    profile: str, config_file: Path, region: Optional[str]
) -> Dict[str, str]:
    cfg = oci.config.from_file(file_location=str(config_file), profile_name=profile)
    if region:
        cfg["region"] = region
    return cfg


def _resolve_tenancy_id(
    args: argparse.Namespace, env_data: Dict[str, str], cfg: Dict[str, str]
) -> str:
    tenancy_id = (
        args.tenancy_id or env_data.get("OCI_TENANCY_OCID") or cfg.get("tenancy")
    )
    if not tenancy_id:
        raise ValueError(
            "Tenancy OCID is required (arg --tenancy-id, .env, or OCI config)."
        )
    return tenancy_id


def _resolve_instance_id(
    args: argparse.Namespace,
    env_data: Dict[str, str],
    deployment: Dict[str, str],
) -> str:
    instance_id = (
        args.instance_id
        or deployment.get("Instance OCID")
        or env_data.get("INSTANCE_OCID")
    )
    if not instance_id:
        raise ValueError(
            "Instance OCID is required (arg --instance-id or configs/deployment-info.txt)."
        )
    return instance_id


def _to_dict(model: Any) -> Dict[str, Any]:
    return oci.util.to_dict(model)  # type: ignore[arg-type]


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_profile_check(
    args: argparse.Namespace, cfg: Dict[str, str], env_data: Dict[str, str]
) -> int:
    tenancy_id = _resolve_tenancy_id(args, env_data, cfg)
    identity = oci.identity.IdentityClient(cfg)
    region_subs = identity.list_region_subscriptions(tenancy_id=tenancy_id).data
    home_region = next((x.region_name for x in region_subs if x.is_home_region), None)

    _emit(
        {
            "ok": True,
            "profile": args.profile,
            "tenancy_id": tenancy_id,
            "user_id": cfg.get("user"),
            "configured_region": cfg.get("region"),
            "home_region": home_region,
            "region_count": len(region_subs),
        }
    )
    return 0


def _get_instance(compute: oci.core.ComputeClient, instance_id: str) -> Dict[str, Any]:
    return _to_dict(compute.get_instance(instance_id=instance_id).data)


def _wait_for_state(
    compute: oci.core.ComputeClient,
    instance_id: str,
    target_state: str,
    timeout: int,
) -> Dict[str, Any]:
    resp = oci.wait_until(
        compute,
        compute.get_instance(instance_id=instance_id),
        evaluate_response=lambda r: r.data.lifecycle_state == target_state,
        max_wait_seconds=timeout,
        max_interval_seconds=10,
    )
    return _to_dict(resp.data)


def cmd_instance_status(
    args: argparse.Namespace,
    cfg: Dict[str, str],
    env_data: Dict[str, str],
    deployment: Dict[str, str],
) -> int:
    instance_id = _resolve_instance_id(args, env_data, deployment)
    compute = oci.core.ComputeClient(cfg)
    instance = _get_instance(compute, instance_id)
    _emit({"ok": True, "instance_id": instance_id, "instance": instance})
    return 0


def cmd_instance_action(
    action: str,
    args: argparse.Namespace,
    cfg: Dict[str, str],
    env_data: Dict[str, str],
    deployment: Dict[str, str],
) -> int:
    instance_id = _resolve_instance_id(args, env_data, deployment)
    compute = oci.core.ComputeClient(cfg)
    current = _get_instance(compute, instance_id)
    current_state = current.get("lifecycle_state")

    target_state = "RUNNING" if action == "START" else "STOPPED"
    already_target = (action == "START" and current_state == "RUNNING") or (
        action == "STOP" and current_state == "STOPPED"
    )

    if not already_target:
        compute.instance_action(instance_id=instance_id, action=action)

    after = (
        _wait_for_state(compute, instance_id, target_state, args.wait_timeout)
        if args.wait
        else _get_instance(compute, instance_id)
    )
    _emit(
        {
            "ok": True,
            "instance_id": instance_id,
            "action": action,
            "already_target_state": already_target,
            "before_state": current_state,
            "after_state": after.get("lifecycle_state"),
            "instance": after,
        }
    )
    return 0


def cmd_instance_ip(
    args: argparse.Namespace,
    cfg: Dict[str, str],
    env_data: Dict[str, str],
    deployment: Dict[str, str],
) -> int:
    instance_id = _resolve_instance_id(args, env_data, deployment)
    compute = oci.core.ComputeClient(cfg)
    network = oci.core.VirtualNetworkClient(cfg)

    instance = _get_instance(compute, instance_id)
    compartment_id = instance["compartment_id"]

    attachments = compute.list_vnic_attachments(
        compartment_id=compartment_id, instance_id=instance_id
    ).data
    if not attachments:
        raise RuntimeError(f"No VNIC attachments found for instance {instance_id}")

    primary = sorted(attachments, key=lambda x: x.time_created)[0]
    vnic = network.get_vnic(vnic_id=primary.vnic_id).data
    _emit(
        {
            "ok": True,
            "instance_id": instance_id,
            "vnic_id": primary.vnic_id,
            "public_ip": vnic.public_ip,
            "private_ip": vnic.private_ip,
            "subnet_id": vnic.subnet_id,
            "hostname_label": vnic.hostname_label,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OCI Python SDK ops helper")
    parser.add_argument(
        "--profile",
        default=None,
        help="OCI profile name (default: OCI_PROFILE/.env/DEFAULT)",
    )
    parser.add_argument(
        "--config-file", default="~/.oci/config", help="Path to OCI config file"
    )
    parser.add_argument("--region", default=None, help="Override OCI region")
    parser.add_argument("--env-file", default=".env", help="Path to env file")
    parser.add_argument(
        "--deployment-info",
        default="configs/deployment-info.txt",
        help="Path to deployment info file",
    )
    parser.add_argument("--tenancy-id", default=None, help="Tenancy OCID override")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("profile-check", help="Validate profile/tenancy access via SDK")

    p_status = sub.add_parser("instance-status", help="Get instance details")
    p_status.add_argument("--instance-id", default=None, help="Instance OCID")

    p_start = sub.add_parser("instance-start", help="Start instance")
    p_start.add_argument("--instance-id", default=None, help="Instance OCID")
    p_start.add_argument("--wait", action="store_true", help="Wait for target state")
    p_start.add_argument(
        "--wait-timeout", type=int, default=900, help="Wait timeout in seconds"
    )

    p_stop = sub.add_parser("instance-stop", help="Stop instance")
    p_stop.add_argument("--instance-id", default=None, help="Instance OCID")
    p_stop.add_argument("--wait", action="store_true", help="Wait for target state")
    p_stop.add_argument(
        "--wait-timeout", type=int, default=900, help="Wait timeout in seconds"
    )

    p_ip = sub.add_parser("instance-ip", help="Get instance primary VNIC IPs")
    p_ip.add_argument("--instance-id", default=None, help="Instance OCID")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_file = Path(args.env_file).expanduser()
    deployment_file = Path(args.deployment_info).expanduser()
    config_file = Path(args.config_file).expanduser()

    env_data = _read_env_file(env_file)
    deployment = _read_deployment_info(deployment_file)

    profile = args.profile or env_data.get("OCI_PROFILE") or "DEFAULT"
    args.profile = profile

    try:
        cfg = _sdk_config(
            profile=profile,
            config_file=config_file,
            region=args.region or env_data.get("OCI_REGION"),
        )

        if args.command == "profile-check":
            return cmd_profile_check(args, cfg, env_data)
        if args.command == "instance-status":
            return cmd_instance_status(args, cfg, env_data, deployment)
        if args.command == "instance-start":
            return cmd_instance_action("START", args, cfg, env_data, deployment)
        if args.command == "instance-stop":
            return cmd_instance_action("STOP", args, cfg, env_data, deployment)
        if args.command == "instance-ip":
            return cmd_instance_ip(args, cfg, env_data, deployment)

        parser.error(f"Unsupported command: {args.command}")
        return 2
    except Exception as exc:  # pylint: disable=broad-except
        _emit({"ok": False, "error": str(exc), "command": args.command})
        return 1


if __name__ == "__main__":
    sys.exit(main())
