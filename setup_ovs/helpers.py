# Copyright (C) 2021, RTE (http://www.rte-france.com)
# SPDX-License-Identifier: Apache-2.0

import functools
import os
import re
import shutil
import subprocess
import logging

PCI_ADDRESS_MATCHER = re.compile(
    r"^((0{1,4}:)?(?P<part1>[0-9a-f]{1,2}):(?P<part2>[0-9a-f]{1,2})."
    r"(?P<part3>[0-9a-f]))$"
)

IPv4_ADDRESS_MATCHER = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")

MAC_ADDRESS_MATCHER = re.compile(r"^([0-9a-f][0-9a-f]:){5}([0-9a-f][0-9a-f])$")

dry_run = False


@functools.lru_cache(maxsize=None)
def find_command(logical_name, *candidates):
    """Locate a system command by trying candidate names/paths."""
    for candidate in candidates:
        if os.path.isabs(candidate):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                logging.debug("Found %s at %s", logical_name, candidate)
                return candidate
        else:
            resolved = shutil.which(candidate)
            if resolved:
                logging.debug("Found %s at %s (via PATH)", logical_name, resolved)
                return resolved
    raise FileNotFoundError(
        "Could not find {}: looked for {}".format(logical_name, ", ".join(candidates))
    )


def run_command(*cmd_args, **kargs):
    """
    Shell runner helper
    Work as subproccess.run except check is set to true by default and
    stdout is not printed unless the logging level is DEBUG
    """
    logging.debug("Run command: " + " ".join(map(str, cmd_args)))
    if not dry_run:
        if "check" not in kargs:
            kargs["check"] = True
        if (
            logging.getLogger().getEffectiveLevel() != logging.DEBUG
            and "stdout" not in kargs
            and (
                "capture_output" not in kargs
                or not kargs["capture_output"]
            )
        ):
            kargs["stdout"] = subprocess.DEVNULL
        return subprocess.run(cmd_args, **kargs)
