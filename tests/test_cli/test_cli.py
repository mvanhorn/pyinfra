import json
from os import path
from tempfile import NamedTemporaryFile
from unittest import TestCase
from unittest.mock import patch

from pyinfra import state as global_state
from pyinfra.api import Config, Host, Inventory, State, StringCommand
from pyinfra.connectors.util import CommandOutput, OutputLine, make_unix_command_for_host
from pyinfra_cli.cli import (
    _apply_inventory_exclude,
    _apply_inventory_limit,
    _main,
    _prompt_for_sudo_passwords,
)

from ..paramiko_util import PatchSSHTestCase
from .util import run_cli


class TestCliEagerFlags(TestCase):
    def test_print_help(self):
        result = run_cli("--version")
        assert result.exit_code == 0, result.stderr

        result = run_cli("--help")
        assert result.exit_code == 0, result.stderr


class TestOperationCli(PatchSSHTestCase):
    def test_invalid_operation_module(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "not_a_module.shell",
        )
        assert result.exit_code == 1, result.stderr
        assert "No such module: not_a_module"

    def test_invalid_operation_function(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "server.not_an_operation",
        )
        assert result.exit_code == 1, result.stderr
        assert "No such operation: server.not_an_operation"

    def test_deploy_operation(self):
        result = run_cli(
            "-y",
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "server.shell",
            "echo hi",
        )
        assert result.exit_code == 0, result.stderr

    def test_deploy_operation_with_all(self):
        result = run_cli(
            "-y",
            path.join("tests", "test_cli", "deploy", "inventory_all.py"),
            "server.shell",
            "echo hi",
        )
        assert result.exit_code == 0, result.stderr

    def test_deploy_operation_json_args(self):
        result = run_cli(
            "-y",
            path.join("tests", "test_cli", "deploy", "inventory_all.py"),
            "server.shell",
            '[["echo hi"], {}]',
        )
        assert result.exit_code == 0, result.stderr


class TestFactCli(PatchSSHTestCase):
    def test_get_fact(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "fact",
            "server.Os",
        )
        assert result.exit_code == 0, result.stderr
        assert '"somehost": null' in result.stderr

    def test_get_fact_with_kwargs(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "fact",
            "files.File",
            "path=.",
        )
        assert result.exit_code == 0, result.stderr
        assert '"somehost": null' in result.stderr


class TestExecCli(PatchSSHTestCase):
    def test_exec_command(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "exec",
            "--",
            "echo hi",
        )
        assert result.exit_code == 0, result.stderr

    def test_exec_command_with_options(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "exec",
            "--sudo",
            "--sudo-user",
            "pyinfra",
            "--su-user",
            "pyinfrawhat",
            "--port",
            "1022",
            "--user",
            "ubuntu",
            "--",
            "echo hi",
        )
        assert result.exit_code == 0, result.stderr

    def test_exec_command_with_serial(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "exec",
            "--serial",
            "--",
            "echo hi",
        )
        assert result.exit_code == 0, result.stderr

    def test_exec_command_with_no_wait(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "exec",
            "--no-wait",
            "--",
            "echo hi",
        )
        assert result.exit_code == 0, result.stderr

    def test_exec_command_with_debug_operations(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "exec",
            "--debug-operations",
            "--",
            "echo hi",
        )
        assert result.exit_code == 0, result.stderr

    def test_exec_command_with_debug_facts(self):
        result = run_cli(
            path.join("tests", "test_cli", "deploy", "inventories", "inventory.py"),
            "exec",
            "--debug-facts",
            "--",
            "echo hi",
        )
        assert result.exit_code == 0, result.stderr


