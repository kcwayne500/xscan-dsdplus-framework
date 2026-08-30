import asyncio
import os
import time

import pytest

from xscan.events import EventBus
from xscan.state import Component


def test_pid_and_heartbeat_health_expires_truthfully():
    component = Component("ffmpeg", state="running", pid=os.getpid(), heartbeat=time.monotonic())
    assert component.payload()["healthy"] is True
    component.heartbeat = time.monotonic() - 6
    assert component.payload()["healthy"] is False
    component.pid = 999_999_999
    component.heartbeat = time.monotonic()
    assert component.payload()["healthy"] is False


@pytest.mark.asyncio
async def test_event_bus_supports_disconnect_and_reconnect():
    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    first = bus.subscribe()
    bus.publish("component", {"state": "running"})
    assert (await asyncio.wait_for(first.get(), 1)).data["state"] == "running"
    bus.unsubscribe(first)
    second = bus.subscribe()
    bus.publish("call-completed", {"id": "one"})
    assert (await asyncio.wait_for(second.get(), 1)).data["id"] == "one"
    bus.unsubscribe(second)
