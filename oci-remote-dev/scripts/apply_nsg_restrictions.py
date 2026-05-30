#!/usr/bin/env python3
"""
Apply source-restricted ingress policy using OCI Python SDK.

Creates (or reuses) an NSG, adds ingress TCP rules for the provided CIDRs/ports,
attaches NSG to the instance VNIC, and tightens the subnet security list so it
does not bypass NSG restrictions.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Callable, Iterable, List

import oci


def retry(label: str, fn: Callable, attempts: int = 6):
    last = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - network/runtime path
            last = exc
            if i < attempts:
                time.sleep(min(3 * i, 15))
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last}")


def has_tcp_rule(rules: Iterable, source: str, port: int) -> bool:
    for rule in rules:
        if getattr(rule, "direction", "INGRESS") != "INGRESS":
            continue
        if str(rule.protocol) != "6":
            continue
        if rule.source != source:
            continue
        tcp = rule.tcp_options
        if not tcp or not tcp.destination_port_range:
            continue
        pr = tcp.destination_port_range
        if pr.min == port and pr.max == port:
            return True
    return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Apply NSG source restrictions")
    p.add_argument("--profile", default="oci4cca")
    p.add_argument("--compartment-id", required=True)
    p.add_argument("--instance-id", required=True)
    p.add_argument("--vcn-id", required=True)
    p.add_argument("--security-list-id", required=True)
    p.add_argument("--nsg-name", default="remote-dev-restricted-nsg")
    p.add_argument("--source", action="append", required=True, help="CIDR source; repeat flag for multiple")
    p.add_argument("--port", action="append", type=int, required=True, help="TCP port; repeat flag for multiple")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    cfg = oci.config.from_file(profile_name=args.profile)
    compute = oci.core.ComputeClient(cfg)
    network = oci.core.VirtualNetworkClient(cfg)

    inst = retry("get_instance", lambda: compute.get_instance(args.instance_id).data)
    if inst.lifecycle_state != "RUNNING":
        raise RuntimeError(f"Instance is not RUNNING: {inst.lifecycle_state}")

    attachment = retry(
        "list_vnic_attachments",
        lambda: compute.list_vnic_attachments(
            compartment_id=args.compartment_id, instance_id=args.instance_id
        ).data[0],
    )
    vnic_id = attachment.vnic_id
    vnic = retry("get_vnic", lambda: network.get_vnic(vnic_id).data)

    nsgs = retry(
        "list_network_security_groups",
        lambda: network.list_network_security_groups(
            compartment_id=args.compartment_id, vcn_id=args.vcn_id
        ).data,
    )
    existing = [n for n in nsgs if n.display_name == args.nsg_name]
    if existing:
        nsg_id = existing[0].id
    else:
        nsg_id = retry(
            "create_network_security_group",
            lambda: network.create_network_security_group(
                oci.core.models.CreateNetworkSecurityGroupDetails(
                    compartment_id=args.compartment_id,
                    vcn_id=args.vcn_id,
                    display_name=args.nsg_name,
                )
            ).data.id,
        )

    nsg_rules = retry(
        "list_nsg_rules",
        lambda: network.list_network_security_group_security_rules(
            network_security_group_id=nsg_id
        ).data,
    )
    additions: List[oci.core.models.AddSecurityRuleDetails] = []
    for src in args.source:
        for port in args.port:
            if has_tcp_rule(nsg_rules, src, port):
                continue
            additions.append(
                oci.core.models.AddSecurityRuleDetails(
                    direction="INGRESS",
                    protocol="6",
                    source=src,
                    source_type="CIDR_BLOCK",
                    is_stateless=False,
                    description=f"allow {src} tcp/{port}",
                    tcp_options=oci.core.models.TcpOptions(
                        destination_port_range=oci.core.models.PortRange(min=port, max=port)
                    ),
                )
            )
    if additions:
        retry(
            "add_nsg_rules",
            lambda: network.add_network_security_group_security_rules(
                network_security_group_id=nsg_id,
                add_network_security_group_security_rules_details=oci.core.models.AddNetworkSecurityGroupSecurityRulesDetails(
                    security_rules=additions
                ),
            ),
        )

    nsg_ids = list(vnic.nsg_ids or [])
    if nsg_id not in nsg_ids:
        nsg_ids.append(nsg_id)
        retry(
            "update_vnic_nsgs",
            lambda: network.update_vnic(
                vnic_id=vnic_id, update_vnic_details=oci.core.models.UpdateVnicDetails(nsg_ids=nsg_ids)
            ),
        )

    sl = retry("get_security_list", lambda: network.get_security_list(args.security_list_id).data)
    new_ingress = []
    for rule in sl.ingress_security_rules or []:
        keep = False
        if str(rule.protocol) == "1":
            keep = True
        if str(rule.protocol) == "6" and rule.source in args.source:
            tcp = rule.tcp_options
            if tcp and tcp.destination_port_range:
                pr = tcp.destination_port_range
                if pr.min in args.port and pr.min == pr.max:
                    keep = True
        if keep:
            new_ingress.append(rule)

    for src in args.source:
        for port in args.port:
            if has_tcp_rule(new_ingress, src, port):
                continue
            new_ingress.append(
                oci.core.models.IngressSecurityRule(
                    protocol="6",
                    source=src,
                    source_type="CIDR_BLOCK",
                    is_stateless=False,
                    description=f"sl allow {src} tcp/{port}",
                    tcp_options=oci.core.models.TcpOptions(
                        destination_port_range=oci.core.models.PortRange(min=port, max=port)
                    ),
                )
            )

    retry(
        "update_security_list",
        lambda: network.update_security_list(
            security_list_id=args.security_list_id,
            update_security_list_details=oci.core.models.UpdateSecurityListDetails(
                display_name=sl.display_name,
                ingress_security_rules=new_ingress,
                egress_security_rules=sl.egress_security_rules,
                freeform_tags=sl.freeform_tags,
                defined_tags=sl.defined_tags,
            ),
        ),
    )

    final_nsg_rules = retry(
        "final_nsg_rules",
        lambda: network.list_network_security_group_security_rules(network_security_group_id=nsg_id).data,
    )
    final_sl = retry("final_security_list", lambda: network.get_security_list(args.security_list_id).data)
    summary = {
        "nsg_id": nsg_id,
        "vnic_id": vnic_id,
        "attached_nsg_ids": nsg_ids,
        "sources": args.source,
        "ports": args.port,
        "nsg_ingress": sorted(
            [
                {
                    "source": r.source,
                    "port": r.tcp_options.destination_port_range.min,
                    "protocol": r.protocol,
                }
                for r in final_nsg_rules
                if r.direction == "INGRESS" and str(r.protocol) == "6" and r.tcp_options and r.tcp_options.destination_port_range
            ],
            key=lambda x: (x["source"], x["port"]),
        ),
        "security_list_ingress": sorted(
            [
                {
                    "source": r.source,
                    "port": (
                        r.tcp_options.destination_port_range.min
                        if r.tcp_options and r.tcp_options.destination_port_range
                        else "icmp"
                    ),
                    "protocol": r.protocol,
                }
                for r in (final_sl.ingress_security_rules or [])
            ],
            key=lambda x: (str(x["source"]), str(x["port"])),
        ),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
