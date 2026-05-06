from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any


NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


class BleakUnavailable(RuntimeError):
    pass


def is_bluetooth_unavailable(exc: BaseException) -> bool:
    return exc.__class__.__name__ in {
        "BleakBluetoothNotAvailableError",
        "BleakDBusError",
    }


def _load_bleak() -> tuple[Any, Any]:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise BleakUnavailable("bleak is not installed. Run: pip install -r requirements.txt") from exc
    return BleakClient, BleakScanner


@dataclass
class BLEDeviceInfo:
    name: str | None
    address: str
    rssi: int | None = None


class MoonsideBLE:
    def __init__(
        self,
        *,
        ble_address: str | None = None,
        name_contains: str | None = "Moonside",
        command_delay_seconds: float = 0.25,
        command_suffix: str = "",
        write_response: bool = True,
        skip_redundant_commands: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self.ble_address = ble_address
        self.name_contains = name_contains
        self.command_delay_seconds = command_delay_seconds
        self.command_suffix = command_suffix
        self.write_response = write_response
        self.skip_redundant_commands = skip_redundant_commands
        self.logger = logger or logging.getLogger(__name__)
        self.client: Any | None = None
        self.device: Any | None = None
        self.rx_characteristic: Any | None = None
        self._last_power_command: str | None = None
        self._last_brightness_command: str | None = None

    async def scan(self, timeout: float = 5.0) -> list[BLEDeviceInfo]:
        _, BleakScanner = _load_bleak()
        devices = await BleakScanner.discover(timeout=timeout)
        results: list[BLEDeviceInfo] = []
        for device in devices:
            rssi = getattr(device, "rssi", None)
            if rssi is None:
                details = getattr(device, "details", None)
                rssi = getattr(details, "rssi", None)
            results.append(BLEDeviceInfo(name=getattr(device, "name", None), address=device.address, rssi=rssi))
        return results

    async def find_device(self, timeout: float = 5.0) -> Any:
        _, BleakScanner = _load_bleak()
        devices = await BleakScanner.discover(timeout=timeout)
        if self.ble_address:
            for device in devices:
                if device.address.lower() == self.ble_address.lower():
                    return device
            raise RuntimeError(f"BLE device not found by address: {self.ble_address}")

        needle = (self.name_contains or "").lower()
        for device in devices:
            name = (getattr(device, "name", None) or "").lower()
            if needle and needle in name:
                return device

        names = ", ".join(f"{device.name or '<unnamed>'} ({device.address})" for device in devices) or "none"
        raise RuntimeError(f"No BLE device matched name containing {self.name_contains!r}. Devices: {names}")

    async def connect(self, timeout: float = 5.0) -> None:
        BleakClient, _ = _load_bleak()
        self.device = await self.find_device(timeout=timeout)
        self.logger.info("Connecting to BLE device name=%s address=%s", self.device.name, self.device.address)
        self.client = BleakClient(self.device)
        await self.client.connect()
        await self._resolve_rx_characteristic()
        self.logger.info("Connected; using RX characteristic %s", self.rx_characteristic)

    async def disconnect(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            finally:
                self.client = None
                self.rx_characteristic = None

    def is_connected(self) -> bool:
        return bool(self.client and getattr(self.client, "is_connected", False))

    async def _resolve_rx_characteristic(self) -> None:
        if not self.client:
            raise RuntimeError("BLE client is not connected")

        services = getattr(self.client, "services", None)
        if services is None and hasattr(self.client, "get_services"):
            services = await self.client.get_services()

        writable_candidates: list[Any] = []
        for service in services:
            for characteristic in service.characteristics:
                uuid = str(characteristic.uuid).lower()
                props = set(getattr(characteristic, "properties", []))
                if uuid == NUS_RX_UUID:
                    self.rx_characteristic = characteristic
                    return
                if "write" in props or "write-without-response" in props:
                    writable_candidates.append(characteristic)

        if writable_candidates:
            self.rx_characteristic = writable_candidates[0]
            self.logger.warning(
                "Nordic UART RX characteristic not found; using first writable characteristic %s. Candidates: %s",
                self.rx_characteristic,
                ", ".join(str(candidate.uuid) for candidate in writable_candidates),
            )
            return

        raise RuntimeError("No writable BLE characteristic found")

    def _redundant_command_key(self, command: str) -> str | None:
        normalized = command.strip().upper()
        if normalized in {"LEDON", "LEDOFF"}:
            return "power"
        if normalized.startswith("BRIGH"):
            return "brightness"
        return None

    def _is_redundant_command(self, command: str) -> bool:
        key = self._redundant_command_key(command)
        normalized = command.strip().upper()
        if key == "power":
            return self._last_power_command == normalized
        if key == "brightness":
            return self._last_brightness_command == normalized
        return False

    def _remember_command(self, command: str) -> None:
        key = self._redundant_command_key(command)
        normalized = command.strip().upper()
        if key == "power":
            self._last_power_command = normalized
            if normalized == "LEDOFF":
                self._last_brightness_command = None
        elif key == "brightness":
            self._last_brightness_command = normalized

    async def send_command(self, command: str, *, optimize: bool = True) -> None:
        if not self.client or not self.rx_characteristic or not self.is_connected():
            raise RuntimeError("BLE client is not connected")
        if optimize and self.skip_redundant_commands and self._is_redundant_command(command):
            self.logger.info("Skipping redundant command: %s", command.strip())
            return
        payload = f"{command.strip()}{self.command_suffix}".encode("utf-8")
        self.logger.info("Sending command: %s", command.strip())
        await self.client.write_gatt_char(self.rx_characteristic, payload, response=self.write_response)
        self._remember_command(command)
        if self.command_delay_seconds > 0:
            await asyncio.sleep(self.command_delay_seconds)

    async def send_commands(self, commands: list[str], *, optimize: bool = True) -> None:
        for command in commands:
            await self.send_command(command, optimize=optimize)
