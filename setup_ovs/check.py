# Copyright (C) 2021, RTE (http://www.rte-france.com)
# SPDX-License-Identifier: Apache-2.0

import logging
import subprocess
import os
from . import helpers
from .setup_ovs_exception import SetupOVSConfigException


def system_check():
    """
    Check if the system is ready to apply an OVS configuration
    Raise an exception if the check failed
    """
    logging.info("Check OVS is up")
    try:
        helpers.run_command("/usr/bin/ovs-vsctl", "show", capture_output=True)
    except subprocess.CalledProcessError as e:
        logging.error("OVS is not running or unavailable")
        raise e


def configuration_check(config):
    """
    Check the JSON configuration file and extract the DPDK interfaces from
    it. Raise an exception if the check failed
    :param config: The configuration which describes the OVS setup
    """
    logging.info("Checking configuration")
    if not isinstance(config, dict):
        raise SetupOVSConfigException(
            "The configuration should be a dictionary"
        )
    dpdk_interfaces = []
    system_interfaces = []
    if "bridges" in config:
        if not isinstance(config["bridges"], list):
            raise SetupOVSConfigException(
                "bridges configuration should be a list"
            )
        for bridge in config["bridges"]:
            if not isinstance(bridge, dict):
                raise SetupOVSConfigException("A bridge must be a dictionary")
            if "name" not in bridge:
                raise SetupOVSConfigException("Bridge without name attribute")
            logging.debug("Checking: " + bridge["name"])
            if "ports" in bridge:
                if not isinstance(bridge["ports"], list):
                    raise SetupOVSConfigException("ports must be a list")
                for port in bridge["ports"]:
                    if not isinstance(port, dict):
                        raise SetupOVSConfigException(
                            "A port must be a dictionary"
                        )
                    _check_port_configuration(
                        bridge["name"],
                        port,
                        dpdk_interfaces,
                        system_interfaces,
                    )
            if "other_config" in bridge:
                attribute_value = (
                    [bridge["other_config"]]
                    if isinstance(bridge["other_config"], str)
                    else bridge["other_config"]
                )
                if not isinstance(attribute_value, list):
                    raise SetupOVSConfigException(
                        "Bridge {}: other_config must be an string or a "
                        "strings list".format(bridge["name"])
                    )
                for element in attribute_value:
                    if not isinstance(element, str):
                        raise SetupOVSConfigException(
                            "Bridge {}: other_config must be an string or a "
                            "strings list".format(bridge["name"])
                        )
            for attribute in ("rstp_enable", "enable_ipv6"):
                if attribute in bridge and not isinstance(bridge[attribute], bool):
                    raise SetupOVSConfigException(
                        "Bridge {}: {} must be a boolean".format(
                            bridge["name"], attribute
                        )
                    )
    if "unbind_pci_address" in config:
        if not isinstance(config["unbind_pci_address"], list):
            raise SetupOVSConfigException(
                "unbind_pci_address should be a PCI addresses list"
            )

        for pci_address in config["unbind_pci_address"]:
            if not isinstance(pci_address, str):
                raise SetupOVSConfigException("A pci_address must be a string")
            if not helpers.PCI_ADDRESS_MATCHER.match(pci_address):
                raise SetupOVSConfigException(
                    f"{pci_address} is not a PCI address"
                )

    logging.info("Configuration check: OK")


def _attribute_is_in_range(
    attribute_name, attribute_value, bridge_name, port_name, maximum
):
    """
    Check if an attribute is an integer between 0 and maximum
    :param attribute_name: The attribute name
    :param attribute_value: The attribute value
    :param bridge_name: The attribute bridge name
    :param port_name: The attribute port name
    :param maximum: The highest accepted value, included
    """
    if not isinstance(attribute_value, int):
        raise SetupOVSConfigException(
            "Bridge {} Port {}: attribute {} must be an "
            "integer".format(bridge_name, port_name, attribute_name)
        )
    if attribute_value < 0 or attribute_value > maximum:
        raise SetupOVSConfigException(
            "Bridge {} Port {}: attribute {} must be in range 0 to "
            "{:,}".format(bridge_name, port_name, attribute_name, maximum)
        )


