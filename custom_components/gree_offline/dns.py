"""Gree's real addresses, via external DNS since the local one redirects Gree names to us."""

from __future__ import annotations

from collections.abc import Sequence

import aiodns
from pycares import ARecordData


class ExternalResolver:
    def __init__(self, nameservers: Sequence[str]) -> None:
        self.nameservers = list(nameservers)

    async def resolve(self, name: str) -> str:
        resolver = aiodns.DNSResolver(nameservers=self.nameservers, timeout=3, tries=1)
        try:
            reply = await resolver.query_dns(name, "A")
        except aiodns.error.DNSError as e:
            raise OSError(f"cannot resolve {name} via {', '.join(self.nameservers)}") from e
        finally:
            await resolver.close()
        addresses = [r.data.addr for r in reply.answer if isinstance(r.data, ARecordData)]
        if not addresses:
            raise OSError(f"no A record for {name}")
        return addresses[0]
