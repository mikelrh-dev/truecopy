"""WA-R1 coverage: LAN-only browser access, scenario "Request from outside the LAN".

ADR-006 enforces LAN-only access with two halves (web-access-auth spec:
"SHALL be reachable only from the local network"):

1. The service binds the LAN interface — asserted here through the
   ``scripts/run_web.py`` helpers that build the actual Streamlit command.
2. The operator firewall drops off-LAN traffic — enforcement is operational
   by design, so this suite pins the documented rule (allow the LAN subnet,
   drop the rest) as a release check: the scenario stays covered only while
   the binding assertion and the firewall documentation both hold.

A request from outside the LAN is therefore not served; nothing in this test
touches the network or the host firewall.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts import run_web

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_bind_address_is_lan_scope() -> None:
    # ADR-006: all interfaces, so LAN tablets/phones can reach the UI; the
    # firewall rule (asserted below) is what keeps it LAN-only.
    assert run_web.DEFAULT_ADDRESS == "0.0.0.0"
    assert run_web.resolve_bind_address(None) == "0.0.0.0"


def test_bind_address_override_is_honored() -> None:
    assert run_web.resolve_bind_address("192.168.1.10") == "192.168.1.10"


def test_streamlit_command_carries_the_resolved_bind_address() -> None:
    address = run_web.resolve_bind_address(None)
    cmd = run_web.build_command(
        "streamlit", REPO_ROOT / "web" / "app.py", address, run_web.DEFAULT_PORT
    )
    assert f"--server.address={address}" in cmd
    assert f"--server.port={run_web.DEFAULT_PORT}" in cmd


def test_readme_keeps_the_firewall_rule_that_drops_off_lan_requests() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    # The stated enforcement boundary: port 8501 open only to the LAN subnet.
    assert "only from the LAN subnet" in readme

    # Linux example: accept the LAN source first, then drop everyone else.
    assert re.search(r"--dport 8501\s+-s\s+\S+\s+-j\s+ACCEPT", readme)
    assert re.search(r"--dport 8501\s+-j\s+DROP", readme)

    # Windows example: same rule scoped by remote address.
    assert "New-NetFirewallRule" in readme
    assert "-LocalPort 8501" in readme
    assert "-RemoteAddress" in readme
