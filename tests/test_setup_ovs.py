# Copyright (C) 2025, RTE (http://www.rte-france.com)
# SPDX-License-Identifier: Apache-2.0

import json
import logging

import pytest

from setup_ovs import helpers
from setup_ovs.setup_ovs import main
from setup_ovs.setup_ovs_exception import SetupOVSConfigException


CONFIG = {
    "bridges": [
        {"name": "br0", "ports": [{"name": "tap0", "type": "tap"}]}
    ]
}


@pytest.fixture
def steps(monkeypatch):
    """
    Replace every step main() drives, and record the order they run in.

    Patching the names inside setup_ovs.setup_ovs is what the module
    actually calls, so this covers the argument dispatch without touching
    OVS.
    """
    import setup_ovs.setup_ovs as cli

    called = []

    def recorder(name, result=None):
        def step(*args, **kwargs):
            called.append(name)
            return result

        return step

    monkeypatch.setattr(cli.check, "configuration_check", recorder("check_config"))
    monkeypatch.setattr(cli.check, "system_check", recorder("system_check"))
    monkeypatch.setattr(cli.ovs, "clear_ovs", recorder("clear_ovs"))
    monkeypatch.setattr(cli.ovs, "clear_tap", recorder("clear_tap"))
    monkeypatch.setattr(cli.ovs, "unbind_pci", recorder("unbind_pci"))
    monkeypatch.setattr(cli.ovs, "setup_ovs", recorder("setup_ovs"))
    monkeypatch.setattr(cli.openflow, "SetupOpenFlow", recorder("openflow"))
    return called


@pytest.fixture
def config_file(tmp_path):
    def write(content, name="ovs_configuration.json"):
        path = tmp_path / name
        path.write_text(
            content if isinstance(content, str) else json.dumps(content)
        )
        return str(path)

    return write


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["setup_ovs"] + list(argv))
    main()


class TestConfigurationLoading:
    def test_reads_a_json_file(self, monkeypatch, steps, config_file):
        run_cli(monkeypatch, "-f", config_file(CONFIG))

        assert "check_config" in steps

    def test_reads_a_yaml_file(self, monkeypatch, steps, config_file):
        path = config_file("bridges:\n  - name: br0\n", name="conf.yaml")

        run_cli(monkeypatch, "-f", path)

        assert "check_config" in steps

    def test_reads_a_yml_file(self, monkeypatch, steps, config_file):
        path = config_file("bridges:\n  - name: br0\n", name="conf.yml")

        run_cli(monkeypatch, "-f", path)

        assert "check_config" in steps

    def test_passes_the_parsed_configuration_along(
        self, monkeypatch, config_file
    ):
        import setup_ovs.setup_ovs as cli

        seen = {}
        monkeypatch.setattr(
            cli.check,
            "configuration_check",
            lambda config: seen.update(config=config),
        )

        run_cli(monkeypatch, "-c", "-f", config_file(CONFIG))

        assert seen["config"] == CONFIG

    def test_exits_when_the_file_is_missing(self, monkeypatch, steps, tmp_path):
        absent = str(tmp_path / "absent.json")

        with pytest.raises(SystemExit):
            run_cli(monkeypatch, "-f", absent)

        assert steps == []

    def test_warns_when_the_file_is_missing(
        self, monkeypatch, steps, tmp_path, caplog
    ):
        absent = str(tmp_path / "absent.json")

        with pytest.raises(SystemExit):
            run_cli(monkeypatch, "-f", absent)

        assert "not found" in caplog.text

    def test_propagates_invalid_json(self, monkeypatch, steps, config_file):
        path = config_file("{not json", name="broken.json")

        with pytest.raises(json.JSONDecodeError):
            run_cli(monkeypatch, "-f", path)

    def test_propagates_a_configuration_error(self, monkeypatch, config_file):
        import setup_ovs.setup_ovs as cli

        def boom(config):
            raise SetupOVSConfigException("bad config")

        monkeypatch.setattr(cli.check, "configuration_check", boom)
        path = config_file(CONFIG)

        with pytest.raises(SetupOVSConfigException):
            run_cli(monkeypatch, "-f", path)


class TestStepDispatch:
    def test_default_run_executes_every_step(
        self, monkeypatch, steps, config_file
    ):
        run_cli(monkeypatch, "-f", config_file(CONFIG))

        assert steps == [
            "check_config",
            "system_check",
            "clear_ovs",
            "clear_tap",
            "unbind_pci",
            "setup_ovs",
            "openflow",
        ]

    def test_check_only_stops_after_the_configuration_check(
        self, monkeypatch, steps, config_file
    ):
        run_cli(monkeypatch, "-c", "-f", config_file(CONFIG))

        assert steps == ["check_config"]

    @pytest.mark.parametrize(
        "flag,skipped",
        [
            ("--no-remove-bridges", "clear_ovs"),
            ("--no-remove-interfaces", "clear_tap"),
            ("--no-unbind", "unbind_pci"),
            ("--no-ovs", "setup_ovs"),
            ("--no-openflow", "openflow"),
        ],
    )
    def test_each_no_flag_skips_its_step(
        self, monkeypatch, steps, config_file, flag, skipped
    ):
        run_cli(monkeypatch, flag, "-f", config_file(CONFIG))

        assert skipped not in steps
        assert "check_config" in steps

    def test_flags_combine(self, monkeypatch, steps, config_file):
        run_cli(
            monkeypatch,
            "--no-remove-bridges",
            "--no-remove-interfaces",
            "--no-unbind",
            "--no-openflow",
            "-f",
            config_file(CONFIG),
        )

        assert steps == ["check_config", "system_check", "setup_ovs"]


class TestFlagsAndLogging:
    @pytest.fixture
    def basic_config_level(self, monkeypatch):
        """
        Capture the level main() asks logging.basicConfig for.

        Asserting on the root logger's effective level would not work here:
        basicConfig is a no-op once handlers exist, and pytest installs its
        own before the test runs.
        """
        levels = []
        monkeypatch.setattr(
            logging, "basicConfig", lambda **kwargs: levels.append(kwargs["level"])
        )
        return levels

    def test_verbose_enables_debug_logging(
        self, monkeypatch, steps, config_file, basic_config_level
    ):
        run_cli(monkeypatch, "-v", "-f", config_file(CONFIG))

        assert basic_config_level == [logging.DEBUG]

    def test_default_logging_is_warning(
        self, monkeypatch, steps, config_file, basic_config_level
    ):
        run_cli(monkeypatch, "-f", config_file(CONFIG))

        assert basic_config_level == [logging.WARNING]

    def test_dry_run_sets_the_helpers_flag(
        self, monkeypatch, steps, config_file
    ):
        run_cli(monkeypatch, "-d", "-f", config_file(CONFIG))

        assert helpers.dry_run is True

    def test_dry_run_is_off_by_default(
        self, monkeypatch, steps, config_file
    ):
        run_cli(monkeypatch, "-f", config_file(CONFIG))

        assert helpers.dry_run is False

    def test_default_configuration_path(self, monkeypatch, steps):
        """Without -f the CLI looks at /etc/ovs_configuration.json."""
        import setup_ovs.setup_ovs as cli

        seen = {}
        monkeypatch.setattr(
            cli.os.path,
            "isfile",
            lambda path: seen.setdefault("path", path) and False,
        )

        with pytest.raises(SystemExit):
            run_cli(monkeypatch)

        assert seen["path"] == "/etc/ovs_configuration.json"

    def test_rejects_an_unknown_flag(self, monkeypatch, steps):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, "--no-such-option")
