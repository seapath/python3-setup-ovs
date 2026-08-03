# Copyright (C) 2025, RTE (http://www.rte-france.com)
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import subprocess

import pytest

from setup_ovs import helpers


class TestFindCommand:
    def test_absolute_candidate_is_returned_when_executable(self, tmp_path):
        script = tmp_path / "dpdk-devbind"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)

        assert helpers.find_command("devbind", str(script)) == str(script)

    def test_absolute_candidate_is_skipped_when_not_executable(self, tmp_path):
        not_exec = tmp_path / "not-exec"
        not_exec.write_text("")
        not_exec.chmod(0o644)
        usable = tmp_path / "usable"
        usable.write_text("#!/bin/sh\n")
        usable.chmod(0o755)

        found = helpers.find_command("devbind", str(not_exec), str(usable))

        assert found == str(usable)

    def test_absolute_candidate_is_skipped_when_missing(self, tmp_path):
        usable = tmp_path / "usable"
        usable.write_text("#!/bin/sh\n")
        usable.chmod(0o755)

        found = helpers.find_command(
            "devbind", str(tmp_path / "nope"), str(usable)
        )

        assert found == str(usable)

    def test_relative_candidate_is_resolved_through_path(self, monkeypatch):
        monkeypatch.setattr(
            helpers.shutil, "which", lambda name: "/usr/bin/" + name
        )

        assert helpers.find_command("ovs", "ovs-vsctl") == "/usr/bin/ovs-vsctl"

    def test_relative_candidate_falls_through_when_not_on_path(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            helpers.shutil,
            "which",
            lambda name: "/usr/bin/second" if name == "second" else None,
        )

        assert helpers.find_command("cmd", "first", "second") == "/usr/bin/second"

    def test_raises_when_no_candidate_matches(self, monkeypatch):
        monkeypatch.setattr(helpers.shutil, "which", lambda name: None)

        with pytest.raises(FileNotFoundError) as excinfo:
            helpers.find_command("devbind", "nope", "/absolute/nope")

        message = str(excinfo.value)
        assert "devbind" in message
        assert "nope" in message
        assert "/absolute/nope" in message

    def test_result_is_memoized(self, monkeypatch):
        calls = []

        def fake_which(name):
            calls.append(name)
            return "/usr/bin/" + name

        monkeypatch.setattr(helpers.shutil, "which", fake_which)

        helpers.find_command("ovs", "ovs-vsctl")
        helpers.find_command("ovs", "ovs-vsctl")

        assert calls == ["ovs-vsctl"]


class TestRunCommand:
    def test_runs_the_command_with_check_enabled(self, monkeypatch):
        recorded = {}

        def fake_run(cmd_args, **kwargs):
            recorded["cmd_args"] = cmd_args
            recorded["kwargs"] = kwargs
            return "result"

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = helpers.run_command("/usr/bin/ovs-vsctl", "show")

        assert result == "result"
        assert recorded["cmd_args"] == ("/usr/bin/ovs-vsctl", "show")
        assert recorded["kwargs"]["check"] is True

    def test_stdout_is_silenced_outside_debug(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr(
            subprocess, "run", lambda a, **k: recorded.update(k)
        )
        logging.getLogger().setLevel(logging.WARNING)

        helpers.run_command("/bin/true")

        assert recorded["stdout"] is subprocess.DEVNULL

    def test_stdout_is_kept_in_debug(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr(
            subprocess, "run", lambda a, **k: recorded.update(k)
        )
        logging.getLogger().setLevel(logging.DEBUG)
        try:
            helpers.run_command("/bin/true")
        finally:
            logging.getLogger().setLevel(logging.WARNING)

        assert "stdout" not in recorded

    def test_stdout_is_not_overridden_when_caller_sets_it(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr(
            subprocess, "run", lambda a, **k: recorded.update(k)
        )

        helpers.run_command("/bin/true", stdout=None)

        assert recorded["stdout"] is None

    def test_stdout_is_not_overridden_when_capturing_output(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr(
            subprocess, "run", lambda a, **k: recorded.update(k)
        )

        helpers.run_command("/bin/true", capture_output=True)

        assert "stdout" not in recorded
        assert recorded["capture_output"] is True

    def test_stdout_is_silenced_when_capture_output_is_false(
        self, monkeypatch
    ):
        recorded = {}
        monkeypatch.setattr(
            subprocess, "run", lambda a, **k: recorded.update(k)
        )

        helpers.run_command("/bin/true", capture_output=False)

        assert recorded["stdout"] is subprocess.DEVNULL

    def test_dry_run_does_not_execute_anything(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("subprocess.run must not be called")

        monkeypatch.setattr(subprocess, "run", boom)
        monkeypatch.setattr(helpers, "dry_run", True)

        assert helpers.run_command("/usr/bin/ovs-vsctl", "del-br", "br0") is None

    def test_propagates_called_process_error(self, monkeypatch):
        def fake_run(cmd_args, **kwargs):
            raise subprocess.CalledProcessError(1, cmd_args)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(subprocess.CalledProcessError):
            helpers.run_command("/bin/false")

    @pytest.mark.xfail(
        strict=True,
        reason="run_command returns without running anything when the caller "
        "passes check explicitly: the subprocess.run call sits inside the "
        "'if \"check\" not in kargs' branch",
    )
    def test_explicit_check_still_runs_the_command(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda a, **k: recorded.update(cmd_args=a, kwargs=k),
        )

        helpers.run_command("/bin/false", check=False)

        assert recorded["cmd_args"] == ("/bin/false",)


class TestMatchers:
    @pytest.mark.parametrize(
        "address", ["0000:3b:00.0", "3b:00.0", "0:01:02.3", "ff:ff.7"]
    )
    def test_pci_matcher_accepts_valid_addresses(self, address):
        assert helpers.PCI_ADDRESS_MATCHER.match(address)

    @pytest.mark.parametrize(
        "address", ["", "3b:00", "zz:00.0", "3B:00.0", "3b-00.0", "3b:00.00"]
    )
    def test_pci_matcher_rejects_invalid_addresses(self, address):
        assert not helpers.PCI_ADDRESS_MATCHER.match(address)

    @pytest.mark.parametrize("address", ["10.0.0.1", "192.168.1.254", "0.0.0.0"])
    def test_ipv4_matcher_accepts_valid_addresses(self, address):
        assert helpers.IPv4_ADDRESS_MATCHER.match(address)

    @pytest.mark.parametrize("address", ["10.0.0", "10.0.0.1.2", "abc"])
    def test_ipv4_matcher_rejects_invalid_addresses(self, address):
        assert not helpers.IPv4_ADDRESS_MATCHER.match(address)

    @pytest.mark.parametrize("address", ["00:11:22:33:44:55", "de:ad:be:ef:00:01"])
    def test_mac_matcher_accepts_valid_addresses(self, address):
        assert helpers.MAC_ADDRESS_MATCHER.match(address)

    @pytest.mark.parametrize(
        "address",
        ["DE:AD:BE:EF:00:01", "00:11:22:33:44", "00-11-22-33-44-55", "xx:11:22:33:44:55"],
    )
    def test_mac_matcher_rejects_invalid_addresses(self, address):
        assert not helpers.MAC_ADDRESS_MATCHER.match(address)
