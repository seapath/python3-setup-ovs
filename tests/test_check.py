# Copyright (C) 2025, RTE (http://www.rte-france.com)
# SPDX-License-Identifier: Apache-2.0

import subprocess

import pytest

from setup_ovs import check, helpers
from setup_ovs.setup_ovs_exception import SetupOVSConfigException


IPV4_CONF_ROOT = "/proc/sys/net/ipv4/conf"


@pytest.fixture
def system_interfaces(monkeypatch):
    """
    Answer os.path.isdir for /proc/sys/net/ipv4/conf lookups only.

    check.os is the stdlib os module, so the patch is global: every other
    path must keep its real behaviour or pytest's own internals break.
    """
    real_isdir = check.os.path.isdir

    def configure(answer):
        def fake_isdir(path):
            if str(path).startswith(IPV4_CONF_ROOT):
                return answer
            return real_isdir(path)

        monkeypatch.setattr(check.os.path, "isdir", fake_isdir)

    return configure


@pytest.fixture
def existing_interfaces(system_interfaces):
    """Pretend every network interface referenced by a config exists."""
    system_interfaces(True)


def bridge_with_port(port, **bridge_extra):
    bridge = {"name": "br0", "ports": [port]}
    bridge.update(bridge_extra)
    return {"bridges": [bridge]}


def assert_rejects(config, match):
    """
    Assert that configuration_check refuses config.

    The configuration is built by the caller so that the assertion block
    holds a single call, which keeps what is under test unambiguous.
    """
    with pytest.raises(SetupOVSConfigException, match=match):
        check.configuration_check(config)


class TestSystemCheck:
    def test_passes_when_ovs_answers(self, run_command):
        check.system_check()

        assert run_command.commands == ["/usr/bin/ovs-vsctl show"]

    def test_reraises_when_ovs_is_down(self, monkeypatch):
        def fake_run_command(*args, **kwargs):
            raise subprocess.CalledProcessError(1, args)

        monkeypatch.setattr(helpers, "run_command", fake_run_command)

        with pytest.raises(subprocess.CalledProcessError):
            check.system_check()


class TestConfigurationShape:
    @pytest.mark.parametrize("config", [[], "string", 42, None])
    def test_rejects_non_dict_configuration(self, config):
        assert_rejects(config, "should be a dictionary")

    def test_accepts_empty_configuration(self):
        check.configuration_check({})

    def test_rejects_non_list_bridges(self):
        assert_rejects({"bridges": {"name": "br0"}}, "should be a list")

    def test_rejects_non_dict_bridge(self):
        assert_rejects({"bridges": ["br0"]}, "must be a dictionary")

    def test_rejects_bridge_without_name(self):
        assert_rejects({"bridges": [{"ports": []}]}, "without name")

    def test_accepts_bridge_without_ports(self):
        check.configuration_check({"bridges": [{"name": "br0"}]})

    def test_rejects_non_list_ports(self):
        config = {"bridges": [{"name": "br0", "ports": {"name": "p0"}}]}

        assert_rejects(config, "ports must be a list")

    def test_rejects_non_dict_port(self):
        config = {"bridges": [{"name": "br0", "ports": ["p0"]}]}

        assert_rejects(config, "port must be a dictionary")


class TestBridgeAttributes:
    def test_accepts_other_config_as_string(self):
        check.configuration_check(
            {"bridges": [{"name": "br0", "other_config": "a=b"}]}
        )

    def test_accepts_other_config_as_string_list(self):
        check.configuration_check(
            {"bridges": [{"name": "br0", "other_config": ["a=b", "c=d"]}]}
        )

    def test_rejects_other_config_of_wrong_type(self):
        config = {"bridges": [{"name": "br0", "other_config": {"a": "b"}}]}

        assert_rejects(config, "other_config")

    def test_rejects_other_config_list_with_non_string(self):
        config = {"bridges": [{"name": "br0", "other_config": ["a=b", 3]}]}

        assert_rejects(config, "other_config")

    @pytest.mark.parametrize("attribute", ["rstp_enable", "enable_ipv6"])
    def test_rejects_non_boolean_flags(self, attribute):
        config = {"bridges": [{"name": "br0", attribute: "yes"}]}

        assert_rejects(config, "must be a boolean")

    @pytest.mark.parametrize("attribute", ["rstp_enable", "enable_ipv6"])
    def test_accepts_boolean_flags(self, attribute):
        check.configuration_check({"bridges": [{"name": "br0", attribute: True}]})


class TestUnbindPciAddress:
    def test_accepts_valid_addresses(self):
        check.configuration_check({"unbind_pci_address": ["0000:3b:00.0", "5e:00.1"]})

    def test_rejects_non_list(self):
        assert_rejects({"unbind_pci_address": "0000:3b:00.0"}, "addresses list")

    def test_rejects_non_string_entry(self):
        assert_rejects({"unbind_pci_address": [42]}, "must be a string")

    def test_rejects_malformed_address(self):
        assert_rejects(
            {"unbind_pci_address": ["not-a-pci"]}, "is not a PCI address"
        )


