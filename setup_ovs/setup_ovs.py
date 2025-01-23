#!/usr/bin/env python3
# Copyright (C) 2021, RTE (http://www.rte-france.com)
# SPDX-License-Identifier: Apache-2.0

import argparse
import logging
import json
import os
import yaml
import pathlib
import sys

import setup_ovs
from . import helpers
from . import check
from . import ovs
from . import openflow

def main():
    parser = argparse.ArgumentParser(description="Setup OVS bridges and ports")
    parser.add_argument(
        "-v",
        "--verbose",
        help="increase output verbosity",
        action="store_true",
        required=False,
    )
    parser.add_argument(
        "-f",
        "--file",
        help="Path to the OVS configuration JSON file",
        type=str,
        default="/etc/ovs_configuration.json",
    )
    parser.add_argument(
        "-c",
        "--check",
        help="Check only the configuration file. Do not apply the "
        "configuration",
        action="store_true",
        required=False,
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        help="Do not run any command.",
        action="store_true",
        required=False,
    )
    parser.add_argument(
        "--no-ovs",
        help="Do not configure OVS bridges and port."
        " Only apply OpenFlow filters",
        action="store_true",
        required=False,
    )
    parser.add_argument(
        "--no-openflow",
        help="Do not apply openflow filters.",
        action="store_true",
        required=False,
    )
    parser.add_argument(
        "--no-remove-bridges",
        help="Do not remove all OVS bridges before applying the configuration.",
        action="store_true",
        required=False,
    )
    parser.add_argument(
        "--no-remove-interfaces",
        help="Do not remove all tap interfaces before applying the configuration."
        " All VMs must be stopped if this argument is used.",
        action="store_true",
        required=False,
    )
    parser.add_argument(
        "--no-unbind",
        help="Do not unbind PCI devices",
        action="store_true",
        required=False,
    )
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
        logging.debug("Enable debug traces")
    else:
        logging.basicConfig(level=logging.WARNING)
    helpers.dry_run = args.dry_run
    if args.dry_run:
        logging.debug("Dry-run enable")
    if args.check:
        logging.debug("Check configuration file only")
    logging.debug("Configuration file: " + args.file)
    if os.path.isfile(args.file):
        with open(args.file, "r") as fd:
            file_content = fd.read()
            logging.debug("Configuration:\n" + file_content)
            if pathlib.Path(args.file).suffix in (".yaml", ".yml"):
                config = yaml.safe_load(file_content)
            else:
                config = json.loads(file_content)
    else:
        config = []
        logging.warning(f"Configuration file {args.file} not found ")
        sys.exit()

    check.configuration_check(config)
    if not args.check:
        check.system_check()
        if not args.no_remove_bridges:
            ovs.clear_ovs(config)
        if not args.no_remove_interfaces:
            ovs.clear_tap(config)
        if not args.no_unbind:
            ovs.unbind_pci(config)
        if not args.no_ovs:
            ovs.setup_ovs(config)
        if not args.no_openflow:
            openflow.SetupOpenFlow(config)

if __name__ == "__main__":
    main()
