"""Guard the known socket/subprocess entry points used by this offline harness.

This monkeypatch fixture is not an operating-system network/process sandbox.
"""

import asyncio
import socket
import subprocess

import pytest


@pytest.fixture(autouse=True)
def isolated_offline_acceptance(tmp_path, monkeypatch):
    attempts = []

    def denied(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Acceptance fixtures must not start network or subprocess operations")

    async def denied_async(*args, **kwargs):
        denied()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", denied_async)
    yield
    # Registry may turn exceptions into ToolResult errors: still fail the test
    # if an unexpected external call was attempted and swallowed downstream.
    assert not attempts, "An acceptance scenario attempted external I/O"
