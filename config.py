"""Archival configuration adapter, NOT the deployed database-backed config module.
Only explicit caller environment values are used. Missing passwords fail closed.
"""
import os
import ipaddress
import re
from typing import Optional
from typing import Any
ENV_MAPPINGS = {"API_PASSWORD": "api_password", "PANEL_PASSWORD": "panel_password", "PROXY": "proxy"}
_ip_whitelist_cache: dict = {"fingerprint": None, "result": ([], [])}

async def get_proxy_config():
    return os.environ.get("PROXY") or None
async def get_api_password():
    value = os.environ.get("API_PASSWORD")
    if not value:
        raise RuntimeError("Explicit API_PASSWORD required; no archive default")
    return value
async def get_panel_password():
    value = os.environ.get("PANEL_PASSWORD")
    if not value:
        raise RuntimeError("Explicit PANEL_PASSWORD required; no archive default")
    return value
async def get_oauth_proxy_url():
    return "https://oauth2.googleapis.com"
async def get_googleapis_proxy_url():
    return "https://www.googleapis.com"

async def get_usage_log_enabled():
    """No runtime persistence is enabled by the archive adapter."""
    return False

def parse_ip_whitelist_line(line: str):
    """
    解析单条白名单条目为 (lo, hi, version) 网络对象；无效时抛出 ValueError。

    支持格式（IPv4 / IPv6）：
    - 单个 IP: 192.0.2.1 / ::1
    - CIDR 网段: 192.0.2.0/24 / 2001:db8::/32
    - IP 起止范围（- 或 ~ 分隔）: 192.0.2.1-192.0.2.100 / ::1-::10
    """
    line = str(line).strip()
    if not line:
        raise ValueError("空条目")

    for sep in ("~", "-"):
        if sep in line:
            start_str, _, end_str = line.partition(sep)
            if start_str.strip() and end_str.strip():
                start = ipaddress.ip_address(start_str.strip())
                end = ipaddress.ip_address(end_str.strip())
                if start.version != end.version:
                    raise ValueError(f"起止 IP 版本不一致: {line}")
                if int(start) > int(end):
                    start, end = end, start
                return start, end, start.version

    if "/" in line:
        net = ipaddress.ip_network(line, strict=False)
        return net.network_address, net.broadcast_address, net.version

    addr = ipaddress.ip_address(line)
    return addr, addr, addr.version

def compile_ip_whitelist(entries: list):
    """
    解析白名单条目列表为规则列表（带缓存）。

    Returns:
        (rules, invalid)——rules 为 [(lo, hi, version), ...]，invalid 为解析失败的原始条目。
    """
    fingerprint = tuple(str(e) for e in entries)
    if _ip_whitelist_cache["fingerprint"] == fingerprint:
        return _ip_whitelist_cache["result"]

    rules = []
    invalid = []
    for entry in entries:
        entry = str(entry).strip()
        if not entry:
            continue
        try:
            rules.append(parse_ip_whitelist_line(entry))
        except ValueError:
            invalid.append(entry)

    result = (rules, invalid)
    _ip_whitelist_cache["fingerprint"] = fingerprint
    _ip_whitelist_cache["result"] = result
    return result

def ip_in_whitelist(client_ip: str, rules: list) -> bool:
    """判断客户端 IP 是否命中白名单规则（IPv4/IPv6 均支持）"""
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(lo.version == addr.version and lo <= addr <= hi for lo, hi, _ in rules)