def _attribute_is_a_port(
    attribute_name, attribute_value, bridge_name, port_name
):
    """
    Check if an attribute is a TCP or UDP port
    :param attribute_name: The attribute name
    :param attribute_value: The attribute value
    :param bridge_name: The attribute bridge name
    :param port_name: The attribute port name
    """
    _attribute_is_in_range(
        attribute_name, attribute_value, bridge_name, port_name, 65535
    )


def _attribute_is_a_vlan_tag(
    attribute_name, attribute_value, bridge_name, port_name
):
    """
    Check if an attribute is a 802.1Q VLAN identifier
    :param attribute_name: The attribute name
    :param attribute_value: The attribute value
    :param bridge_name: The attribute bridge name
    :param port_name: The attribute port name
    """
    _attribute_is_in_range(
        attribute_name, attribute_value, bridge_name, port_name, 4095
    )


def _attribute_is_an_ipv4(
    attribute_name, attribute_value, bridge_name, port_name
):
    """
    Check if an attribute is an IPv4 address
    :param attribute_name: The attribute name
    :param attribute_value: The attribute value
    :param bridge_name: The attribute bridge name
    :param port_name: The attribute port name
    """
    if not isinstance(attribute_value, str) or not helpers.IPv4_ADDRESS_MATCHER.match(
        attribute_value
    ):
        raise SetupOVSConfigException(
            "Bridge {} Port {}: attribute {} must be an"
            " IPv4 address".format(bridge_name, port_name, attribute_name)
        )


def _attribute_is_a_mac(
    attribute_name, attribute_value, bridge_name, port_name
):
    """
    Check if an attribute is a MAC address
    :param attribute_name: The attribute name
    :param attribute_value: The attribute value
    :param bridge_name: The attribute bridge name
    :param port_name: The attribute port name
    """
    if not isinstance(attribute_value, str) or not helpers.MAC_ADDRESS_MATCHER.match(
        attribute_value
    ):
        raise SetupOVSConfigException(
            "Bridge {} Port {}: attribute {} must be an"
            ' MAC address, in lower case with ":" as separator'.format(
                bridge_name, port_name, attribute_name
            )
        )


PORT_TYPES = (
    "internal",
    "tap",
    "system",
    "dpdk",
    "dpdkvhostuserclient",
    "vxlan",
)

VLAN_MODES = ("access", "native-tagged", "native-untagged", "trunk")

MAC_CAPABLE_TYPES = ("tap", "dpdkvhostuserclient")


def _check_name_and_type(bridge_name, port):
    """
    Check the two mandatory port attributes
    :param bridge_name: the bridge name in which the port take from
    :param port: the port configuration
    :return: the port name and the port type
    """
    if "name" not in port:
        raise SetupOVSConfigException(
            "Bridge {}: Port without name attribute".format(bridge_name)
        )
    port_name = port["name"]
    logging.debug("Checking Port: " + port_name)
    if "type" not in port:
        raise SetupOVSConfigException(
            "Bridge {}: Port without type attribute".format(bridge_name)
        )
    port_type = port["type"]
    if port_type not in PORT_TYPES:
        raise SetupOVSConfigException(
            "Bridge {} Port {}: Bad type value: {}".format(
                bridge_name, port_name, port_type
            )
        )
    return port_name, port_type


def _lspci_address(bridge_name, port_name, interface):
    """
    Convert a NIC PCI address in the lspci format
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param interface: the interface PCI address
    :return: the address in the lspci format
    """
    match = helpers.PCI_ADDRESS_MATCHER.match(interface)
    if not match:
        raise SetupOVSConfigException(
            "Bridge {} Port {}: NIC {} is not a PCI address."
            " Invalid format".format(bridge_name, port_name, interface)
        )
    match_group = match.groupdict()
    lspci_nic_address_part1 = int(match_group["part1"], 16)
    lspci_nic_address_part2 = int(match_group["part2"], 16)
    lspci_nic_address_part3 = int(match_group["part3"], 16)
    return (
        f"{lspci_nic_address_part1:02x}:"
        f"{lspci_nic_address_part2:02x}."
        f"{lspci_nic_address_part3:01x}"
    )


