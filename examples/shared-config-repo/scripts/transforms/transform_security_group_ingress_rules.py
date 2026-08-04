"""Convert SSH CIDR inputs into module-compatible ingress rule mappings."""

import ipaddress
import logging
from typing import Any

LOGGER = logging.getLogger("transforms")


def _parse_cidrs(
    raw: Any,
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    if isinstance(raw, str):
        items = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, list):
        items = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    else:
        raise ValueError("ssh_ingress_cidrs must be a comma-separated string or list")

    LOGGER.debug("Parsing %d CIDR item(s)", len(items))
    cidrs = []
    for item in items:
        cidr = ipaddress.ip_network(item, strict=False)
        LOGGER.debug("Parsed CIDR %s", cidr.with_prefixlen)
        cidrs.append(cidr)
    return cidrs


def transform(value: Any, **context: Any) -> dict[str, dict[str, str | int]]:
    rules = {}
    for idx, cidr in enumerate(_parse_cidrs(value)):
        rule = {
            "description": f"SSH from {cidr.with_prefixlen}",
            "from_port": 22,
            "to_port": 22,
            "ip_protocol": "tcp",
        }
        key = f"ssh_{idx}"
        if cidr.version == 4:
            rule["cidr_ipv4"] = cidr.with_prefixlen
        else:
            rule["cidr_ipv6"] = cidr.with_prefixlen
        rules[key] = rule
        LOGGER.debug("Built ingress rule key=%s rule=%r", key, rule)

    LOGGER.info("Generated %d SSH ingress rule(s)", len(rules))
    return rules
