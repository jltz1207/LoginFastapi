"""Tenant scoping of the auth layer and the endpoints that take client-supplied ids.

No DB: a fake AsyncSession records every statement and returns canned results, so the
tests check *which filters are sent* (the tenant boundary) rather than query results.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints import users as users_endpoint
from app.models.tenant import TenantStatus
from app.schemas.users import UserCreate, UserLogin, UserUpdate
from app.services.jwtService import ensureTenantActive, getCurrentUser, jwtService
from app.services.knowledgeBaseService import get_owned_knowledge_base


class _Scalars:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value[0] if isinstance(self._value, list) else self._value

    def all(self):
        return self._value if isinstance(self._value, list) else [self._value]


class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _Scalars(self._value)

    def first(self):
        return _Scalars(self._value).first()


class FakeSession:
    def __init__(self, *results, commit_error: Exception | None = None):
        self._results = list(results)
        self.statements = []
        self.added = []
        self.rolled_back = False
        self._commit_error = commit_error

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self._commit_error:
            raise self._commit_error

    async def rollback(self):
        self.rolled_back = True


def _where(stmt) -> tuple[str, list]:
    compiled = stmt.compile()
    return str(stmt.whereclause), list(compiled.params.values())


def _user(**overrides):
    fields = {"id": uuid.uuid4(), "tenant_id": uuid.uuid4(), "email": "a@example.com", "username": "alice"}
    return SimpleNamespace(**(fields | overrides))


def _raises(status_code: int, coro):
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(coro)
    assert exc_info.value.status_code == status_code


# --- get_owned_knowledge_base -------------------------------------------------------


def test_owned_kb_is_filtered_by_id_tenant_and_user():
    user, kb = _user(), SimpleNamespace(id=uuid.uuid4())
    db = FakeSession(kb)

    assert asyncio.run(get_owned_knowledge_base(db, str(kb.id), user)) is kb
    where, params = _where(db.statements[0])
    assert "tenant_id" in where and "user_id" in where and "deleted_at IS NULL" in where
    assert {kb.id, user.tenant_id, user.id} <= set(params)


def test_someone_elses_kb_is_404():
    _raises(404, get_owned_knowledge_base(FakeSession(None), uuid.uuid4(), _user()))


def test_malformed_kb_id_is_404_without_querying():
    db = FakeSession()
    _raises(404, get_owned_knowledge_base(db, "not-a-uuid", _user()))
    assert db.statements == []


# --- tenant status ------------------------------------------------------------------


def test_inactive_tenant_is_403():
    db = FakeSession(None)
    _raises(403, ensureTenantActive(db, uuid.uuid4()))
    where, params = _where(db.statements[0])
    assert "status" in where and "deleted_at IS NULL" in where
    assert TenantStatus.ACTIVE in params or TenantStatus.ACTIVE.value in params


def test_active_tenant_passes():
    asyncio.run(ensureTenantActive(FakeSession((uuid.uuid4(),)), uuid.uuid4()))


def test_token_stops_working_once_tenant_is_suspended():
    user = _user()
    token = jwtService.createToken({"email": user.email, "username": user.username})
    # 1st query loads the user, 2nd finds no ACTIVE tenant.
    _raises(403, getCurrentUser(token=token, db=FakeSession(user, None)))


# --- users endpoints ----------------------------------------------------------------


def test_list_users_is_scoped_to_caller_tenant():
    caller = _user()
    db = FakeSession([caller])

    asyncio.run(users_endpoint.get_users(db=db, currUser=caller))
    where, params = _where(db.statements[0])
    assert "tenant_id" in where and caller.tenant_id in params


def test_get_user_is_scoped_to_caller_tenant():
    caller = _user()
    db = FakeSession(None)  # user exists in another tenant -> filtered out

    _raises(404, users_endpoint.get_user(user_id=uuid.uuid4(), db=db, currUser=caller))
    assert caller.tenant_id in _where(db.statements[0])[1]


@pytest.mark.parametrize("handler", [
    users_endpoint.update_user,
    users_endpoint.change_email,
    users_endpoint.change_password,
])
def test_cannot_modify_another_user(handler):
    db = FakeSession()
    other = UserUpdate(id=uuid.uuid4(), email="b@example.com", username="b", first_name="b", last_name="b", password="x")

    _raises(403, handler(user=other, db=db, currUser=_user()))
    assert db.statements == []


def test_update_rejects_username_taken_in_same_tenant():
    caller = _user(first_name="a", last_name="a")
    db = FakeSession(caller, (uuid.uuid4(),))
    update = UserUpdate(id=caller.id, username="taken", first_name="a", last_name="a")

    _raises(400, users_endpoint.update_user(user=update, db=db, currUser=caller))
    assert caller.tenant_id in _where(db.statements[1])[1]


# --- login / register ---------------------------------------------------------------


def test_username_login_requires_tenant_slug():
    db = FakeSession()
    _raises(400, users_endpoint.login(UserLogin(username="alice", password="x"), db=db))
    assert db.statements == []


def test_username_login_is_scoped_by_tenant_slug():
    db = FakeSession(None)
    _raises(404, users_endpoint.login(UserLogin(username="alice", tenant_slug="acme", password="x"), db=db))
    stmt = db.statements[0]
    assert "tenants" in str(stmt) and "acme" in _where(stmt)[1]


def _register_body(**overrides):
    fields = {"email": "a@example.com", "username": "alice", "password": "pw", "tenant_slug": "acme"}
    return UserCreate(**(fields | overrides))


def test_register_rejects_unknown_or_inactive_tenant():
    db = FakeSession(None, None)  # email free, tenant not found
    _raises(400, users_endpoint.register(_register_body(), db=db))
    where, params = _where(db.statements[1])
    assert "acme" in params and "status" in where


def test_register_rejects_username_taken_in_tenant():
    tenant = SimpleNamespace(id=uuid.uuid4())
    db = FakeSession(None, tenant, (uuid.uuid4(),))
    _raises(400, users_endpoint.register(_register_body(), db=db))
    assert tenant.id in _where(db.statements[2])[1]


def test_register_race_is_caught_by_unique_constraint():
    tenant = SimpleNamespace(id=uuid.uuid4())
    db = FakeSession(None, tenant, None, commit_error=IntegrityError("INSERT", {}, Exception("dup")))

    _raises(400, users_endpoint.register(_register_body(), db=db))
    assert db.rolled_back
    assert db.added[0].tenant_id == tenant.id