class TestSudoPasswordPrompt(PatchSSHTestCase):
    inventory = path.join("tests", "test_cli", "deploy", "inventories", "inventory.py")

    def test_use_sudo_password_is_a_copyable_config_default(self):
        assert Config().USE_SUDO_PASSWORD is False
        assert Config(USE_SUDO_PASSWORD=True).copy().USE_SUDO_PASSWORD is True

    def test_use_sudo_password_prompts_before_first_sudo_command(self):
        config = Config(USE_SUDO_PASSWORD=True)
        inventory = Inventory((["somehost"], {}))
        state = State(inventory, config)
        host = inventory.get_host("somehost")
        state.activate_host(host)

        with patch("pyinfra_cli.cli.getpass", return_value="host-password") as getpass:
            _prompt_for_sudo_passwords(state, config)

        getpass.assert_called_once_with(f"{host.print_prefix}sudo password: ")
        assert host.connector_data["prompted_sudo_password"] == "host-password"

        def run_shell_command(_command):
            return (
                True,
                CommandOutput([OutputLine("stdout", "/tmp/pyinfra-sudo-askpass")]),
            )

        host.run_shell_command = run_shell_command  # type: ignore[method-assign]
        command = make_unix_command_for_host(
            state,
            host,
            StringCommand("true"),
            _sudo=True,
        )
        assert "PYINFRA_SUDO_PASSWORD=host-password" in command.get_raw_value()
        assert "sudo -H -A -k" in command.get_raw_value()
        assert "sudo -H -n" not in command.get_raw_value()

    def test_config_use_sudo_password_prompts_for_each_active_host(self):
        with NamedTemporaryFile(mode="w", suffix=".py") as config_file:
            config_file.write("from pyinfra import config\nconfig.USE_SUDO_PASSWORD = True\n")
            config_file.flush()

            def get_password(prompt):
                return f"password for {prompt}"

            with patch("pyinfra_cli.cli.getpass", side_effect=get_password) as getpass:
                result = run_cli(
                    "-y",
                    "--dry",
                    "--config",
                    config_file.name,
                    self.inventory,
                    "exec",
                    "--",
                    "echo hi",
                )

        assert result.exit_code == 0, result.stderr
        assert getpass.call_count == 2
        for host in global_state.active_hosts:
            prompt = f"{host.print_prefix}sudo password: "
            assert host.connector_data["prompted_sudo_password"] == f"password for {prompt}"

    def test_cli_use_sudo_password_prompts_for_each_active_host(self):
        def get_password(prompt):
            return f"password for {prompt}"

        with patch("pyinfra_cli.cli.getpass", side_effect=get_password) as getpass:
            result = run_cli(
                "-y",
                "--dry",
                "--sudo",
                "--use-sudo-password",
                self.inventory,
                "exec",
                "--",
                "echo hi",
            )

        assert result.exit_code == 0, result.stderr
        assert getpass.call_count == 2
        for host in global_state.active_hosts:
            prompt = f"{host.print_prefix}sudo password: "
            assert host.connector_data["prompted_sudo_password"] == f"password for {prompt}"

    def test_same_sudo_password_skips_host_specific_prompts(self):
        with patch("pyinfra_cli.cli.getpass", return_value="shared-password") as getpass:
            result = run_cli(
                "-y",
                "--dry",
                "--use-sudo-password",
                "--same-sudo-password",
                self.inventory,
                "exec",
                "--",
                "echo hi",
            )

        assert result.exit_code == 0, result.stderr
        getpass.assert_called_once_with("sudo password: ")
        assert global_state.config.SUDO_PASSWORD == "shared-password"
        assert all(
            "prompted_sudo_password" not in host.connector_data
            for host in global_state.active_hosts
        )

    def test_disabled_eager_prompt_leaves_fallback_unprimed(self):
        config = Config()
        inventory = Inventory((["somehost"], {}))
        state = State(inventory, config)
        host = inventory.get_host("somehost")
        state.activate_host(host)

        with patch("pyinfra_cli.cli.getpass") as getpass:
            _prompt_for_sudo_passwords(state, config)

        getpass.assert_not_called()
        assert "prompted_sudo_password" not in host.connector_data


class TestJsonOutput(PatchSSHTestCase):
    inventory = path.join("tests", "test_cli", "deploy", "inventories", "inventory.py")

    def _parse_stdout(self, result):
        assert result.exit_code == 0, result.stderr
        assert result.stdout, "stdout must not be empty in --json mode"
        return json.loads(result.stdout)

    def test_json_debug_inventory(self):
        result = run_cli("--json", self.inventory, "debug-inventory")
        payload = self._parse_stdout(result)
        assert isinstance(payload, list)
        names = {host["name"] for host in payload}
        assert {"somehost", "anotherhost"} <= names
        for host in payload:
            assert set(host.keys()) == {"name", "groups", "data"}

    def test_json_fact(self):
        result = run_cli("--json", self.inventory, "fact", "server.Os")
        payload = self._parse_stdout(result)
        assert "server.Os" in payload
        assert "somehost" in payload["server.Os"]

    def test_json_dry_run(self):
        result = run_cli("--json", "--dry", self.inventory, "exec", "--", "echo hi")
        payload = self._parse_stdout(result)
        assert "plan" in payload
        assert payload["results"] is None
        assert isinstance(payload["plan"], list)
        assert payload["plan"]
        first_op = payload["plan"][0]
        assert first_op["names"] == ["server.shell"]
        assert "op_hash" in first_op

    def test_json_deploy_results(self):
        result = run_cli("-y", "--json", self.inventory, "exec", "--", "echo hi")
        payload = self._parse_stdout(result)
        assert "plan" in payload
        assert payload["results"] is not None
        results = payload["results"]
        assert set(results.keys()) == {"operations", "totals", "failed_hosts"}
        assert results["totals"]["hosts"] >= 1
        assert results["failed_hosts"] == []

    def test_json_debug_operations(self):
        result = run_cli(
            "--json",
            "--debug-operations",
            self.inventory,
            "exec",
            "--",
            "echo hi",
        )
        payload = self._parse_stdout(result)
        assert set(payload.keys()) == {"operations", "op_meta", "op_order"}
        assert isinstance(payload["op_order"], list)
        assert payload["op_order"]

    def test_json_deploy_without_yes_does_not_apply(self):
        # Regression for pyinfra-dev/pyinfra#1662 review: --json must not
        # imply --yes. A diffable operation run with --json and no --yes
        # prints the proposed changes and exits without mutating the host.
        result = run_cli("--json", self.inventory, "server.shell", "echo hi")
        payload = self._parse_stdout(result)
        assert payload["results"] is None
        assert isinstance(payload["plan"], list)
        assert payload["plan"]

    def test_json_deploy_with_yes_applies(self):
        result = run_cli("-y", "--json", self.inventory, "server.shell", "echo hi")
        payload = self._parse_stdout(result)
        assert payload["results"] is not None
        assert set(payload["results"].keys()) == {"operations", "totals", "failed_hosts"}

    def test_json_deploy_with_exclude(self):
        result = run_cli(
            "-y",
            "--json",
            "--exclude",
            "somehost",
            self.inventory,
            "exec",
            "--",
            "echo hi",
        )
        payload = self._parse_stdout(result)
        operation = payload["results"]["operations"][0]
        assert operation["hosts"] == 1
        assert operation["success"] == ["anotherhost"]


