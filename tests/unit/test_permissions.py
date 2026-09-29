"""権限表（docs/IMPLEMENTATION.md 3.5）をそのままテストにする。"""

import pytest

from hatch.domain.permissions import Actor, ServerRef, can

ADMIN = Actor(id="a", role="admin")
OWNER = Actor(id="o", role="user")
OTHER = Actor(id="x", role="user")


def srv(status="running", shares=None):
    return ServerRef(owner_id="o", status=status, shares=shares or {})


# (action, admin, owner, view, console, files, full)
TABLE = [
    ("server.view", 1, 1, 1, 1, 1, 1),
    ("server.power", 1, 1, 0, 1, 1, 1),
    ("server.backup", 1, 1, 0, 0, 1, 1),
    ("server.restore", 1, 1, 0, 0, 0, 1),
    ("server.plugins", 1, 1, 0, 0, 1, 1),
    ("server.settings", 1, 1, 0, 0, 0, 0),
    ("server.share", 1, 1, 0, 0, 0, 0),
    ("server.domain", 1, 1, 0, 0, 0, 0),
    ("server.trash", 1, 1, 0, 0, 0, 0),
    ("server.dev_copy", 1, 1, 0, 0, 0, 0),
    ("server.extend", 1, 1, 0, 0, 0, 0),
]


@pytest.mark.parametrize("row", TABLE, ids=[r[0] for r in TABLE])
def test_table(row):
    action, *expect = row
    assert can(ADMIN, action, srv()) == bool(expect[0])
    assert can(OWNER, action, srv()) == bool(expect[1])
    for i, perm in enumerate(["view", "console", "files", "full"]):
        assert can(OTHER, action, srv(shares={"x": perm})) == bool(expect[2 + i]), (action, perm)
    assert can(OTHER, action, srv()) is False


def test_admin_actions():
    assert can(ADMIN, "admin.domains") is True
    assert can(OWNER, "admin.domains") is False


def test_suspended_only_view_for_non_admin():
    s = srv(status="suspended", shares={"x": "full"})
    assert can(OWNER, "server.view", s) is True
    assert can(OWNER, "server.power", s) is False
    assert can(OTHER, "server.backup", s) is False
    assert can(ADMIN, "server.power", s) is True


def test_deleting_account_is_read_only():
    me = Actor(id="o", role="user", deleting=True)
    assert can(me, "server.view", srv()) is True
    assert can(me, "server.power", srv()) is False
    assert can(me, "server.create") is False


def test_unknown_action_is_denied():
    with pytest.raises(ValueError):
        can(ADMIN, "server.fly", srv())