class TestPortBasics:
    def test_rejects_port_without_name(self):
        assert_rejects(bridge_with_port({"type": "tap"}), "without name")

    def test_rejects_port_without_type(self):
        assert_rejects(bridge_with_port({"name": "p0"}), "without type")

    def test_rejects_unknown_type(self):
        config = bridge_with_port({"name": "p0", "type": "wormhole"})

        assert_rejects(config, "Bad type value")

    @pytest.mark.parametrize(
        "port_type",
        ["internal", "tap", "dpdkvhostuserclient"],
    )
    def test_accepts_types_needing_no_interface(self, port_type):
        check.configuration_check(
            bridge_with_port({"name": "p0", "type": port_type})
        )

    def test_accepts_a_complete_vxlan_port(self):
        check.configuration_check(
            bridge_with_port(
                {
                    "name": "p0",
                    "type": "vxlan",
                    "key": "42",
                    "remote_ip": "10.0.0.1",
                }
            )
        )

    def test_warns_when_interface_is_ignored(self, caplog):
        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": "tap", "interface": "eth0"}
            )
        )

        assert "interface is ignored" in caplog.text


class TestSystemPort:
    def test_accepts_existing_interface(self, existing_interfaces):
        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": "system", "interface": "eth0"}
            )
        )

    def test_rejects_missing_interface_attribute(self):
        config = bridge_with_port({"name": "p0", "type": "system"})

        assert_rejects(config, "interface is required")

    def test_rejects_unknown_interface(self, system_interfaces):
        system_interfaces(False)
        config = bridge_with_port(
            {"name": "p0", "type": "system", "interface": "eth9"}
        )

        assert_rejects(config, "could not find the network")

    def test_only_logs_unknown_interface_in_dry_run(
        self, system_interfaces, monkeypatch, caplog
    ):
        system_interfaces(False)
        monkeypatch.setattr(helpers, "dry_run", True)

        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": "system", "interface": "eth9"}
            )
        )

        assert "could not find the network" in caplog.text


class TestDpdkPort:
    def test_rejects_missing_interface_attribute(self):
        config = bridge_with_port({"name": "p0", "type": "dpdk"})

        assert_rejects(config, "interface is required")

    def test_rejects_non_pci_interface(self):
        config = bridge_with_port(
            {"name": "p0", "type": "dpdk", "interface": "eth0"}
        )

        assert_rejects(config, "not a PCI address")

    def test_looks_the_nic_up_with_lspci(self, run_command):
        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": "dpdk", "interface": "0000:3b:00.0"}
            )
        )

        assert run_command.commands_containing("lspci")
        assert "3b:00.0" in run_command.commands[0]

    def test_normalises_the_pci_address_for_lspci(self, run_command):
        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": "dpdk", "interface": "0:3:0.0"}
            )
        )

        assert "03:00.0" in run_command.commands[0]

    def test_rejects_nic_absent_from_lspci(self, monkeypatch):
        def fake_run_command(*args, **kwargs):
            raise subprocess.CalledProcessError(1, args)

        monkeypatch.setattr(helpers, "run_command", fake_run_command)
        config = bridge_with_port(
            {"name": "p0", "type": "dpdk", "interface": "0000:3b:00.0"}
        )

        assert_rejects(config, "Can't find the NIC")

    def test_skips_the_lspci_lookup_in_dry_run(self, run_command, monkeypatch):
        monkeypatch.setattr(helpers, "dry_run", True)

        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": "dpdk", "interface": "0000:3b:00.0"}
            )
        )

        assert run_command.calls == []


