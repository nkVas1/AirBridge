"""mDNS/Bonjour service discovery for zero-configuration networking."""

from __future__ import annotations

import logging
import socket
from typing import Any

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from airbridge import __version__

logger = logging.getLogger(__name__)


def get_local_ip() -> str:
    """Detect the local IPv4 address of this machine.

    Uses a UDP socket trick to determine the primary network interface
    address without actually sending any data.

    Returns:
        The local IPv4 address as a string.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not actually connect — just determines the route
        sock.connect(("10.255.255.255", 1))
        ip: str = sock.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        sock.close()
    return ip


class ServiceDiscovery:
    """Manages mDNS/Bonjour service registration and teardown.

    Registration goes through zeroconf's asyncio interface. The
    synchronous one drives its own event loop from a worker thread and
    raises `EventLoopBlocked` when called from inside a running loop,
    which is exactly where an aiohttp startup hook lives.
    """

    def __init__(
        self,
        service_name: str,
        service_type: str,
        port: int,
        scheme: str = "https",
    ) -> None:
        self._service_name = service_name
        self._service_type = service_type
        self._port = port
        self._scheme = scheme
        self._zeroconf: AsyncZeroconf | None = None
        self._info: ServiceInfo | None = None

    @property
    def local_ip(self) -> str:
        """Return the detected local IP address."""
        return get_local_ip()

    async def register(self) -> str:
        """Register the service via mDNS/Bonjour.

        Returns:
            The local IP address where the service is available.
        """
        ip = self.local_ip

        self._info = ServiceInfo(
            type_=self._service_type,
            name=f"{self._service_name}.{self._service_type}",
            addresses=[socket.inet_aton(ip)],
            port=self._port,
            properties=self._build_properties(ip),
            server=f"{self._service_name.lower()}.local.",
        )

        self._zeroconf = AsyncZeroconf()
        await self._zeroconf.async_register_service(self._info)
        logger.info(
            "mDNS service registered: %s at %s:%d",
            self._service_name,
            ip,
            self._port,
        )
        return ip

    async def unregister(self) -> None:
        """Unregister the service and shut down mDNS."""
        if self._zeroconf is not None:
            if self._info is not None:
                await self._zeroconf.async_unregister_service(self._info)
            await self._zeroconf.async_close()
            logger.info("mDNS service unregistered")
        self._zeroconf = None
        self._info = None

    def _build_properties(self, ip: str) -> dict[str, Any]:
        """Build TXT record properties for the service."""
        return {
            "version": __version__,
            "platform": "desktop",
            "scheme": self._scheme,
            "ip": ip,
            "port": str(self._port),
        }
