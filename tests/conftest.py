# Copyright (C) 2025, RTE (http://www.rte-france.com)
# SPDX-License-Identifier: Apache-2.0

import pytest

from setup_ovs import helpers


@pytest.fixture(autouse=True)
def reset_helpers_state(monkeypatch):
    """
    helpers.dry_run is a module global and find_command is memoized.
    Both leak between tests, so reset them around every test. monkeypatch
    restores dry_run on teardown, tests that need it on just set it the
    same way.
    """
    # Captured before the test body runs, so the teardown still clears the
    # real memoized function even when a test monkeypatches find_command.
    real_find_command = helpers.find_command
    real_find_command.cache_clear()
    monkeypatch.setattr(helpers, "dry_run", False)
    yield
    real_find_command.cache_clear()


@pytest.fixture
def run_command(monkeypatch):
    """
    Replace helpers.run_command by a recorder shared by every module.

    The modules under test call helpers.run_command through the module
    object, so patching the attribute once covers ovs, openflow and check.
    """

    class Recorder:
        def __init__(self):
            self.calls = []

        def __call__(self, *cmd_args, **kwargs):
            self.calls.append((cmd_args, kwargs))
            return None

        @property
        def commands(self):
            """Every call flattened to a single string, for substring asserts."""
            return [" ".join(map(str, args)) for args, _ in self.calls]

        def commands_containing(self, needle):
            return [cmd for cmd in self.commands if needle in cmd]

    recorder = Recorder()
    monkeypatch.setattr(helpers, "run_command", recorder)
    return recorder
