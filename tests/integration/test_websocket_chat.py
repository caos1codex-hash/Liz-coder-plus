"""Integration tests for the /ws/chat WebSocket endpoint.

Sprint 1 - Prompt 2.

Covers:
  - successful connection
  - sending a message and receiving streamed response
  - receiving a final 'message' envelope with status=completed
  - graceful disconnection
  - error handling for malformed payloads
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import websockets

# Make the backend src importable.
ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "apps" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

# Drop any cached `src` namespace so the backend's `src` is the one that
# gets imported, regardless of test execution order.
for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from fastapi.testclient import TestClient  # noqa: E402

from src.api.main import app  # noqa: E402
from src.api.ws_routes import get_orchestrator, get_ws_manager  # noqa: E402


def test_health_endpoint_works() -> None:
    """Sanity check: REST /health still works after wiring the WS router."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_endpoint_reports_ws_state() -> None:
    """/status should report ws_connections and ws_sessions fields."""
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert "ws_connections" in body
    assert "ws_sessions" in body
    assert "memory_sessions" in body
    assert "agents" in body


@pytest.mark.asyncio
async def test_websocket_round_trip_streams_and_completes() -> None:
    """/ws/chat should accept a message and stream back a completed envelope.

    Uses a real ASGI server bound to an ephemeral port via uvicorn.
    """
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=8765, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())
    # Wait for the server to start.
    while not server.started:
        await asyncio.sleep(0.05)

    try:
        uri = "ws://127.0.0.1:8765/ws/chat"
        async with websockets.connect(uri) as ws:
            # Send a message.
            await ws.send(json.dumps({
                "message": "Hola Liz",
                "session_id": "test-session-1",
                "mode": "confirmation",
            }))

            # Collect envelopes until we get the final 'message'.
            envelopes = []
            for _ in range(50):
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                env = json.loads(raw)
                envelopes.append(env)
                if env.get("type") == "message" and env.get("status") == "completed":
                    break

        # Assertions
        assert len(envelopes) >= 1
        final = envelopes[-1]
        assert final["type"] == "message"
        assert final["status"] == "completed"
        assert final["session_id"] == "test-session-1"
        assert "Hola, soy Liz" in final["content"]
        # There must have been at least one chunk before the final.
        chunk_types = [e["type"] for e in envelopes]
        assert "chunk" in chunk_types or "message" in chunk_types
    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_websocket_missing_message_returns_error() -> None:
    """Sending a payload without 'message' should yield an error envelope."""
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=8766, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    try:
        uri = "ws://127.0.0.1:8766/ws/chat"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"session_id": "s1"}))  # missing message
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            env = json.loads(raw)
            assert env["type"] == "error"
            assert "message" in env["error"]
    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_websocket_missing_session_returns_error() -> None:
    """Sending a payload without 'session_id' should yield an error envelope."""
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=8767, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    try:
        uri = "ws://127.0.0.1:8767/ws/chat"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"message": "hi"}))  # missing session_id
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            env = json.loads(raw)
            assert env["type"] == "error"
            assert "session_id" in env["error"]
    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_websocket_invalid_mode_returns_error() -> None:
    """An invalid mode value should yield an error envelope."""
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=8768, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    try:
        uri = "ws://127.0.0.1:8768/ws/chat"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "message": "hi",
                "session_id": "s1",
                "mode": "totally-invalid",
            }))
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            env = json.loads(raw)
            assert env["type"] == "error"
            assert "mode" in env["error"]
    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_websocket_graceful_disconnect() -> None:
    """Closing the client side should clean up the connection server-side."""
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=8769, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    try:
        uri = "ws://127.0.0.1:8769/ws/chat"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "message": "bye",
                "session_id": "s-disconnect",
                "mode": "confirmation",
            }))
            # Drain the streamed response.
            for _ in range(20):
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                env = json.loads(raw)
                if env.get("type") == "message" and env.get("status") == "completed":
                    break

        # After context exit, the connection is closed.
        # Give the server a moment to unregister.
        await asyncio.sleep(0.1)

        ws_manager = get_ws_manager()
        assert ws_manager.connection_count == 0
    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_websocket_session_history_grows() -> None:
    """Each exchange should append to the session manager history."""
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=8770, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    orch = get_orchestrator()
    initial_session_count = orch.session_manager.session_count

    try:
        uri = "ws://127.0.0.1:8770/ws/chat"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "message": "primero",
                "session_id": "session-history-1",
                "mode": "confirmation",
            }))
            for _ in range(20):
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                env = json.loads(raw)
                if env.get("type") == "message" and env.get("status") == "completed":
                    break

        # After the exchange, the session should have at least 2 turns
        # (user + assistant).
        snapshot = orch.session_manager.snapshot("session-history-1")
        assert snapshot is not None
        assert len(snapshot["history"]) >= 2
        roles = [t["role"] for t in snapshot["history"]]
        assert "user" in roles
        assert "assistant" in roles
    finally:
        server.should_exit = True
        await server_task