class TestCliLimitExclude(TestCase):
    def setUp(self):
        self.inventory = Inventory(
            (
                [
                    "app-1.net",
                    "app-2.net",
                    "db-1.net",
                    "db-2.net",
                ],
                {},
            ),
            app_servers=(["app-1.net", "app-2.net"], {}),
            db_servers=(["db-1.net", "db-2.net"], {}),
        )

    def _host_names(self, hosts: list[Host] | None) -> set[str]:
        assert hosts is not None
        return {host.name for host in hosts}

    def test_apply_inventory_exclude_host(self):
        initial_limit = _apply_inventory_limit(self.inventory, None)
        limited_hosts = _apply_inventory_exclude(
            self.inventory,
            initial_limit,
            ("app-2.net",),
        )

        assert self._host_names(limited_hosts) == {
            "app-1.net",
            "db-1.net",
            "db-2.net",
        }

    def test_apply_inventory_exclude_glob(self):
        initial_limit = _apply_inventory_limit(self.inventory, None)
        limited_hosts = _apply_inventory_exclude(self.inventory, initial_limit, ("db*",))

        assert self._host_names(limited_hosts) == {"app-1.net", "app-2.net"}

    def test_apply_inventory_exclude_group(self):
        initial_limit = _apply_inventory_limit(self.inventory, None)
        limited_hosts = _apply_inventory_exclude(
            self.inventory,
            initial_limit,
            ("db_servers",),
        )

        assert self._host_names(limited_hosts) == {"app-1.net", "app-2.net"}

    def test_apply_inventory_limit_then_exclude(self):
        initial_limit = _apply_inventory_limit(self.inventory, ("app_servers",))
        limited_hosts = _apply_inventory_exclude(
            self.inventory,
            initial_limit,
            ("app-2.net",),
        )

        assert self._host_names(limited_hosts) == {"app-1.net"}

    def test_apply_inventory_exclude_no_match_warns(self):
        initial_limit = _apply_inventory_limit(self.inventory, None)

        with self.assertLogs("pyinfra", level="WARNING") as logs:
            limited_hosts = _apply_inventory_exclude(
                self.inventory,
                initial_limit,
                ("missing-host",),
            )

        assert self._host_names(limited_hosts) == {
            "app-1.net",
            "app-2.net",
            "db-1.net",
            "db-2.net",
        }
        assert "No host matches found for --exclude pattern: missing-host" in "\n".join(logs.output)

    def test_apply_inventory_exclude_all_hosts(self):
        initial_limit = _apply_inventory_limit(self.inventory, None)
        limited_hosts = _apply_inventory_exclude(self.inventory, initial_limit, ("*",))

        assert limited_hosts == []


class TestDirectMainExecution(PatchSSHTestCase):
    """
    These tests are very similar as above, without the click wrappers - basically
    here because coverage.py fails to properly detect all the code under the wrapper.
    """

    def test_deploy_operation_direct(self):
        with self.assertRaises(SystemExit) as e:
            _main(
                inventory=path.join("tests", "test_deploy", "inventories", "inventory.py"),
                operations=["server.shell", "echo hi"],
                chdir=None,
                group_data=None,
                verbosity=0,
                ssh_user=None,
                ssh_port=None,
                ssh_key=None,
                ssh_key_password=None,
                ssh_password=None,
                ssh_password_prompt=False,
                same_sudo_password=False,
                sudo=False,
                sudo_user=None,
                use_sudo_password=False,
                use_sudo_login=False,
                su_user=None,
                dzdo=False,
                dzdo_user=None,
                parallel=None,
                fail_percent=0,
                dry=False,
                yes=True,
                limit=None,
                exclude=tuple(),
                no_wait=False,
                serial=False,
                shell_executable=None,
                data=tuple(),
                debug=False,
                debug_facts=False,
                debug_all=False,
                debug_operations=False,
                config_filename="config.py",
                diff=True,
                retry=0,
                retry_delay=5,
            )
            assert e.args == (0,)
