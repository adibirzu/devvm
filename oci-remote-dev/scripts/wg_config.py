"""Shared WireGuard client-config rendering.

Single source of truth for both deployers (``deploy_multicloud.py`` and
``deploy_sdk.py``) so the two paths can never drift apart again — drift between
them is exactly what produced the macOS split-tunnel/DNS routing bug.
"""

from __future__ import annotations


def render_wg_client_config(
    *,
    private_key: str,
    address: str,
    server_public_key: str,
    endpoint: str,
    wg_network: str,
    full_tunnel: bool = False,
    dns: str = "",
) -> str:
    """Render a WireGuard client config.

    Split tunnel is the default: only ``wg_network`` is routed through the tunnel
    and no DNS line is emitted, because a DNS entry in a split-tunnel config
    hijacks macOS system resolvers and breaks name resolution once the tunnel is
    up. Pass ``full_tunnel=True`` to route all traffic (0.0.0.0/0, ::/0); a DNS
    line is only emitted when ``dns`` is a non-empty string.
    """
    allowed_ips = "0.0.0.0/0, ::/0" if full_tunnel else wg_network
    dns_line = f"DNS = {dns.strip()}\n" if dns.strip() else ""
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {address}/24\n"
        f"{dns_line}\n"
        "[Peer]\n"
        f"PublicKey = {server_public_key}\n"
        f"Endpoint = {endpoint}\n"
        f"AllowedIPs = {allowed_ips}\n"
        "PersistentKeepalive = 25\n"
    )