def _check_dpdk_interface(
    bridge_name, port_name, interface, dpdk_interfaces
):
    """
    Check a DPDK port interface and claim the NIC
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param interface: the interface PCI address
    :param dpdk_interfaces: the NICs already claimed, appended to by this call
    """
    lspci_nic_address = _lspci_address(bridge_name, port_name, interface)
    if not helpers.dry_run:
        try:
            helpers.run_command(
                "/usr/bin/lspci -mm | /bin/grep -q " + lspci_nic_address,
                shell=True,
            )
        except subprocess.CalledProcessError:
            raise SetupOVSConfigException(
                "Bridge {} Port {}: Can't find the NIC {}".format(
                    bridge_name, port_name, interface
                )
            )
    if interface in dpdk_interfaces:
        raise SetupOVSConfigException(
            "Bridge {} Port {}: NIC {} already used in another "
            "port".format(bridge_name, port_name, interface)
        )
    dpdk_interfaces.append(interface)


def _check_system_interface(
    bridge_name, port_name, interface, system_interfaces
):
    """
    Check a system port interface and claim the NIC
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param interface: the network interface name
    :param system_interfaces: the NICs already claimed, appended to by this
                              call
    """
    if not os.path.isdir(
        os.path.join("/proc/sys/net/ipv4/conf/", interface)
    ):
        message = (
            "Bridge {} Port {}: could not find the network "
            "interface {}".format(bridge_name, port_name, interface)
        )
        if helpers.dry_run:
            logging.error(message)
        else:
            raise SetupOVSConfigException(message)
    if interface in system_interfaces:
        raise SetupOVSConfigException(
            "Bridge {} Port {}: {} already used in another"
            " port".format(bridge_name, port_name, interface)
        )
    system_interfaces.append(interface)


def _check_interface(
    bridge_name, port_name, port_type, port, dpdk_interfaces,
    system_interfaces
):
    """
    Check the interface attribute, which only the dpdk and system types use
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param port_type: the port type
    :param port: the port configuration
    :param dpdk_interfaces: the DPDK NICs already claimed
    :param system_interfaces: the system NICs already claimed
    """
    if port_type not in ("dpdk", "system"):
        if "interface" in port:
            logging.warning(
                "Bridge {} Port {}: attribute interface is ignored when "
                " type is not system nor dpdk".format(bridge_name, port_name)
            )
        return
    if "interface" not in port:
        raise SetupOVSConfigException(
            "Bridge {} Port {}: attribute interface is required with "
            "type {}".format(bridge_name, port_name, port_type)
        )
    interface = port["interface"]
    if port_type == "dpdk":
        _check_dpdk_interface(
            bridge_name, port_name, interface, dpdk_interfaces
        )
    else:
        _check_system_interface(
            bridge_name, port_name, interface, system_interfaces
        )


def _check_vxlan_attributes(bridge_name, port_name, port_type, port):
    """
    Check the attributes a vxlan port requires, and warn about them on any
    other type
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param port_type: the port type
    :param port: the port configuration
    """
    if port_type == "vxlan":
        for attribute in ("key", "remote_ip"):
            if attribute not in port:
                raise SetupOVSConfigException(
                    "Bridge {} Port {}: {} must be set if type is "
                    "vxlan".format(bridge_name, port_name, attribute)
                )
        return
    for attribute in ("key", "remote_ip", "remote_port"):
        if attribute in port:
            logging.warning(
                "Bridge {} Port {}: attribute {} is ignored"
                " when type is not vxlan".format(
                    bridge_name, port_name, attribute
                )
            )


def _check_vlan_attributes(bridge_name, port_name, port):
    """
    Check the tag, trunks and vlan_mode attributes
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param port: the port configuration
    """
    if "tag" in port:
        _attribute_is_a_vlan_tag("tag", port["tag"], bridge_name, port_name)
    if "trunks" in port:
        trunks = (
            [port["trunks"]]
            if isinstance(port["trunks"], int)
            else port["trunks"]
        )
        if not isinstance(trunks, list):
            raise SetupOVSConfigException(
                "Bridge {} Port {}: attribute trunks must be an integer or"
                " an integer list".format(bridge_name, port_name)
            )
        for trunk in trunks:
            _attribute_is_a_vlan_tag("trunks", trunk, bridge_name, port_name)
    if "vlan_mode" in port and port["vlan_mode"] not in VLAN_MODES:
        raise SetupOVSConfigException(
            "Bridge {} Port {}: Bad vlan_mode value: {}".format(
                bridge_name, port_name, port["vlan_mode"]
            )
        )


