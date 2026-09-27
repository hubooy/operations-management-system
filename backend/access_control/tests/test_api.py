from __future__ import annotations

import json
from unittest.mock import patch
from django.test import TestCase

from access_control.models import (
    AccessControlDataRevision,
    AppUser,
    PermissionAuditEvent,
)
from sales.tests.factories import TEST_SECRET, signed_headers


ADMIN_EMAIL = "dengweizhang321@gmail.com"


class AccessControlApiTests(TestCase):
    def setUp(self) -> None:
        AccessControlDataRevision.objects.filter(domain="access-control").update(
            revision=1, source_digest="a" * 64
        )

    def post_json(self, url: str, payload: dict[str, object], request_id: str, *, role: str = "admin", email: str = ADMIN_EMAIL):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return self.client.post(
            url, data=body, content_type="application/json; charset=utf-8",
            headers=signed_headers(url, method="POST", body=body, request_id=request_id, role=role, email=email),
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_reserved_local_actor_requires_signature_and_cannot_be_registered(self) -> None:
        path = "/api/access-control/users"
        self.assertEqual(self.client.get(path).status_code, 401)
        signed = self.client.get(path, headers=signed_headers(path, email="local-admin@teruisi.local"))
        self.assertEqual(signed.status_code, 200, signed.content)
        payload = {"email": "local-admin@teruisi.local", "displayName": "保留身份", "role": "admin", "status": "active", "scope": None, "reason": "不允许登记"}
        self.assertEqual(self.post_json(path, payload, "reserved-actor").status_code, 400)
        resolved = self.post_json("/api/access-control/principal/resolve", {"email": payload["email"], "displayName": "x"}, "reserved-resolve", email=payload["email"], role="viewer")
        self.assertEqual(resolved.status_code, 403)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_revocation_during_mutex_wait_rejects_write(self) -> None:
        actor = AppUser.objects.create(email="manager@example.test", display_name="Manager", role_id="admin", status="active", scope=None, version=1, created_at="2026-09-05T00:00:00Z", updated_at="2026-09-05T00:00:00Z")
        def revoke(_request_id):
            AppUser.objects.filter(email=actor.email).update(status="disabled")
        with patch("access_control.views._lock_request_id", side_effect=revoke):
            response = self.post_json("/api/access-control/users", {"email": "new@example.test", "displayName": "New", "role": "viewer", "status": "active", "scope": None, "reason": "test"}, "revoked-write", email=actor.email)
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(AppUser.objects.filter(email="new@example.test").exists())

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_identity_resolution_uses_postgres_role_and_rejects_unknown_user(self) -> None:
        payload = {"email": "dengweizhang321@gmail.com", "displayName": "外部名称"}
        response = self.post_json(
            "/api/access-control/principal/resolve", payload, "access-resolve-1", role="viewer", email=payload["email"]
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["user"]["role"], "admin")
        self.assertEqual(response.json()["user"]["displayName"], "系统管理员")
        self.assertTrue(response["X-Access-Control-Revision"].startswith("1:"))

        denied = self.post_json(
            "/api/access-control/principal/resolve",
            {"email": "unknown@example.com", "displayName": "未知"},
            "access-resolve-2", role="viewer", email="unknown@example.com",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(AppUser.objects.filter(email="unknown@example.com").exists())

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_admin_create_update_audit_and_replay_fencing(self) -> None:
        create_payload = {
            "email": "analyst@example.com", "displayName": "分析员甲", "role": "analyst",
            "status": "active", "scope": {"warehouses": ["主仓"], "channels": ["京东-测试店"], "platforms": ["京东"]},
            "reason": "入职授权",
        }
        created = self.post_json("/api/access-control/users", create_payload, "access-create-1")
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["user"]["version"], 1)
        self.assertEqual(PermissionAuditEvent.objects.filter(action="user_created").count(), 1)
        replay = self.post_json("/api/access-control/users", create_payload, "access-create-1")
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(AppUser.objects.filter(email="analyst@example.com").count(), 1)

        path = "/api/access-control/users"
        update_payload = {
            "email": "analyst@example.com", "displayName": "分析员甲", "role": "operator", "status": "active",
            "scope": None, "expectedVersion": 1, "reason": "岗位调整",
        }
        body = json.dumps(update_payload, ensure_ascii=False, separators=(",", ":")).encode()
        updated = self.client.put(
            path, data=body, content_type="application/json; charset=utf-8",
            headers=signed_headers(path, method="PUT", body=body, request_id="access-update-1", email=ADMIN_EMAIL),
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["user"]["role"], "operator")
        self.assertEqual(updated.json()["user"]["version"], 2)
        stale = self.client.put(
            path, data=body, content_type="application/json; charset=utf-8",
            headers=signed_headers(path, method="PUT", body=body, request_id="access-update-2", email=ADMIN_EMAIL),
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "version_conflict")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_restricted_or_non_admin_cannot_manage_and_bootstrap_admin_is_protected(self) -> None:
        for email, role, scope in (
            ("restricted-admin@example.test", "admin", {"warehouses": [], "channels": [], "platforms": []}),
            ("viewer@example.test", "viewer", None),
        ):
            AppUser.objects.create(
                email=email, display_name=email, role_id=role, status="active", scope=scope,
                version=1, created_at="2026-09-05T00:00:00Z", updated_at="2026-09-05T00:00:00Z",
            )
        restricted = self.client.get(
            "/api/access-control/users",
            headers=signed_headers(
                "/api/access-control/users", role="admin", email="restricted-admin@example.test",
                scope={"warehouses": [], "channels": [], "platforms": []},
            ),
        )
        self.assertEqual(restricted.status_code, 403)
        viewer = self.client.get(
            "/api/access-control/users",
            headers=signed_headers("/api/access-control/users", role="viewer", email="viewer@example.test"),
        )
        self.assertEqual(viewer.status_code, 403)

        path = "/api/access-control/users"
        payload = {"email": "dengweizhang321@gmail.com", "displayName": "系统管理员", "role": "viewer", "status": "active", "scope": None, "expectedVersion": 1, "reason": "错误变更"}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        response = self.client.put(
            path, data=body, content_type="application/json",
            headers=signed_headers(path, method="PUT", body=body, request_id="access-bootstrap-denied", email=ADMIN_EMAIL),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(AppUser.objects.get(email="dengweizhang321@gmail.com").role_id, "admin")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_structured_display_name_and_reason_are_rejected_as_client_errors(self) -> None:
        malformed_name = self.post_json(
            "/api/access-control/users",
            {
                "email": "bad-name@example.com", "displayName": {"name": "bad"},
                "role": "viewer", "status": "active", "scope": None,
                "reason": "invalid structured name",
            },
            "access-invalid-name",
        )
        self.assertEqual(malformed_name.status_code, 400)
        self.assertEqual(malformed_name.json()["code"], "invalid_request")

        malformed_reason = self.post_json(
            "/api/access-control/users",
            {
                "email": "bad-reason@example.com", "displayName": "Bad Reason",
                "role": "viewer", "status": "active", "scope": None,
                "reason": ["not", "text"],
            },
            "access-invalid-reason",
        )
        self.assertEqual(malformed_reason.status_code, 400)
        self.assertEqual(malformed_reason.json()["code"], "invalid_request")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_background_authorization_fails_after_scope_narrowing(self) -> None:
        AppUser.objects.create(
            email="agent@example.com", display_name="Agent", role_id="analyst", status="active",
            scope={"warehouses": ["主仓"], "channels": [], "platforms": []}, version=1,
            created_at="2026-09-05T00:00:00Z", updated_at="2026-09-05T00:00:00Z",
        )
        allowed = self.post_json(
            "/api/access-control/principal/authorize-background",
            {"ownerEmail": "agent@example.com", "scope": {"warehouses": ["主仓"], "channels": [], "platforms": []}},
            "access-background-1", role="viewer", email="agent@example.com",
        )
        self.assertEqual(allowed.status_code, 200, allowed.content)
        denied = self.post_json(
            "/api/access-control/principal/authorize-background",
            {"ownerEmail": "agent@example.com", "scope": {"warehouses": ["主仓", "二仓"], "channels": [], "platforms": []}},
            "access-background-2", role="viewer", email="agent@example.com",
        )
        self.assertEqual(denied.status_code, 403)
