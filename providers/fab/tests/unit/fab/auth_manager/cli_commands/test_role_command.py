#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import json
from contextlib import redirect_stdout
from importlib import reload
from io import StringIO
from typing import TYPE_CHECKING

import pytest

from airflow.cli import cli_parser

from tests_common.test_utils.compat import ignore_provider_compatibility_error
from tests_common.test_utils.config import conf_vars

with ignore_provider_compatibility_error("2.9.0+", __file__):
    from airflow.providers.fab.auth_manager.cli_commands import role_command
    from airflow.providers.fab.auth_manager.cli_commands.utils import get_application_builder

from airflow.providers.fab.www.security import permissions

pytestmark = pytest.mark.db_test


if TYPE_CHECKING:
    from airflow.providers.fab.auth_manager.models import Role

TEST_USER1_EMAIL = "test-user1@example.com"
TEST_USER2_EMAIL = "test-user2@example.com"


class TestCliRoles:
    @pytest.fixture(autouse=True)
    def _set_attrs(self):
        with conf_vars(
            {
                (
                    "core",
                    "auth_manager",
                ): "airflow.providers.fab.auth_manager.fab_auth_manager.FabAuthManager",
            }
        ):
            # Reload the module to use FAB auth manager
            reload(cli_parser)
            # Clearing the cache before calling it
            cli_parser.get_parser.cache_clear()
            self.parser = cli_parser.get_parser()
            with conf_vars({("fab", "UPDATE_FAB_PERMS"): "False"}):
                with get_application_builder() as appbuilder:
                    self.appbuilder = appbuilder
                    self.clear_users_and_roles()
                    yield
                    self.clear_users_and_roles()

    def clear_users_and_roles(self):
        session = self.appbuilder.get_session
        for user in self.appbuilder.sm.get_all_users():
            session.delete(user)
        for role_name in ["FakeTeamA", "FakeTeamB", "FakeTeamC"]:
            if self.appbuilder.sm.find_role(role_name):
                self.appbuilder.sm.delete_role(role_name)
        session.commit()

    def test_cli_create_roles(self):
        assert self.appbuilder.sm.find_role("FakeTeamA") is None
        assert self.appbuilder.sm.find_role("FakeTeamB") is None

        args = self.parser.parse_args(["roles", "create", "FakeTeamA", "FakeTeamB"])
        role_command.roles_create(args)

        assert self.appbuilder.sm.find_role("FakeTeamA") is not None
        assert self.appbuilder.sm.find_role("FakeTeamB") is not None

    def test_cli_delete_roles(self):
        assert self.appbuilder.sm.find_role("FakeTeamA") is None
        assert self.appbuilder.sm.find_role("FakeTeamB") is None
        assert self.appbuilder.sm.find_role("FakeTeamC") is None

        self.appbuilder.sm.add_role("FakeTeamA")
        self.appbuilder.sm.add_role("FakeTeamB")
        self.appbuilder.sm.add_role("FakeTeamC")

        args = self.parser.parse_args(["roles", "delete", "FakeTeamA", "FakeTeamC"])
        role_command.roles_delete(args)

        assert self.appbuilder.sm.find_role("FakeTeamA") is None
        assert self.appbuilder.sm.find_role("FakeTeamB") is not None
        assert self.appbuilder.sm.find_role("FakeTeamC") is None

    def test_cli_create_roles_is_reentrant(self):
        assert self.appbuilder.sm.find_role("FakeTeamA") is None
        assert self.appbuilder.sm.find_role("FakeTeamB") is None

        args = self.parser.parse_args(["roles", "create", "FakeTeamA", "FakeTeamB"])

        role_command.roles_create(args)

        assert self.appbuilder.sm.find_role("FakeTeamA") is not None
        assert self.appbuilder.sm.find_role("FakeTeamB") is not None

    def test_cli_list_roles(self):
        self.appbuilder.sm.add_role("FakeTeamA")
        self.appbuilder.sm.add_role("FakeTeamB")

        with redirect_stdout(StringIO()) as stdout:
            role_command.roles_list(self.parser.parse_args(["roles", "list"]))
            stdout = stdout.getvalue()

        assert "FakeTeamA" in stdout
        assert "FakeTeamB" in stdout

    def test_cli_list_roles_with_args(self):
        role_command.roles_list(self.parser.parse_args(["roles", "list", "--output", "yaml"]))
        role_command.roles_list(self.parser.parse_args(["roles", "list", "-p", "--output", "yaml"]))

    def test_cli_roles_add_and_del_perms(self):
        assert self.appbuilder.sm.find_role("FakeTeamC") is None

        role_command.roles_create(self.parser.parse_args(["roles", "create", "FakeTeamC"]))
        assert self.appbuilder.sm.find_role("FakeTeamC") is not None
        role: Role = self.appbuilder.sm.find_role("FakeTeamC")
        assert len(role.permissions) == 0

        role_command.roles_add_perms(
            self.parser.parse_args(
                [
                    "roles",
                    "add-perms",
                    "FakeTeamC",
                    "-r",
                    permissions.RESOURCE_POOL,
                    "-a",
                    permissions.ACTION_CAN_EDIT,
                ]
            )
        )
        role: Role = self.appbuilder.sm.find_role("FakeTeamC")
        assert len(role.permissions) == 1
        assert role.permissions[0].resource.name == permissions.RESOURCE_POOL
        assert role.permissions[0].action.name == permissions.ACTION_CAN_EDIT

        role_command.roles_del_perms(
            self.parser.parse_args(
                [
                    "roles",
                    "del-perms",
                    "FakeTeamC",
                    "-r",
                    permissions.RESOURCE_POOL,
                    "-a",
                    permissions.ACTION_CAN_EDIT,
                ]
            )
        )
        role: Role = self.appbuilder.sm.find_role("FakeTeamC")
        assert len(role.permissions) == 0

    def test_cli_export_roles(self, tmp_path):
        fn = tmp_path / "export_roles.json"
        fn.touch()
        args = self.parser.parse_args(["roles", "create", "FakeTeamA", "FakeTeamB"])
        role_command.roles_create(args)
        role_command.roles_add_perms(
            self.parser.parse_args(
                [
                    "roles",
                    "add-perms",
                    "FakeTeamA",
                    "-r",
                    permissions.RESOURCE_POOL,
                    "-a",
                    permissions.ACTION_CAN_EDIT,
                    permissions.ACTION_CAN_READ,
                ]
            )
        )
        role_command.roles_export(self.parser.parse_args(["roles", "export", str(fn)]))
        with open(fn) as outfile:
            roles_exported = json.load(outfile)
        assert {"name": "FakeTeamA", "resource": "Pools", "action": "can_edit,can_read"} in roles_exported
        assert {"name": "FakeTeamB", "resource": "", "action": ""} in roles_exported

    def test_cli_import_roles(self, tmp_path):
        fn = tmp_path / "import_roles.json"
        fn.touch()
        roles_list = [
            {"name": "FakeTeamA", "resource": "Pools", "action": "can_edit,can_read"},
            {"name": "FakeTeamA", "resource": "Admin", "action": "menu_access"},
            {"name": "FakeTeamB", "resource": "", "action": ""},
        ]
        with open(fn, "w") as outfile:
            json.dump(roles_list, outfile)
        role_command.roles_import(self.parser.parse_args(["roles", "import", str(fn)]))
        fakeTeamA: Role = self.appbuilder.sm.find_role("FakeTeamA")
        fakeTeamB: Role = self.appbuilder.sm.find_role("FakeTeamB")

        assert fakeTeamA is not None
        assert fakeTeamB is not None
        assert len(fakeTeamB.permissions) == 0
        assert len(fakeTeamA.permissions) == 3
        assert any(
            permission.resource.name == permissions.RESOURCE_POOL
            and permission.action.name == permissions.ACTION_CAN_EDIT
            for permission in fakeTeamA.permissions
        )
        assert any(
            permission.resource.name == permissions.RESOURCE_POOL
            and permission.action.name == permissions.ACTION_CAN_READ
            for permission in fakeTeamA.permissions
        )
        assert any(
            permission.resource.name == permissions.RESOURCE_ADMIN_MENU
            and permission.action.name == permissions.ACTION_CAN_ACCESS_MENU
            for permission in fakeTeamA.permissions
        )