class TestVlanAttributes:
    @pytest.mark.parametrize(
        "vlan_mode", ["access", "native-tagged", "native-untagged", "trunk"]
    )
    def test_accepts_known_vlan_modes(self, vlan_mode):
        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": "tap", "vlan_mode": vlan_mode}
            )
        )

    def test_rejects_unknown_vlan_mode(self):
        config = bridge_with_port(
            {"name": "p0", "type": "tap", "vlan_mode": "sideways"}
        )

        assert_rejects(config, "Bad vlan_mode")

    def test_accepts_single_trunk(self):
        check.configuration_check(
            bridge_with_port({"name": "p0", "type": "tap", "trunks": 100})
        )

    def test_accepts_trunk_list(self):
        check.configuration_check(
            bridge_with_port({"name": "p0", "type": "tap", "trunks": [1, 2, 3]})
        )

    def test_rejects_trunks_of_wrong_type(self):
        config = bridge_with_port(
            {"name": "p0", "type": "tap", "trunks": {"a": 1}}
        )

        assert_rejects(config, "trunks")

    def test_rejects_non_integer_trunk(self):
        config = bridge_with_port({"name": "p0", "type": "tap", "trunks": ["1"]})

        assert_rejects(config, "must be an integer")

    @pytest.mark.parametrize("value", [-1, 4096])
    def test_rejects_out_of_range_trunk(self, value):
        config = bridge_with_port(
            {"name": "p0", "type": "tap", "trunks": [value]}
        )

        assert_rejects(config, "range 0 to")

    @pytest.mark.parametrize("value", [-1, 4096, 9999])
    def test_rejects_out_of_range_tag(self, value):
        config = bridge_with_port({"name": "p0", "type": "tap", "tag": value})

        assert_rejects(config, "range 0 to 4,095")

    def test_rejects_non_integer_tag(self):
        config = bridge_with_port({"name": "p0", "type": "tap", "tag": "10"})

        assert_rejects(config, "must be an integer")

    @pytest.mark.parametrize("value", [0, 10, 4095])
    def test_accepts_in_range_tag(self, value):
        check.configuration_check(
            bridge_with_port({"name": "p0", "type": "tap", "tag": value})
        )

    def test_vlan_without_tag_is_not_a_crash(self):
        # "vlan" is not consumed by ovs._create_bridges. It used to gate the
        # tag check and raised KeyError when tag was absent.
        check.configuration_check(
            bridge_with_port({"name": "p0", "type": "tap", "vlan": 10})
        )


class TestPolicingAndVxlan:
    @pytest.mark.parametrize(
        "attribute", ["ingress_policing_rate", "ingress_policing_burst"]
    )
    def test_accepts_integer_policing(self, attribute):
        check.configuration_check(
            bridge_with_port({"name": "p0", "type": "tap", attribute: 1000})
        )

    @pytest.mark.parametrize(
        "attribute", ["ingress_policing_rate", "ingress_policing_burst"]
    )
    def test_rejects_non_integer_policing(self, attribute):
        config = bridge_with_port(
            {"name": "p0", "type": "tap", attribute: "1000"}
        )

        assert_rejects(config, "must be an integer")

    def test_accepts_complete_vxlan_port(self):
        check.configuration_check(
            bridge_with_port(
                {
                    "name": "p0",
                    "type": "vxlan",
                    "key": "42",
                    "remote_ip": "10.0.0.1",
                    "remote_port": 4000,
                }
            )
        )

    def test_rejects_non_integer_remote_port(self):
        config = bridge_with_port(
            {
                "name": "p0",
                "type": "vxlan",
                "key": "42",
                "remote_ip": "10.0.0.1",
                "remote_port": "4789",
            }
        )

        assert_rejects(config, "must be an integer")

    @pytest.mark.parametrize("value", [-1, 65536])
    def test_rejects_out_of_range_remote_port(self, value):
        config = bridge_with_port(
            {
                "name": "p0",
                "type": "vxlan",
                "key": "42",
                "remote_ip": "10.0.0.1",
                "remote_port": value,
            }
        )

        assert_rejects(config, "range 0 to 65,535")

    def test_accepts_the_iana_vxlan_port(self):
        check.configuration_check(
            bridge_with_port(
                {
                    "name": "p0",
                    "type": "vxlan",
                    "key": "42",
                    "remote_ip": "10.0.0.1",
                    "remote_port": 4789,
                }
            )
        )

    def test_rejects_non_string_key(self):
        config = bridge_with_port(
            {
                "name": "p0",
                "type": "vxlan",
                "key": 42,
                "remote_ip": "10.0.0.1",
            }
        )

        assert_rejects(config, "must be a string")

    def test_rejects_malformed_remote_ip(self):
        config = bridge_with_port(
            {"name": "p0", "type": "vxlan", "key": "42", "remote_ip": "10.0.0"}
        )

        assert_rejects(config, "IPv4 address")

    def test_warns_when_vxlan_attributes_are_ignored(self, caplog):
        check.configuration_check(
            bridge_with_port({"name": "p0", "type": "tap", "key": "42"})
        )

        assert "ignored" in caplog.text

    def test_rejects_non_string_hook_file(self):
        config = bridge_with_port(
            {"name": "p0", "type": "tap", "hook_file": 1}
        )

        assert_rejects(config, "must be a string")

    def test_rejects_vxlan_port_without_key_nor_remote_ip(self):
        config = bridge_with_port({"name": "p0", "type": "vxlan"})

        assert_rejects(config, "must be set if type is vxlan")

    @pytest.mark.parametrize("missing", ["key", "remote_ip"])
    def test_rejects_vxlan_port_missing_one_attribute(self, missing):
        port = {
            "name": "p0",
            "type": "vxlan",
            "key": "42",
            "remote_ip": "10.0.0.1",
        }
        del port[missing]

        assert_rejects(
            bridge_with_port(port),
            "{} must be set if type is vxlan".format(missing),
        )


