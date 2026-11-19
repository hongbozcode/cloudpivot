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
        raise ValueError('无法解析模型服务地址。') from exc
    if not addresses or any(
        not address.is_global or address.is_multicast or address.is_reserved
        or (address.version == 6 and (
            address.ipv4_mapped or address.scope_id or any(address in network for network in _IPV6_TRANSITION_RANGES)
        ))
        for address in addresses
    ):
        raise ValueError('模型服务必须解析到公网地址。')
    return str(addresses[0])


def validate_base_url(value: str) -> str:
    if any(char.isspace() or not char.isprintable() or char == '\\' for char in value):
        raise ValueError('请输入有效的模型服务 Base URL。')
    raw = urlsplit(value)
    parsed = httpx.URL(value)
    if (
        parsed.scheme != 'https' or not parsed.host or raw.username is not None
        or parsed.query or parsed.fragment or '?' in value or '#' in value
    ):
        raise ValueError('请输入公网 HTTPS Base URL，地址中不能包含凭据、查询参数或片段。')
    public_address(parsed.raw_host.decode('ascii'), parsed.port or 443)
    return str(parsed).rstrip('/')


def _target(request: httpx.Request, base: httpx.URL) -> tuple[str, int]:
    if (request.url.scheme, request.url.raw_host, request.url.port) != (base.scheme, base.raw_host, base.port):
        raise httpx.ConnectError('模型服务请求超出了配置地址。', request=request)
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


class AsyncPublicModelTransport(httpx.AsyncHTTPTransport):
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
        httpx.AsyncClient(transport=AsyncPublicModelTransport(base), trust_env=False),
    )
