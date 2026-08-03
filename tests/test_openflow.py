# Copyright (C) 2025, RTE (http://www.rte-france.com)
# SPDX-License-Identifier: Apache-2.0

import pytest

from setup_ovs import helpers
from setup_ovs.openflow import SetupOpenFlow


SECURED_PORT = {
    "name": "tap0",
    "type": "tap",
    "mac": "de:ad:be:ef:00:01",
    "ip": "10.0.0.1",
}


def flows(run_command):
    """Only the add-flow rule arguments, one string per rule."""
    return [args[-1] for args, _ in run_command.calls if args[1] == "add-flow"]


class TestConstructor:
    def test_does_nothing_without_bridges(self, run_command):
        SetupOpenFlow({})

        assert run_command.calls == []

    def test_walks_every_bridge(self, run_command):
        SetupOpenFlow(
            {"bridges": [{"name": "br0", "ports": []}, {"name": "br1", "ports": []}]}
        )

        assert run_command.commands_containing("del-flows br0")
        assert run_command.commands_containing("del-flows br1")


class TestBridgeFlows:
    def test_bridge_without_ports_key_gets_no_flow(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0"}]})

        assert run_command.calls == []

    def test_default_filters_are_cleared_first(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0", "ports": []}]})

        assert run_command.commands[0] == "ovs-ofctl del-flows br0"

    def test_ipv6_is_dropped_by_default(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0", "ports": []}]})

        ipv6_rules = [rule for rule in flows(run_command) if "ipv6" in rule]
        assert len(ipv6_rules) == 2
        assert all("action=drop" in rule for rule in ipv6_rules)

    def test_ipv6_can_be_forced_to_normal(self, run_command, caplog):
        SetupOpenFlow(
            {"bridges": [{"name": "br0", "ports": [], "enable_ipv6": True}]}
        )

        ipv6_rules = [rule for rule in flows(run_command) if "ipv6" in rule]
        assert all("action=normal" in rule for rule in ipv6_rules)
        assert "Force enabling IPv6" in caplog.text

    def test_ipv6_stays_dropped_when_flag_is_false(self, run_command):
        SetupOpenFlow(
            {"bridges": [{"name": "br0", "ports": [], "enable_ipv6": False}]}
        )

        ipv6_rules = [rule for rule in flows(run_command) if "ipv6" in rule]
        assert all("action=drop" in rule for rule in ipv6_rules)

    def test_catch_all_rule_allows_everything(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0", "ports": []}]})

        assert any(
            "table=0" in rule and "priority=0" in rule and "action=normal" in rule
            for rule in flows(run_command)
        )

    @pytest.mark.parametrize(
        "port",
        [
            {"name": "p0", "type": "internal", "mac": "de:ad:be:ef:00:01", "ip": "10.0.0.1"},
            {"name": "p0", "type": "tap", "ip": "10.0.0.1"},
            {"name": "p0", "type": "tap", "mac": "de:ad:be:ef:00:01"},
            {"name": "p0", "type": "tap", "mac": "", "ip": "10.0.0.1"},
            {"name": "p0", "type": "tap", "mac": "de:ad:be:ef:00:01", "ip": ""},
        ],
    )
    def test_port_without_full_mac_and_ip_is_not_secured(self, run_command, port):
        SetupOpenFlow({"bridges": [{"name": "br0", "ports": [port]}]})

        assert not [rule for rule in flows(run_command) if "in_port=p0" in rule]


class TestPortFlows:
    def test_mac_spoofing_is_blocked(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0", "ports": [SECURED_PORT]}]})

        rules = flows(run_command)
        assert any(
            "dl_src=de:ad:be:ef:00:01" in rule and "action=goto_table:1" in rule
            for rule in rules
        )
        assert any(
            "in_port=tap0" in rule and "priority=39" in rule and "action=drop" in rule
            for rule in rules
        )

    def test_ingress_defaults_to_drop(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0", "ports": [SECURED_PORT]}]})

        assert any(
            "table=1" in rule and "priority=0" in rule and "action=drop" in rule
            for rule in flows(run_command)
        )

    def test_source_ip_is_allowed(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0", "ports": [SECURED_PORT]}]})

        assert any(
            "ip nw_src=10.0.0.1" in rule and "action=normal" in rule
            for rule in flows(run_command)
        )

    def test_every_ip_of_a_list_is_allowed(self, run_command):
        port = dict(SECURED_PORT, ip=["10.0.0.1", "10.0.0.2"])

        SetupOpenFlow({"bridges": [{"name": "br0", "ports": [port]}]})

        rules = flows(run_command)
        assert any("ip nw_src=10.0.0.1" in rule for rule in rules)
        assert any("ip nw_src=10.0.0.2" in rule for rule in rules)

    def test_arp_is_restricted_to_the_declared_ip(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0", "ports": [SECURED_PORT]}]})

        rules = flows(run_command)
        assert any(
            "arp arp_sha=de:ad:be:ef:00:01 arp_spa=10.0.0.1" in rule
            and "action=normal" in rule
            for rule in rules
        )
        assert any(
            rule.count("arp_sha=de:ad:be:ef:00:01") and "arp_spa" not in rule
            and "action=drop" in rule
            for rule in rules
        )

    def test_dhcp_server_traffic_is_dropped(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0", "ports": [SECURED_PORT]}]})

        assert any(
            "udp udp_src=67" in rule and "action=drop" in rule
            for rule in flows(run_command)
        )

    def test_priorities_stay_ordered_within_table_1(self, run_command):
        port = dict(SECURED_PORT, ip=["10.0.0.1", "10.0.0.2"])

        SetupOpenFlow({"bridges": [{"name": "br0", "ports": [port]}]})

        priorities = [
            int(rule.split("priority=")[1].split(" ")[0])
            for rule in flows(run_command)
            if "table=1" in rule and "in_port=tap0" in rule
        ]
        assert priorities == sorted(priorities)

    def test_dpdkvhostuserclient_ports_are_secured_too(self, run_command):
        port = dict(SECURED_PORT, type="dpdkvhostuserclient", name="vhost0")

        SetupOpenFlow({"bridges": [{"name": "br0", "ports": [port]}]})

        assert [rule for rule in flows(run_command) if "in_port=vhost0" in rule]


class TestAddFlow:
    def test_omits_in_port_when_not_given(self, run_command):
        SetupOpenFlow.add_flow("br0", 0, 10, "normal", "ip")

        assert "in_port" not in run_command.commands[0]

    def test_includes_in_port_when_given(self, run_command):
        SetupOpenFlow.add_flow("br0", 0, 10, "normal", "ip", port="tap0")

        assert "in_port=tap0" in run_command.commands[0]

    def test_builds_the_expected_rule(self, run_command):
        SetupOpenFlow.add_flow("br0", 1, 20, "drop", "arp")

        args, _ = run_command.calls[0]
        assert args[:3] == ("ovs-ofctl", "add-flow", "br0")
        assert "table=1" in args[3]
        assert "priority=20" in args[3]
        assert "action=drop" in args[3]


class TestFlowsOverride:
    def test_override_replaces_the_flows_from_a_temp_file(self, run_command):
        SetupOpenFlow(
            {
                "bridges": [
                    {"name": "br0", "flows_override": "priority=0,action=drop\n"}
                ]
            }
        )

        replace = run_command.commands_containing("replace-flows")
        assert len(replace) == 1
        assert "--bundle" in replace[0]
        assert "br0" in replace[0]

    def test_override_content_is_written_before_the_call(self, monkeypatch):
        written = {}

        def spy(*args, **kwargs):
            if "replace-flows" in args:
                with open(args[-1]) as handle:
                    written["content"] = handle.read()

        monkeypatch.setattr(helpers, "run_command", spy)

        SetupOpenFlow(
            {
                "bridges": [
                    {"name": "br0", "flows_override": "priority=0,action=drop\n"}
                ]
            }
        )

        assert written["content"] == "priority=0,action=drop\n"

    def test_empty_override_is_ignored(self, run_command):
        SetupOpenFlow({"bridges": [{"name": "br0", "flows_override": ""}]})

        assert not run_command.commands_containing("replace-flows")

    def test_override_combines_with_port_rules(self, run_command):
        SetupOpenFlow(
            {
                "bridges": [
                    {
                        "name": "br0",
                        "ports": [SECURED_PORT],
                        "flows_override": "priority=0,action=drop\n",
                    }
                ]
            }
        )

        assert run_command.commands_containing("replace-flows")
        assert run_command.commands_containing("del-flows br0")