def _check_integer_attributes(bridge_name, port_name, port):
    """
    Check the policing attributes and remote_port
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param port: the port configuration
    """
    for attribute in ("ingress_policing_rate", "ingress_policing_burst"):
        if attribute in port and not isinstance(port[attribute], int):
            raise SetupOVSConfigException(
                "Bridge {} Port {}: attribute {} must be an "
                "integer".format(bridge_name, port_name, attribute)
            )
    if "remote_port" in port:
        _attribute_is_a_port(
            "remote_port", port["remote_port"], bridge_name, port_name
        )


def _check_string_attributes(bridge_name, port_name, port):
    """
    Check the attributes which must be plain strings
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param port: the port configuration
    """
    for attribute in ("key", "remote_ip", "hook_file"):
        if attribute not in port:
            continue
        if not isinstance(port[attribute], str):
            raise SetupOVSConfigException(
                "Bridge {} Port {}: attribute {} must be a "
                "string".format(bridge_name, port_name, attribute)
            )
        if attribute == "remote_ip":
            _attribute_is_an_ipv4(
                attribute, port[attribute], bridge_name, port_name
            )


def _check_ip_element(bridge_name, port_name, port, attribute, element):
    """
    Check one element of the ip attribute
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param port: the port configuration
    :param attribute: the attribute name
    :param element: the element to check
    """
    _attribute_is_an_ipv4(attribute, element, bridge_name, port_name)
    if port["type"] not in MAC_CAPABLE_TYPES:
        raise SetupOVSConfigException(
            "Bridge {} Port {}: attribute {} only works if"
            " interface is tap or"
            " dpdkvhostuserclient".format(bridge_name, port_name, attribute)
        )


def _as_string_list(bridge_name, port_name, attribute, port):
    """
    Read an attribute accepting a string or a string list, as a list
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param attribute: the attribute name
    :param port: the port configuration
    :return: the attribute value as a list
    """
    attribute_value = (
        [port[attribute]]
        if isinstance(port[attribute], str)
        else port[attribute]
    )
    if not isinstance(attribute_value, list):
        raise SetupOVSConfigException(
            "Bridge {} Port {}: attribute {} must be a string or "
            "a string list".format(bridge_name, port_name, attribute)
        )
    return attribute_value


def _check_list_attributes(bridge_name, port_name, port):
    """
    Check the attributes accepting a string or a string list
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param port: the port configuration
    """
    if "other_config" in port:
        for element in _as_string_list(
            bridge_name, port_name, "other_config", port
        ):
            if not isinstance(element, str):
                raise SetupOVSConfigException(
                    "Bridge {} Port {}: attribute {} must be a string"
                    " or a string list".format(
                        bridge_name, port_name, "other_config"
                    )
                )
    if "ip" in port:
        for element in _as_string_list(bridge_name, port_name, "ip", port):
            _check_ip_element(bridge_name, port_name, port, "ip", element)


def _check_mac_attribute(bridge_name, port_name, port):
    """
    Check the mac attribute
    :param bridge_name: the port bridge name
    :param port_name: the port name
    :param port: the port configuration
    """
    if "mac" not in port:
        return
    _attribute_is_a_mac("mac", port["mac"], bridge_name, port_name)
    if port["type"] not in MAC_CAPABLE_TYPES:
        raise SetupOVSConfigException(
            "Bridge {} Port {}: attribute mac only works if"
            " interface is tap or"
            " dpdkvhostuserclient".format(bridge_name, port_name)
        )


def _check_port_configuration(
    bridge_name, port, dpdk_interfaces, system_interfaces
):
    """
    Helper method for _configuration_check which checks the port
    configuration
    :param bridge_name: the bridge name in which the port take from
    :param port: the port configuration
    :param dpdk_interfaces: the DPDK NICs already claimed by another port,
                            appended to by this call
    :param system_interfaces: the system NICs already claimed by another
                              port, appended to by this call
    """
    port_name, port_type = _check_name_and_type(bridge_name, port)
    _check_interface(
        bridge_name,
        port_name,
        port_type,
        port,
        dpdk_interfaces,
        system_interfaces,
    )
    _check_vxlan_attributes(bridge_name, port_name, port_type, port)
    _check_vlan_attributes(bridge_name, port_name, port)
    _check_integer_attributes(bridge_name, port_name, port)
    _check_string_attributes(bridge_name, port_name, port)
    _check_list_attributes(bridge_name, port_name, port)
    _check_mac_attribute(bridge_name, port_name, port)