class TestIpAndMac:
    @pytest.mark.parametrize("port_type", ["tap", "dpdkvhostuserclient"])
    def test_accepts_ip_on_supported_types(self, port_type):
        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": port_type, "ip": "10.0.0.1"}
            )
        )

    def test_accepts_ip_list(self):
        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": "tap", "ip": ["10.0.0.1", "10.0.0.2"]}
            )
        )

    def test_rejects_ip_on_unsupported_type(self):
        config = bridge_with_port(
            {"name": "p0", "type": "internal", "ip": "10.0.0.1"}
        )

        assert_rejects(config, "only works if")

    def test_rejects_malformed_ip(self):
        config = bridge_with_port({"name": "p0", "type": "tap", "ip": ["nope"]})

        assert_rejects(config, "IPv4 address")

    def test_rejects_ip_of_wrong_container_type(self):
        config = bridge_with_port({"name": "p0", "type": "tap", "ip": {"a": 1}})

        assert_rejects(config, "string or")

    def test_accepts_port_other_config_string(self):
        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": "tap", "other_config": "a=b"}
            )
        )

    def test_rejects_port_other_config_non_string_element(self):
        config = bridge_with_port(
            {"name": "p0", "type": "tap", "other_config": [1]}
        )

        assert_rejects(config, "other_config")

    @pytest.mark.parametrize("port_type", ["tap", "dpdkvhostuserclient"])
    def test_accepts_mac_on_supported_types(self, port_type):
        check.configuration_check(
            bridge_with_port(
                {"name": "p0", "type": port_type, "mac": "de:ad:be:ef:00:01"}
            )
        )

    @pytest.mark.parametrize("mac", ["DE:AD:BE:EF:00:01", "de-ad-be-ef-00-01", 42])
    def test_rejects_malformed_mac(self, mac):
        config = bridge_with_port({"name": "p0", "type": "tap", "mac": mac})

        assert_rejects(config, "MAC address")

    def test_rejects_mac_on_unsupported_type(self):
        config = bridge_with_port(
            {"name": "p0", "type": "internal", "mac": "de:ad:be:ef:00:01"}
        )

        assert_rejects(config, "only works if")

    def test_mac_rejection_message_names_the_mac_attribute(self):
        config = bridge_with_port(
            {"name": "p0", "type": "internal", "mac": "de:ad:be:ef:00:01"}
        )

        with pytest.raises(SetupOVSConfigException) as excinfo:
            check.configuration_check(config)

        assert "attribute mac only works" in str(excinfo.value)


class TestDuplicateInterfaces:
    def test_rejects_the_same_dpdk_nic_on_two_ports(self, run_command):
        config = {
            "bridges": [
                {
                    "name": "br0",
                    "ports": [
                        {
                            "name": "p0",
                            "type": "dpdk",
                            "interface": "0000:3b:00.0",
                        },
                        {
                            "name": "p1",
                            "type": "dpdk",
                            "interface": "0000:3b:00.0",
                        },
                    ],
                }
            ]
        }

        assert_rejects(config, "already used")

    def test_rejects_the_same_dpdk_nic_on_two_bridges(self, run_command):
        # A NIC is claimed by a single port anywhere in the configuration,
        # so the guard spans every bridge and not only the current one.
        port = {"name": "p0", "type": "dpdk", "interface": "0000:3b:00.0"}
        config = {
            "bridges": [
                {"name": "br0", "ports": [dict(port, name="p0")]},
                {"name": "br1", "ports": [dict(port, name="p1")]},
            ]
        }

        assert_rejects(config, "already used")

    def test_rejects_the_same_system_nic_on_two_ports(
        self, existing_interfaces
    ):
        port = {"name": "p0", "type": "system", "interface": "eth0"}
        config = {
            "bridges": [
                {
                    "name": "br0",
                    "ports": [dict(port, name="p0"), dict(port, name="p1")],
                }
            ]
        }

        assert_rejects(config, "already used")

    def test_accepts_distinct_nics(self, run_command, existing_interfaces):
        check.configuration_check(
            {
                "bridges": [
                    {
                        "name": "br0",
                        "ports": [
                            {
                                "name": "p0",
                                "type": "dpdk",
                                "interface": "0000:3b:00.0",
                            },
                            {
                                "name": "p1",
                                "type": "dpdk",
                                "interface": "0000:3b:00.1",
                            },
                            {
                                "name": "p2",
                                "type": "system",
                                "interface": "eth0",
                            },
                            {
                                "name": "p3",
                                "type": "system",
                                "interface": "eth1",
                            },
                        ],
                    }
                ]
            }
        )
