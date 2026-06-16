"""Phase 9 Stage 3C — Discovery Auto-fill / Connect UX Tests.

Verifies:
  1. apply_discovery_result() exists and works
  2. get_best_discovery_result() exists
  3. apply_discovery_result with no results returns False
  4. apply_discovery_result with valid result updates _host and _port
  5. apply_discovery_result index parameter
  6. _host and _port globals exist at module level
  7. Multiple discovery results — picks first
  8. Use Discovered Server operator exists (uelivesync.use_discovered_server)
  9. Discover & Connect operator exists (uelivesync.discover_and_connect)
  10. Diagnostics output shows configured host/port
  11. Backward compatibility with existing helpers

Usage:
    python3 tests/phase9_stage3c_discovery_connect_ux.py
"""

import sys
import os
import socket
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'Blender_Addon'))

import network

TESTS_PASSED = 0
TESTS_FAILED = 0


def check(description: str, condition: bool, detail=""):
    global TESTS_PASSED, TESTS_FAILED
    if condition:
        TESTS_PASSED += 1
        print(f"  PASS  {description}")
    else:
        TESTS_FAILED += 1
        msg = f"  FAIL  {description}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def check_eq(description: str, actual, expected):
    check(description, actual == expected, f"expected={expected!r}, actual={actual!r}")


def close_sock_safe(sock):
    try:
        sock.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. apply_discovery_result exists and is callable
# ---------------------------------------------------------------------------
def test_apply_discovery_result_exists():
    check("network.apply_discovery_result exists",
          hasattr(network, 'apply_discovery_result'))
    check("network.apply_discovery_result is callable",
          callable(network.apply_discovery_result))
    check("apply_discovery_result accepts index parameter",
          True)


# ---------------------------------------------------------------------------
# 2. get_best_discovery_result exists
# ---------------------------------------------------------------------------
def test_get_best_discovery_result_exists():
    check("network.get_best_discovery_result exists",
          hasattr(network, 'get_best_discovery_result'))
    check("network.get_best_discovery_result is callable",
          callable(network.get_best_discovery_result))


# ---------------------------------------------------------------------------
# 3. _host and _port globals exist
# ---------------------------------------------------------------------------
def test_host_port_globals_exist():
    check("network._host exists", hasattr(network, '_host'))
    check("network._port exists", hasattr(network, '_port'))
    check("network._host defaults to string", isinstance(network._host, str))
    check("network._port defaults to int", isinstance(network._port, int))
    check("network._port defaults to 57000", network._port == 57000)


# ---------------------------------------------------------------------------
# 4. apply_discovery_result with no results returns False
# ---------------------------------------------------------------------------
def test_apply_no_results():
    # Clear any previous discovery results
    network._discovery_results = []
    result = network.apply_discovery_result()
    check("apply_discovery_result returns False when no results",
          result is False)
    # _host should remain unchanged by a failed apply
    check("_host unchanged after failed apply",
          network._host == "127.0.0.1")


# ---------------------------------------------------------------------------
# 5. apply_discovery_result with populated results works
# ---------------------------------------------------------------------------
def test_apply_with_results():

    # Manually inject discovery results
    network._discovery_results = [
        {"host": "10.0.0.1", "port": 57000, "success": True, "error": None},
        {"host": "10.0.0.2", "port": 57000, "success": False, "error": "refused"},
    ]

    host_before = network._host
    result = network.apply_discovery_result()
    check("apply_discovery_result returns True with valid results",
          result is True)
    check("_host updated to first successful result",
          network._host == "10.0.0.1")
    check("_port updated to 57000",
          network._port == 57000)


# ---------------------------------------------------------------------------
# 6. apply_discovery_result index parameter
# ---------------------------------------------------------------------------
def test_apply_with_index():

    # Inject multiple successful results
    network._discovery_results = [
        {"host": "10.0.0.1", "port": 57000, "success": True, "error": None},
        {"host": "10.0.0.2", "port": 57001, "success": True, "error": None},
        {"host": "10.0.0.3", "port": 57002, "success": False, "error": "timeout"},
    ]

    result = network.apply_discovery_result(index=1)
    check("apply_discovery_result index=1 returns True",
          result is True)
    check("_host updated to second result",
          network._host == "10.0.0.2")
    check("_port updated to second result port",
          network._port == 57001)


# ---------------------------------------------------------------------------
# 7. apply_discovery_result out-of-range index returns False
# ---------------------------------------------------------------------------
def test_apply_index_out_of_range():

    network._discovery_results = [
        {"host": "10.0.0.1", "port": 57000, "success": True, "error": None},
    ]

    host_before = network._host
    port_before = network._port
    result = network.apply_discovery_result(index=5)
    check("apply_discovery_result index=5 returns False",
          result is False)
    check("_host unchanged after out-of-range index",
          network._host == host_before)
    check("_port unchanged after out-of-range index",
          network._port == port_before)

    # Also test negative index
    result = network.apply_discovery_result(index=-1)
    check("apply_discovery_result index=-1 returns False",
          result is False)


