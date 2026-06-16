from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass


SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


@dataclass
class RconClient:
    host: str
    port: int
    password: str
    timeout: float = 10.0

    async def execute(self, command: str) -> str:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        request_id = 1

        try:
            await self._send_packet(writer, request_id, SERVERDATA_AUTH, self.password)
            auth_type, auth_id, auth_body = await self._read_packet(reader)

            if auth_type != SERVERDATA_AUTH_RESPONSE or auth_id == -1:
                raise RuntimeError("RCON authentication failed")

            exec_id = 2
            await self._send_packet(writer, exec_id, SERVERDATA_EXECCOMMAND, command)
            await self._send_packet(writer, exec_id + 1, SERVERDATA_EXECCOMMAND, "")

            chunks: list[str] = []
            while True:
                packet_type, packet_id, body = await self._read_packet(reader)
                if packet_type != SERVERDATA_RESPONSE_VALUE:
                    continue
                if packet_id == exec_id + 1 and not body:
                    break
                if packet_id == exec_id:
                    chunks.append(body)
                    if not body.endswith("\n"):
                        break

            return "".join(chunks).strip()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _send_packet(
        self,
        writer: asyncio.StreamWriter,
        request_id: int,
        packet_type: int,
        body: str,
    ) -> None:
        payload = struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
        writer.write(struct.pack("<i", len(payload)) + payload)
        await writer.drain()

    async def _read_packet(self, reader: asyncio.StreamReader) -> tuple[int, int, str]:
        size_data = await reader.readexactly(4)
        size = struct.unpack("<i", size_data)[0]
        packet_data = await reader.readexactly(size)
        request_id, packet_type = struct.unpack("<ii", packet_data[:8])
        body = packet_data[8:-2].decode("utf-8", errors="replace")
        return packet_type, request_id, body
