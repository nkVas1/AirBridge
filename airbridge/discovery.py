"""mDNS/Bonjour service discovery for zero-configuration networking."""

from __future__ import annotations

import logging
import socket
from typing import Any

from zeroconf import ServiceInfo, Zeroconf

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
    """Manages mDNS/Bonjour service registration and teardown."""

    def __init__(self, service_name: str, service_type: str, port: int) -> None:
        self._service_name = service_name
        self._service_type = service_type
        self._port = port
        self._zeroconf: Zeroconf | None = None
        self._info: ServiceInfo | None = None

    @property
    def local_ip(self) -> str:
        """Return the detected local IP address."""
        return get_local_ip()

    def register(self) -> str:
        """Register the service via mDNS/Bonjour.

        Returns:
            The local IP address where the service is available.
        """
        ip = self.local_ip
        parsed = socket.inet_aton(ip)

        self._info = ServiceInfo(
            type_=self._service_type,
            name=f"{self._service_name}.{self._service_type}",
            addresses=[parsed],
            port=self._port,
            properties=self._build_properties(ip),
            server=f"{self._service_name.lower()}.local.",
        )

        self._zeroconf = Zeroconf()
        self._zeroconf.register_service(self._info)
        logger.info(
            "mDNS service registered: %s at %s:%d",
            self._service_name,
            ip,
            self._port,
        )
        return ip

    def unregister(self) -> None:
        """Unregister the service and shut down mDNS."""
        if self._zeroconf and self._info:
            self._zeroconf.unregister_service(self._info)
            self._zeroconf.close()
            logger.info("mDNS service unregistered")
        self._zeroconf = None
        self._info = None

    def _build_properties(self, ip: str) -> dict[str, Any]:
        """Build TXT record properties for the service."""
        return {
            "version": "1.0.0",
            "platform": "desktop",
            "ip": ip,
            "port": str(self._port),
        }