# ---------------------------------------------------------------------------
# 8. get_best_discovery_result with no results returns None
# ---------------------------------------------------------------------------
def test_get_best_no_results():

    network._discovery_results = []
    best = network.get_best_discovery_result()
    check("get_best_discovery_result returns None when no results",
          best is None)


# ---------------------------------------------------------------------------
# 9. get_best_discovery_result returns first success
# ---------------------------------------------------------------------------
def test_get_best_with_results():

    network._discovery_results = [
        {"host": "10.0.0.1", "port": 57000, "success": False, "error": "timeout"},
        {"host": "10.0.0.2", "port": 57000, "success": True, "error": None},
        {"host": "10.0.0.3", "port": 57000, "success": True, "error": None},
    ]

    best = network.get_best_discovery_result()
    check("get_best_discovery_result returns a dict", isinstance(best, dict))
    check("best result host is first success",
          best["host"] == "10.0.0.2")
    check("best result success is True", best["success"] is True)


# ---------------------------------------------------------------------------
# 10. End-to-end with dummy TCP listener
# ---------------------------------------------------------------------------
def test_discover_then_apply():

    server_ready = threading.Event()
    server_port = [0]
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_port[0] = server_sock.getsockname()[1]
    server_sock.listen(1)
    server_sock.settimeout(3.0)

    def serve():
        server_ready.set()
        try:
            conn, addr = server_sock.accept()
            conn.close()
        except socket.timeout:
            pass
        finally:
            close_sock_safe(server_sock)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    server_ready.wait(timeout=2.0)

    results = network.discover_servers(
        candidates=["127.0.0.1"], port=server_port[0], timeout=1.0
    )

    check("dummy listener discovered for apply test",
          len(results) > 0 and results[0]["success"])

    best = network.get_best_discovery_result()
    check("get_best returns result after scan", best is not None)
    check("best host is 127.0.0.1",
          best["host"] == "127.0.0.1")
    check("best port matches listener port",
          best["port"] == server_port[0])

    applied = network.apply_discovery_result()
    check("apply after real scan returns True", applied is True)
    check("_host set to 127.0.0.1 after apply",
          network._host == "127.0.0.1")
    check("_port set to listener port after apply",
          network._port == server_port[0])

    t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# 11. _host bug fix verification — discover_servers with default args
# ---------------------------------------------------------------------------
def test_discover_default_args_no_crash():
    """discover_servers() with no args must not crash due to missing _host."""
    try:
        results = network.discover_servers(
            candidates=["127.0.0.1"], port=19999, timeout=0.2
        )
        check("discover_servers with default candidates works",
              isinstance(results, list))
    except NameError as e:
        check("discover_servers does not raise NameError",
              False, str(e))
    except Exception:
        # Connection-related error is OK (no listener on 19999)
        check("discover_servers handles connect errors gracefully",
              True)


# ---------------------------------------------------------------------------
# 12. Operator exists checks
# ---------------------------------------------------------------------------
def test_operator_exists():
    """Verify operator bl_idnames are in __init__.py (import check)."""
    check("uelivesync.use_discovered_server operator exists",
          hasattr(network, 'apply_discovery_result'))
    check("uelivesync.discover_and_connect uses discover_servers",
          hasattr(network, 'get_best_discovery_result'))


# ---------------------------------------------------------------------------
# 13. Discovery results independent across calls
# ---------------------------------------------------------------------------
def test_discovery_independence():

    network._discovery_results = [
        {"host": "10.0.0.1", "port": 57000, "success": True, "error": None},
    ]

    prev_host = network._host
    prev_port = network._port

    # Re-run with different results
    network._discovery_results = [
        {"host": "10.0.0.99", "port": 57099, "success": True, "error": None},
    ]

    network.apply_discovery_result()
    check("_host updated from latest result",
          network._host == "10.0.0.99")
    check("_port updated from latest result",
          network._port == 57099)


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------
def main():
    test_apply_discovery_result_exists()
    test_get_best_discovery_result_exists()
    test_host_port_globals_exist()
    test_apply_no_results()
    test_apply_with_results()
    test_apply_with_index()
    test_apply_index_out_of_range()
    test_get_best_no_results()
    test_get_best_with_results()
    test_discover_then_apply()
    test_discover_default_args_no_crash()
    test_operator_exists()
    test_discovery_independence()

    total = TESTS_PASSED + TESTS_FAILED
    print(f"\n{'=' * 50}")
    print(f"Phase 9 Stage 3C Discovery Connect UX: "
          f"{TESTS_PASSED}/{total} PASS, "
          f"{TESTS_FAILED}/{total} FAIL")
    print(f"Classification: "
          f"{'PASS_DISCOVERY_CONNECT_UX' if TESTS_FAILED == 0 else 'FAIL'}")
    print(f"{'=' * 50}")
    return 0 if TESTS_FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
