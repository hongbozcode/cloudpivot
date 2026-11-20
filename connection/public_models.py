import asyncio
import socket
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

import httpx


_IPV6_TRANSITION_RANGES = tuple(map(ip_network, ('64:ff9b::/96', '64:ff9b:1::/48', '2002::/16', '2001::/32')))


def public_address(host: str, port: int) -> str:
    try:
        addresses = [ip_address(entry[4][0]) for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    except (OSError, ValueError) as exc:
        raise ValueError('无法解析服务地址。') from exc
    if not addresses or any(
        not address.is_global or address.is_multicast or address.is_reserved
        or (address.version == 6 and (
            address.ipv4_mapped or address.scope_id or any(address in network for network in _IPV6_TRANSITION_RANGES)
        ))
        for address in addresses
    ):
        raise ValueError('服务必须解析到公网地址。')
    return str(addresses[0])


def validate_base_url(value: str) -> str:
    return validate_public_https_url(value).rstrip('/')


def validate_public_https_url(value: str, *, allow_query: bool = False) -> str:
    if any(char.isspace() or not char.isprintable() or char == '\\' for char in value):
        raise ValueError('请输入有效的服务地址。')
    raw = urlsplit(value)
    try:
        parsed = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise ValueError('请输入有效的服务地址。') from exc
    if (
        parsed.scheme != 'https' or not parsed.host or raw.username is not None
        or parsed.port == 0 or '#' in value or (not allow_query and '?' in value)
    ):
        raise ValueError('请输入不含凭据和片段的公网 HTTPS 地址。')
    public_address(parsed.raw_host.decode('ascii'), parsed.port or 443)
    return str(parsed)


def _target(request: httpx.Request, base: httpx.URL) -> tuple[str, int]:
    if (request.url.scheme, request.url.raw_host, request.url.port) != (base.scheme, base.raw_host, base.port):
        raise httpx.ConnectError('请求超出了配置的服务地址。', request=request)
    return base.raw_host.decode('ascii'), base.port or 443


def _pinned(request: httpx.Request, host: str, address: str) -> httpx.Request:
    # Connect to the vetted address and retain the provider's Host, TLS SNI and certificate name.
    return httpx.Request(
        request.method, request.url.copy_with(host=address), headers=request.headers,
        stream=request.stream, extensions={**request.extensions, 'sni_hostname': host},
    )


class PublicModelTransport(httpx.HTTPTransport):
    def __init__(self, base: httpx.URL):
        super().__init__(trust_env=False, limits=httpx.Limits(max_keepalive_connections=0))
        self.base = base

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host, port = _target(request, self.base)
        return super().handle_request(_pinned(request, host, public_address(host, port)))


class AsyncPublicTransport(httpx.AsyncHTTPTransport):
    def __init__(self, base: httpx.URL):
        super().__init__(trust_env=False, limits=httpx.Limits(max_keepalive_connections=0))
        self.base = base

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host, port = _target(request, self.base)
        address = await asyncio.to_thread(public_address, host, port)
        return await super().handle_async_request(_pinned(request, host, address))


def public_clients(base_url: str) -> tuple[httpx.Client, httpx.AsyncClient]:
    base = httpx.URL(base_url)
    return (
        httpx.Client(transport=PublicModelTransport(base), trust_env=False),
        httpx.AsyncClient(transport=AsyncPublicTransport(base), trust_env=False),
    )
