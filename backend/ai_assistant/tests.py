from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from access_control.models import AccessRole, AppUser
from sales.auth import Principal
from . import models as m, chat, memory, workflows, sandbox, space, provider, views
from .control_models import AiDataRevision, AiWriteAuthority
from .policy import AiError, canonical, digest, mutation, revision, scope_covers
from .transport import signed_headers

ADMIN = Principal("local-admin@teruisi.local", "本地管理员", "admin", None)
CATALOG = [
    {
        "name": "get_data_freshness",
        "title": "数据水位",
        "description": "只读数据水位",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "risk": "read_only",
        "allowedRoles": ["analyst", "operator", "admin"],
        "scopePolicy": "metadata_safe",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "execution": {
            "environment": "worker_inline",
            "mode": "direct",
            "allowedSurfaces": ["ai_chat", "ai_agent"],
            "timeoutMs": 12000,
            "maxResultCharacters": 40000,
            "maxCallsPerRequest": 4,
        },
    }
]


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class AiDomainTests(TestCase):
    def setUp(self):
        AiDataRevision.objects.get_or_create(domain="ai-assistant")
        AiWriteAuthority.objects.get_or_create(id=1)
        for role in ["viewer", "analyst", "operator", "admin"]:
            AccessRole.objects.get_or_create(
                code=role,
                defaults={
                    "label": role,
                    "description": role,
                    "rank": ["viewer", "analyst", "operator", "admin"].index(role),
                    "permissions": [],
                },
            )
        self.owner = self.user(
            "owner@example.invalid",
            "analyst",
            {"warehouses": ["A"], "channels": [], "platforms": ["京东"]},
        )
        self.other = self.user("other@example.invalid", "analyst", None)
        self.viewer = self.user("viewer@example.invalid", "viewer", None)
        self.model = m.AiModels.objects.create(
            id="model-test",
            name="Isolated provider",
            protocol="openai_compatible",
            model_type="text",
            model_name="fixture",
            base_url="https://api.openai.com/v1",
            api_key_encrypted="encrypted-fixture",
            api_key_suffix="ture",
            is_default_text_model=1,
            status="enabled",
        )

    def user(self, email, role, scope):
        AppUser.objects.create(
            email=email,
            display_name=email,
            role_id=role,
            scope=scope,
            created_at=timezone.now(),
            updated_at=timezone.now(),
        )
        return Principal(email, email, role, scope)

    def call(self, path, payload=None, principal=None, method="POST", request_id=None):
        principal = principal or self.owner
        payload = {} if payload is None else payload
        with (
            patch.dict(
                "os.environ",
                {
                    "TERUISI_DJANGO_INTERNAL_SECRET": "A-valid-isolated-signing-secret-13579-abcdefghijklmnopqrstuvwxyz"
                },
            ),
            override_settings(
                DJANGO_INTERNAL_SECRET="A-valid-isolated-signing-secret-13579-abcdefghijklmnopqrstuvwxyz"
            ),
        ):
            raw = canonical(payload) if method != "GET" else ""
            headers = signed_headers(path, payload, principal, request_id)
            if method == "GET":
                import hashlib
                import hmac

                headers["X-Teruisi-Content-SHA256"] = hashlib.sha256(b"").hexdigest()
                message = "\n".join(
                    [
                        "v1",
                        headers["X-Teruisi-Timestamp"],
                        headers["X-Teruisi-Request-Id"],
                        "GET",
                        path,
                        "",
                        headers["X-Teruisi-Content-SHA256"],
                        headers["X-Teruisi-Principal"],
                    ]
                )
                headers["X-Teruisi-Signature"] = (
                    "v1="
                    + hmac.new(
                        b"A-valid-isolated-signing-secret-13579-abcdefghijklmnopqrstuvwxyz",
                        message.encode(),
                        hashlib.sha256,
                    ).hexdigest()
                )
            return self.client.generic(
                method, path, raw, content_type="application/json", headers=headers
            )

    def test_nested_mutation_revision_and_rollback(self):
        before = int(revision())
        with mutation(self.owner):
            with mutation(self.owner):
                m.AiToolAuditLogs.objects.create(
                    id="nested-audit",
                    request_id="nested",
                    actor_email=self.owner.email,
                    actor_role=self.owner.role,
                    surface="test",
                    tool_name="nested",
                    status="succeeded",
                )
        self.assertEqual(int(revision()), before + 1)
        with self.assertRaises(AiError):
            with mutation(self.owner):
                with mutation(self.viewer):
                    pass
        self.assertEqual(int(revision()), before + 1)

    def test_scope_snapshots_fail_closed(self):
        self.assertFalse(scope_covers(self.owner.scope, {"warehouses": []}))
        self.assertFalse(scope_covers(None, "invalid-json"))
        self.assertFalse(scope_covers(self.owner.scope, None))
        self.assertTrue(
            scope_covers(
                self.owner.scope, {"warehouses": [], "channels": [], "platforms": []}
            )
        )

    def test_memory_cas_atomic_audit_scope_and_duplicate(self):
        body = {
            "kind": "business_context",
            "key": "产品定位",
            "content": "系列定位为家庭清洁设备，沟通时优先介绍易清洁结构",
            "confirmed": True,
        }
        with mutation(self.owner):
            created = memory.save(body, self.owner, "memory-first")
        identifier = created["item"]["id"]
        self.assertEqual(m.AiMemoryAuditLogs.objects.count(), 1)
        self.assertEqual(memory.listing({}, self.other)["pagination"]["total"], 0)
        with mutation(self.owner):
            duplicate = memory.save(body, self.owner, "memory-second")
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(m.AiMemoryEntries.objects.count(), 1)
        with self.assertRaises(AiError):
            with mutation(self.owner):
                memory.save(
                    {"confirmed": True, "expectedVersion": 7, "content": "修改"},
                    self.owner,
                    "memory-cas",
                    identifier,
                )
        self.assertEqual(m.AiMemoryAuditLogs.objects.count(), 2)
        with self.assertRaises(AiError):
            with mutation(self.owner):
                memory.save(
                    {**body, "content": "api_key=sk-sensitive-12345678901234567890"},
                    self.owner,
                    "memory-secret",
                )

    def test_chat_tool_identity_replay_and_delete(self):
        replies = [
            {
                "text": "",
                "calls": [
                    {"id": "call-1", "name": "get_data_freshness", "arguments": {}}
                ],
                "frame": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "get_data_freshness",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
            },
            {
                "text": "已核对数据截止日期。",
                "calls": [],
                "frame": {"role": "assistant", "content": "已核对数据截止日期。"},
            },
        ]
        body = {"clientRequestId": "chat-first", "message": "查询数据水位"}
        with (
            patch.object(chat.transport, "catalog", return_value=CATALOG),
            patch.object(chat.provider, "turn", side_effect=replies) as provider,
            patch.object(
                chat.transport,
                "execute_tool",
                return_value={"ok": True, "data": {"returned": 0}},
            ) as tool,
        ):
            answer = chat.answer(body, self.owner, "chat-http")
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(tool.call_args.kwargs["provider_call_id"], "call-1")
            self.assertEqual(tool.call_args.kwargs["policy_digest"], digest(CATALOG))
            self.assertEqual(chat.answer(body, self.owner, "chat-replay"), answer)
            self.assertEqual(provider.call_count, 2)
        with self.assertRaises(AiError):
            chat.messages({"conversationId": answer["conversationId"]}, self.other)
        with mutation(self.owner):
            chat.delete(answer["conversationId"], self.owner)
        self.assertFalse(m.AiConversationMessages.objects.exists())
        self.assertEqual(m.AiConversationDeletionAudits.objects.count(), 1)

    def test_provider_unknown_cannot_be_replayed(self):
        body = {"clientRequestId": "uncertain", "message": "查询数据"}
        with (
            patch.object(chat.transport, "catalog", return_value=CATALOG),
            patch.object(chat.provider, "turn", side_effect=TimeoutError) as provider,
        ):
            with self.assertRaises(TimeoutError):
                chat.answer(body, self.owner, "unknown-http")
            with self.assertRaises(AiError):
                chat.answer(body, self.owner, "unknown-retry")
            self.assertEqual(provider.call_count, 1)
        self.assertEqual(
            m.AiChatRequestReceipts.objects.get(client_request_id="uncertain").status,
            "unknown",
        )
        failure = m.AiToolAuditLogs.objects.get(
            request_id="unknown-http", tool_name="ai_chat_provider", status="failed"
        )
        self.assertEqual(failure.error_code, "provider_timeout")
        self.assertGreaterEqual(failure.duration_ms, 0)

    def test_workflow_cycle_rejected_without_writes(self):
        bad = {
            "nodes": [
                {"key": "a", "type": "agent", "instruction": "a", "dependsOn": ["b"]},
                {"key": "b", "type": "agent", "instruction": "b", "dependsOn": ["a"]},
            ]
        }
        with self.assertRaises(AiError):
            workflows.validate_graph(bad)
        self.assertFalse(m.AiWorkflowRuns.objects.exists())

    def test_dry_workflow_review_and_idempotence(self):
        body = {
            "clientRequestId": "workflow-dry",
            "name": "隔离演练",
            "dryRun": True,
            "graph": {
                "nodes": [
                    {"key": "analysis", "type": "agent", "instruction": "查询数据"},
                    {
                        "key": "review",
                        "type": "human_review",
                        "dependsOn": ["analysis"],
                        "instruction": "审核分析",
                    },
                ]
            },
        }
        first = workflows.create(body, self.owner, True)
        self.assertTrue(workflows.create(body, self.owner, True)["replayed"])
        for _ in range(4):
            workflows.workflow_tick()
        row = m.AiWorkflowRuns.objects.get(id=first["item"]["id"])
        self.assertEqual(row.status, "completed")
        self.assertTrue(
            all(
                status == "skipped"
                for status in m.AiWorkflowNodeRuns.objects.filter(
                    run_id=row.id
                ).values_list("status", flat=True)
            )
        )
        with patch.object(workflows.transport, "catalog", return_value=CATALOG):
            formal = workflows.create(
                {
                    "clientRequestId": "formal-review",
                    "name": "人工复核",
                    "graph": {
                        "nodes": [
                            {
                                "key": "review",
                                "type": "human_review",
                                "instruction": "复核",
                            }
                        ]
                    },
                },
                self.owner,
                True,
            )
        workflows.workflow_tick()
        row = m.AiWorkflowRuns.objects.get(id=formal["item"]["id"])
        self.assertEqual(row.status, "waiting_review")
        node = m.AiWorkflowNodeRuns.objects.get(run_id=row.id, node_key="review")
        workflows.review(
            row.id,
            "review",
            {"expectedVersion": node.version, "decision": "approve"},
            self.owner,
        )
        for _ in range(3):
            workflows.workflow_tick()
        row.refresh_from_db()
        self.assertEqual(row.status, "completed")
        self.assertFalse(m.AiAgentJobs.objects.exists())

    def test_formal_agent_checkpoint_and_completed(self):
        with patch.object(workflows.transport, "catalog", return_value=CATALOG):
            first = workflows.create(
                {"clientRequestId": "agent-formal", "task": "给出隔离测试摘要"},
                self.owner,
            )
            with patch.object(
                workflows.provider,
                "turn",
                return_value={
                    "text": "隔离测试完成",
                    "calls": [],
                    "frame": {"role": "assistant", "content": "隔离测试完成"},
                    "usage": {},
                    "providerRequestId": "fixture",
                },
            ):
                for _ in range(4):
                    workflows.agent_tick()
        row = m.AiAgentJobs.objects.get(id=first["item"]["id"])
        self.assertEqual(row.status, "completed")
        self.assertEqual(m.AiAgentProviderDispatches.objects.count(), 1)
        self.assertEqual(m.AiAgentProviderResults.objects.count(), 1)
        self.assertTrue(
            m.AiAgentCheckpoints.objects.filter(
                job_id=row.id, kind="completed"
            ).exists()
        )

    def test_sandbox_numeric_precision_and_no_code(self):
        result = sandbox.transform(
            [
                {"category": "A", "net": 100, "cost": 70},
                {"category": "A", "net": -10, "cost": -7},
            ],
            [
                {
                    "op": "group",
                    "groupBy": ["category"],
                    "metrics": [
                        {"aggregate": "sum", "field": "net", "as": "net"},
                        {"aggregate": "sum", "field": "cost", "as": "cost"},
                    ],
                },
                {
                    "op": "derive",
                    "leftField": "net",
                    "rightField": "cost",
                    "operator": "subtract",
                    "as": "gross",
                },
            ],
        )
        self.assertEqual(
            result["rows"], [{"category": "A", "net": 90, "cost": 63, "gross": 27}]
        )
        with self.assertRaises(AiError):
            sandbox.transform([], [{"op": "eval", "code": "arbitrary"}])

    def test_signed_reader_consumer_and_tamper(self):
        response = self.call(
            "/api/ai/consumer",
            {"operation": "model-list", "modelType": "text"},
            self.viewer,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["X-AI-Revision"], revision())
        self.assertNotIn("api_key_encrypted", response.content.decode())
        with patch.dict(
            "os.environ",
            {
                "TERUISI_DJANGO_INTERNAL_SECRET": "A-valid-isolated-signing-secret-13579-abcdefghijklmnopqrstuvwxyz"
            },
        ):
            bad = self.client.post(
                "/api/ai/consumer",
                data='{"operation":"model-list"}',
                content_type="application/json",
            )
        self.assertEqual(bad.status_code, 401)

    def test_generic_receipt_replay_cas_and_audit(self):
        payload = {
            "confirmed": True,
            "kind": "preference",
            "key": "输出格式",
            "content": "请使用简洁中文段落",
        }
        first = self.call("/api/ai/memories", payload, request_id="same-request")
        self.assertEqual(first.status_code, 201, first.content)
        again = self.call("/api/ai/memories", payload, request_id="same-request")
        self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(again["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(m.AiMemoryEntries.objects.count(), 1)
        self.assertEqual(m.AiMemoryAuditLogs.objects.count(), 1)
        conflict = self.call(
            "/api/ai/memories",
            {**payload, "content": "新内容"},
            request_id="same-request",
        )
        self.assertEqual(conflict.status_code, 409, conflict.content)

    def test_memory_recall_is_an_explicit_bounded_match(self):
        with mutation(self.owner):
            memory.save(
                {
                    "confirmed": True,
                    "kind": "preference",
                    "key": "输出格式",
                    "content": "使用中文段落",
                },
                self.owner,
                "memory-match",
            )
        self.assertEqual(memory.recall("无关查询", self.owner)["returned"], 0)
        self.assertEqual(memory.recall("", self.owner)["returned"], 0)
        self.assertEqual(memory.recall("中文", self.other)["returned"], 0)
        self.assertEqual(memory.recall("中文", self.owner)["totalMatched"], 1)
        self.assertEqual(memory.listing({}, self.owner)["pagination"]["pageSize"], 20)

    def test_message_body_is_bounded_in_the_database_query(self):
        conv = m.AiConversations.objects.create(
            id="bounded-conversation", title="test", created_by=self.owner.email
        )
        m.AiConversationScopes.objects.create(
            conversation_id=conv.id, scope_json=canonical(self.owner.scope)
        )
        chat.append(conv.id, "assistant", "中" * 50000)
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as captured:
            result = chat.messages({"conversationId": conv.id}, self.owner)
        self.assertTrue(result["items"][0]["contentTruncated"])
        self.assertEqual(result["items"][0]["contentBytes"], 150000)
        self.assertLessEqual(len(result["items"][0]["content"].encode()), 24 * 1024)
        self.assertTrue(any("SUBSTR" in q["sql"].upper() for q in captured))

    def test_nested_request_capacity_reserves_audit_and_cancel_threads(self):
        self.assertTrue(views._primary_slots.acquire(blocking=False))
        self.assertTrue(views._primary_slots.acquire(blocking=False))
        try:
            self.assertEqual(
                self.call(
                    "/api/ai/chat", {"clientRequestId": "capacity", "message": "test"}
                ).status_code,
                429,
            )
            self.assertEqual(
                self.call(
                    "/api/ai/chat/cancel", {"clientRequestId": "capacity"}
                ).status_code,
                200,
            )
            response = self.call(
                "/api/ai/consumer",
                {
                    "operation": "tool-audit",
                    "entry": {
                        "requestId": "nested-audit",
                        "actorEmail": self.owner.email,
                        "actorRole": self.owner.role,
                        "surface": "ai_chat",
                        "toolName": "fixture",
                        "status": "started",
                        "durationMs": 0,
                    },
                },
            )
            self.assertEqual(response.status_code, 200, response.content)
        finally:
            views._primary_slots.release()
            views._primary_slots.release()

    def test_provider_probe_uses_real_image_and_rejects_nonrecognition(self):
        self.model.model_type = "vision"
        with patch.object(provider, "turn", return_value={"text": "红色"}) as request:
            self.assertEqual(provider.probe(self.model), "图片识别连接成功")
            content = request.call_args.args[1][0]["content"]
            import base64

            raw = base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])
            self.assertEqual(space.png(raw), (2, 2))
        with patch.object(provider, "turn", return_value={"text": "无法查看图片"}):
            with self.assertRaises(AiError):
                provider.probe(self.model)

    def image_job(self, number=1):
        profile, _ = m.AiSpaceModelProfiles.objects.get_or_create(
            id="image-profile",
            defaults={
                "name": "isolated",
                "protocol": "openai_images",
                "model_name": "fixture",
                "base_url": "https://api.openai.com/v1",
                "api_key_encrypted": "fixture",
                "api_key_suffix": "ture",
                "status": "enabled",
            },
        )
        template, _ = m.AiSpaceTemplates.objects.get_or_create(
            id="image-template",
            defaults={
                "scene": "product_main",
                "name": "fixture",
                "prompt_template": "{product_name}",
                "size": "1024x1024",
                "model_profile_id": profile.id,
                "updated_by": ADMIN.email,
            },
        )
        return space.create(
            {
                "clientRequestId": "image-client-" + str(number),
                "scene": "product_main",
                "templateId": template.id,
                "productName": "测试商品",
                "count": 1,
            },
            self.owner,
        )["item"]

    def test_image_unknown_dispatch_is_not_replayed_and_cross_owner_is_hidden(self):
        job = self.image_job()
        with (
            patch.object(space, "decrypt", return_value="isolated-test-key"),
            patch.object(
                space.transport, "bounded_json", side_effect=TimeoutError()
            ) as paid,
        ):
            self.assertEqual(space.tick()["status"], "failed")
            self.assertEqual(space.tick()["status"], "idle")
            self.assertEqual(paid.call_count, 1)
        self.assertEqual(m.AiSpaceDispatchReceipts.objects.count(), 1)
        self.assertEqual(m.AiSpaceDispatchResults.objects.get().status, "failed")
        with self.assertRaises(AiError):
            space.get_job(job["id"], self.other)

    def test_image_admin_audit_is_atomic_and_redacts_model_secret(self):
        from . import configuration

        with patch.dict(
            "os.environ",
            {
                "AI_SECRET_ENCRYPTION_KEY": "isolated-encryption-key-abcdefghijklmnopqrstuvwxyz"
            },
        ):
            with mutation(ADMIN):
                result = configuration.save_model(
                    {
                        "name": "fixture",
                        "modelName": "image-fixture",
                        "baseUrl": "https://api.openai.com/v1",
                        "apiKey": "secret-model-fixture",
                        "status": "enabled",
                    },
                    ADMIN,
                    image=True,
                )
        audit = m.AiSpaceAdminAudits.objects.get()
        self.assertEqual(audit.action, "upsert_profile")
        self.assertEqual(audit.entity_id, result["id"])
        self.assertNotIn("secret-model-fixture", audit.after_json)
        self.assertNotIn("api_key_encrypted", audit.after_json)

    def test_image_favorites_and_listing_do_not_grow_queries_per_job(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        for number in range(1, 6):
            self.image_job(number)
        with CaptureQueriesContext(connection) as captured:
            result = space.listing({}, self.owner)
        self.assertEqual(result["pagination"]["total"], 5)
        self.assertEqual(result["pagination"]["pageSize"], 20)
        self.assertLessEqual(len(captured), 5)
        result = space.listing({"favorites": "1"}, self.owner, assets=True)
        self.assertEqual(result["pagination"]["pageSize"], 24)
        self.assertEqual(result["items"], [])

    def test_expired_request_budget_never_contacts_a_provider(self):
        with (
            chat.transport.request_budget(-1),
            patch.object(chat.transport, "resolve_addresses") as dns,
        ):
            with self.assertRaises(AiError):
                chat.transport.bounded_json(
                    "https://api.openai.com/v1/chat/completions", {}
                )
        dns.assert_not_called()

    def test_artifact_privacy_money_csv_digest_and_browser_contract(self):
        from . import artifacts

        conv = m.AiConversations.objects.create(
            id="artifact-conversation", title="test", created_by=self.owner.email
        )
        m.AiConversationScopes.objects.create(
            conversation_id=conv.id, scope_json=canonical(self.owner.scope)
        )
        message = chat.append(conv.id, "assistant", "test")
        with mutation(self.owner):
            result = chat._artifacts(
                [
                    (
                        "get_sales",
                        {
                            "items": [
                                {
                                    "净额": -125,
                                    "SKU": "=SUM(A1)",
                                    "rawContent": "private",
                                    "apiKey": "secret",
                                }
                            ],
                            "totalMatched": 7,
                        },
                    )
                ],
                conv,
                message,
                self.owner,
            )
        self.assertEqual(result[0]["columns"], ["净额", "SKU"])
        self.assertEqual(result[0]["rows"], [[-125, "=SUM(A1)"]])
        self.assertEqual(result[0]["rowCount"], 7)
        row = m.AiArtifacts.objects.get()
        self.assertIn("-125,'=SUM(A1)", artifacts.csv_content(row))
        self.assertNotIn("'-125", artifacts.csv_content(row))
        with mutation(self.owner):
            response = chat.csv_download(row.id, self.owner, "artifact-download")
        self.assertEqual(
            m.AiArtifactDeliveries.objects.get().byte_size,
            len(response["content"].encode()),
        )
        row.rows_json = '[[0,"tampered"]]'
        with self.assertRaises(AiError):
            artifacts.public(row)
        with self.assertRaises(AiError):
            chat.csv_download(row.id, self.other, "artifact-denied")

    def test_knowledge_lexical_contract_and_role_filtered_prompt_context(self):
        from . import knowledge

        m.AiKnowledgeEntries.objects.create(
            id="knowledge-metric",
            source_type="business_metric",
            source_ref="contract",
            title="大毛利率",
            content="按分摊后金额与成本计算，金额单位为分",
            tags_json='["毛利"]',
            allowed_roles_json='["analyst"]',
            content_digest="a" * 64,
        )
        result = knowledge.search("本月大毛利率", self.owner, 4)
        self.assertEqual(result["matchMode"], "deterministic_lexical")
        self.assertEqual(result["returned"], 1)
        self.assertIn("excerpt", result["items"][0])
        self.assertNotIn("content", result["items"][0])
        self.assertEqual(knowledge.search("大毛利率", self.viewer)["returned"], 0)
        self.assertEqual(
            knowledge.search("totally unrelated", self.owner)["returned"], 0
        )
        self.assertIn("knowledge-metric", knowledge.context("大毛利率", self.owner))
        for limit in [0, 9, True]:
            with self.assertRaises(AiError):
                knowledge.search("毛利", self.owner, limit)

    def image_bytes(self):
        import struct
        import zlib

        def chunk(kind, data):
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 1024, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 1024) * 1024))
            + chunk(b"IEND", b"")
        )

    def test_image_publish_requires_confirmed_storage_and_cleanup_preserves_published_asset(
        self,
    ):
        import base64

        raw = self.image_bytes()
        self.image_job()

        with (
            patch.object(space, "decrypt", return_value="isolated"),
            patch.object(
                space.transport,
                "bounded_json",
                return_value={"data": [{"b64_json": base64.b64encode(raw).decode()}]},
            ),
            patch.object(space.transport, "edge", side_effect=AssertionError("R2 retired")),
        ):
            self.assertEqual(space.tick()["status"], "succeeded")
        asset = m.AiSpaceAssets.objects.get()
        self.assertEqual(asset.width, 1024)
        self.assertEqual(bytes(m.AiSpaceAssetPayload.objects.get(asset=asset).content), raw)
        self.assertEqual(base64.b64decode(space.download(asset.id, self.owner)["base64"]), raw)
        with self.assertRaises(AiError):
            space.download(asset.id, self.other)
        m.AiSpaceAssetCleanupQueue.objects.create(object_key=asset.object_key)
        with patch.object(space.transport, "edge") as storage:
            space.cleanup()
            storage.assert_not_called()
        self.assertFalse(m.AiSpaceAssetCleanupQueue.objects.exists())

    def test_cancel_during_paid_image_dispatch_prevents_publication_and_repeat_call(
        self,
    ):
        import base64

        job = self.image_job()
        raw = self.image_bytes()

        def paid(*args, **kwargs):
            with mutation(self.owner):
                space.cancel(job["id"], self.owner)
            return {"data": [{"b64_json": base64.b64encode(raw).decode()}]}

        with (
            patch.object(space, "decrypt", return_value="isolated"),
            patch.object(space.transport, "bounded_json", side_effect=paid) as call,
            patch.object(space.transport, "edge") as storage,
        ):
            space.tick()
            space.tick()
            self.assertEqual(call.call_count, 1)
            storage.assert_not_called()
        self.assertFalse(m.AiSpaceAssets.objects.exists())
        self.assertFalse(m.AiSpaceAssetPayload.objects.exists())
        self.assertEqual(m.AiSpaceJobItems.objects.get().status, "cancelled")

    def test_image_payload_failure_rolls_back_publication_and_does_not_repeat_paid_call(self):
        import base64
        self.image_job()
        raw = self.image_bytes()
        with (
            patch.object(space, "decrypt", return_value="isolated"),
            patch.object(space.transport, "bounded_json", return_value={"data": [{"b64_json": base64.b64encode(raw).decode()}]}) as paid,
            patch.object(m.AiSpaceAssetPayload.objects, "create", side_effect=RuntimeError("database unavailable")),
            patch.object(space.transport, "edge", side_effect=AssertionError("R2 retired")),
        ):
            self.assertEqual(space.tick()["status"], "failed")
            space.tick()
        self.assertEqual(paid.call_count, 1)
        self.assertFalse(m.AiSpaceAssets.objects.exists())
        self.assertFalse(m.AiSpaceAssetPayload.objects.exists())
        self.assertEqual(m.AiSpaceJobItems.objects.get().status, "failed")

    def test_retired_storage_actions_fail_before_any_network_call(self):
        from . import transport
        with patch.object(transport, "bounded_json") as network:
            for action in ("storage_get", "storage_put", "storage_delete"):
                with self.assertRaises(AiError):
                    transport.edge(action, {}, self.owner)
            network.assert_not_called()

    def test_postgres_payload_constraints_reject_missing_tampered_or_mutated_bytes(self):
        import base64
        from django.db import connection, transaction, DatabaseError
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL database constraint test")
        self.image_job()
        raw = self.image_bytes()
        with (
            patch.object(space, "decrypt", return_value="isolated"),
            patch.object(space.transport, "bounded_json", return_value={"data": [{"b64_json": base64.b64encode(raw).decode()}]}),
        ):
            self.assertEqual(space.tick()["status"], "succeeded")
        asset = m.AiSpaceAssets.objects.get()
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ai_asset_payload_complete IMMEDIATE")
        for sql, args in (
            ("UPDATE ai_space_asset_payloads SET content=%s WHERE asset_id=%s", [b"tampered", asset.id]),
            ("DELETE FROM ai_space_asset_payloads WHERE asset_id=%s", [asset.id]),
            ("UPDATE ai_space_assets SET content_sha256=%s WHERE id=%s", ["0" * 64, asset.id]),
        ):
            with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql, args)
        self.assertEqual(base64.b64decode(space.download(asset.id, self.owner)["base64"]), raw)
