#!/usr/bin/env python3
"""Two-hop WebSocket proxy test mimicking HA ingress architecture.

Simulates: Client -> Proxy1 (HA Core) -> Proxy2 (Supervisor) -> Addon WebSocket

This matches the exact architecture of Home Assistant's ingress proxy:
- Each hop terminates WebSocket and creates a new connection
- Each hop runs _websocket_forward in both directions
- asyncio.wait with FIRST_COMPLETED tears down on any close
"""
import aiohttp
from aiohttp import web, WSMsgType
import asyncio
import json
import sys

ADDON_WS_URL = 'http://172.30.33.19:7682/ws'
PROXY2_PORT = 18901  # Simulates Supervisor
PROXY1_PORT = 18902  # Simulates HA Core


async def _websocket_forward(ws_from, ws_to, label=""):
    """Exact copy of HA's _websocket_forward function."""
    try:
        async for msg in ws_from:
            if msg.type is WSMsgType.TEXT:
                await ws_to.send_str(msg.data)
            elif msg.type is WSMsgType.BINARY:
                await ws_to.send_bytes(msg.data)
            elif msg.type is WSMsgType.PING:
                await ws_to.ping(msg.data)
            elif msg.type is WSMsgType.PONG:
                await ws_to.pong(msg.data)
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break
    except (RuntimeError, ConnectionResetError) as e:
        print(f"  [{label}] forward error: {e}")


async def proxy_handler(request, backend_url, name):
    """WebSocket proxy handler - accepts client WS, connects to backend, relays."""
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)
    print(f"  [{name}] client connected, connecting to backend {backend_url}")

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            backend_url,
            autoclose=False,
            autoping=False,
        ) as ws_client:
            print(f"  [{name}] backend connected, starting relay")
            await asyncio.wait(
                [
                    asyncio.create_task(
                        _websocket_forward(ws_server, ws_client, f"{name}:client->backend")
                    ),
                    asyncio.create_task(
                        _websocket_forward(ws_client, ws_server, f"{name}:backend->client")
                    ),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            print(f"  [{name}] relay ended")

    return ws_server


async def run_test():
    results = {"pass": 0, "fail": 0, "errors": []}

    def check(condition, msg):
        if condition:
            print(f"  PASS: {msg}")
            results["pass"] += 1
        else:
            print(f"  FAIL: {msg}")
            results["fail"] += 1
            results["errors"].append(msg)

    # Start Proxy2 (Supervisor simulator) - connects to addon
    proxy2_app = web.Application()
    proxy2_app.router.add_route(
        "GET", "/ws",
        lambda r: proxy_handler(r, ADDON_WS_URL, "Proxy2-Supervisor"),
    )
    proxy2_runner = web.AppRunner(proxy2_app)
    await proxy2_runner.setup()
    proxy2_site = web.TCPSite(proxy2_runner, "0.0.0.0", PROXY2_PORT)
    await proxy2_site.start()
    print(f"Proxy2 (Supervisor sim) on port {PROXY2_PORT}")

    # Start Proxy1 (HA Core simulator) - connects to Proxy2
    proxy1_app = web.Application()
    proxy1_app.router.add_route(
        "GET", "/ws",
        lambda r: proxy_handler(r, f"http://127.0.0.1:{PROXY2_PORT}/ws", "Proxy1-Core"),
    )
    proxy1_runner = web.AppRunner(proxy1_app)
    await proxy1_runner.setup()
    proxy1_site = web.TCPSite(proxy1_runner, "0.0.0.0", PROXY1_PORT)
    await proxy1_site.start()
    print(f"Proxy1 (HA Core sim) on port {PROXY1_PORT}")

    # === Test 1: Direct connection (baseline) ===
    print("\n=== Test 1: Direct connection ===")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ADDON_WS_URL, autoclose=False, autoping=False) as ws:
                await ws.send_str(json.dumps({"type": "list"}))
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                data = json.loads(msg.data)
                check(data["type"] == "sessions", f"Direct: got sessions response with {len(data.get('tabs',[]))} tabs")
    except Exception as e:
        check(False, f"Direct connection failed: {e}")

    # === Test 2: Single-hop proxy (Supervisor only) ===
    print("\n=== Test 2: Single-hop proxy (Supervisor sim) ===")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"http://127.0.0.1:{PROXY2_PORT}/ws", autoclose=False, autoping=False
            ) as ws:
                check(True, "Single-hop: connected through proxy")
                await ws.send_str(json.dumps({"type": "list"}))
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                data = json.loads(msg.data)
                check(data["type"] == "sessions", f"Single-hop: got sessions response with {len(data.get('tabs',[]))} tabs")
    except Exception as e:
        check(False, f"Single-hop proxy failed: {e}")

    # === Test 3: Two-hop proxy (full HA ingress sim) ===
    print("\n=== Test 3: Two-hop proxy (full HA ingress sim) ===")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"http://127.0.0.1:{PROXY1_PORT}/ws", autoclose=False, autoping=False
            ) as ws:
                check(True, "Two-hop: connected through double proxy")
                await ws.send_str(json.dumps({"type": "list"}))
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                data = json.loads(msg.data)
                check(data["type"] == "sessions", f"Two-hop: got sessions response with {len(data.get('tabs',[]))} tabs")

                # Test creating a shell tab
                await ws.send_str(json.dumps({
                    "type": "create",
                    "tabId": "test1",
                    "command": "/bin/bash",
                    "args": [],
                    "label": "Test Shell",
                }))
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                data = json.loads(msg.data)
                check(data["type"] == "created", f"Two-hop: tab created ({data.get('label','')})")

                # Wait for some PTY output
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                data = json.loads(msg.data)
                check(data["type"] == "output", f"Two-hop: received PTY output ({len(data.get('data',''))} bytes)")

                # Send input to the shell
                await ws.send_str(json.dumps({
                    "type": "input",
                    "tabId": "test1",
                    "data": "echo PROXY_TEST_OK\r",
                }))
                # Read output until we find our echo
                found = False
                for _ in range(20):
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if "PROXY_TEST_OK" in data.get("data", ""):
                            found = True
                            break
                check(found, "Two-hop: received echo response through full proxy chain")

                # Close the test tab
                await ws.send_str(json.dumps({"type": "close", "tabId": "test1"}))
                await asyncio.sleep(0.5)

    except Exception as e:
        check(False, f"Two-hop proxy failed: {e}")

    # Cleanup
    await proxy1_runner.cleanup()
    await proxy2_runner.cleanup()

    # Summary
    print(f"\n=== SUMMARY ===")
    print(f"Passed: {results['pass']}, Failed: {results['fail']}")
    if results["errors"]:
        print(f"Errors: {'; '.join(results['errors'])}")

    return results["fail"] == 0


if __name__ == "__main__":
    ok = asyncio.run(run_test())
    sys.exit(0 if ok else 1)
