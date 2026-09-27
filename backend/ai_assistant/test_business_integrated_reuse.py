"""Completed memo tests only: no ORM, services, provider or runtime wiring."""
from contextlib import contextmanager
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch

from . import business_integrated_reuse as reuse
from .policy import AiError


class CompletedReuseTests(unittest.TestCase):
    def setUp(self):
        self.binding={"ownerEmail":"synthetic@example.invalid","scope":None,"reportId":"report-local",
            "snapshotDigest":"a"*64,"sealedDigest":"b"*64,"algorithmVersion":"synthetic-v1"}
        self.live=deepcopy(self.binding)
        self.validate=Mock(side_effect=lambda:deepcopy(self.live))

    def scope(self,**kwargs):
        return reuse.CompletedReuse(self.binding,validate=self.validate,**kwargs)

    def get(self,scope,key=None,loader=None,kind="analysis",binding=None):
        return scope.resolve(kind,key or {"mode":"mapped","pairKey":"c"*64,"dimension":"sku","offset":0},
            loader or (lambda:{"value":7}),binding=self.binding if binding is None else binding)

    def test_load_once_per_exact_kind_key_and_live_validate_on_every_use(self):
        loader=Mock(return_value={"rows":[{"value":7.5,"missing":None}]})
        with self.scope() as scope:
            self.assertEqual(self.get(scope,loader=loader),self.get(scope,loader=loader))
            self.assertEqual(self.validate.call_count,5) # enter + two checks per miss/hit
            loader.assert_called_once()
            self.get(scope,key={"offset":1},loader=loader)
            self.get(scope,kind="budget",loader=loader)
            self.assertEqual(loader.call_count,3)
            self.assertEqual(scope.stats()["hits"],1)
        self.assertEqual(self.validate.call_count,10)
        self.assertEqual((scope.stats()["entries"],scope.stats()["bytes"]),(0,0))

    def test_binding_change_or_revoked_permission_rejects_hit_before_loader(self):
        with self.scope() as scope:
            self.get(scope)
            loader=Mock(return_value={"value":999})
            original=deepcopy(self.live)
            for key,value in (("ownerEmail","other"),("scope",{"shop":"other"}),("reportId","other"),
                    ("snapshotDigest","d"*64),("sealedDigest","e"*64),("algorithmVersion","next")):
                self.live={**original,key:value}
                with self.assertRaises(AiError):self.get(scope,loader=loader)
            self.live=original
            with self.assertRaises(AiError):self.get(scope,loader=loader,binding={**self.binding,"reportId":"other"})
            self.validate.side_effect=AiError("revoked","access_denied",403)
            with self.assertRaises(AiError) as caught:self.get(scope,loader=loader)
            self.assertEqual(caught.exception.status,403)
            self.validate.side_effect=lambda:deepcopy(self.live)
            loader.assert_not_called()

    def test_loader_late_context_failure_and_unknown_never_create_completed_entry(self):
        @contextmanager
        def source():
            yield {"rows":[1]}
            raise AiError("late source verification failed")
        def loader():
            with source() as value:result=value
            return result
        with self.scope() as scope:
            for function in (loader,Mock(side_effect=AiError("unknown","tool_dispatch_unknown",409)),Mock(side_effect=RuntimeError("failure"))):
                with self.assertRaises(Exception):self.get(scope,loader=function)
                self.assertEqual(scope.stats()["entries"],0)
            self.assertEqual(self.get(scope),{"value":7})
            self.assertEqual(scope.stats()["loads"],4)

    def test_second_validation_failure_does_not_cache_and_exit_failure_cleans(self):
        scope=self.scope()
        with self.assertRaises(AiError):
            with scope:
                def loader():
                    self.live["sealedDigest"]="changed"
                    return {"value":7}
                self.get(scope,loader=loader)
        self.assertEqual((scope.stats()["entries"],scope.stats()["bytes"]),(0,0))
        self.live=deepcopy(self.binding);scope=self.scope()
        with self.assertRaises(AiError):
            with scope:
                self.get(scope)
                self.live["scope"]={"shop":"revoked"}
        self.assertFalse(scope.stats()["active"])
        self.assertEqual(scope.stats()["entries"],0)

    def test_result_aliases_input_aliases_and_shared_children_cannot_poison_cache(self):
        child={"amount":10};original={"rows":[child,child]};key={"offset":0,"selector":{"dimension":"sku"}}
        with self.scope() as scope:
            value=self.get(scope,key=key,loader=lambda:original)
            original["rows"][0]["amount"]=999
            value["rows"][0]["amount"]=-1
            key["selector"]["dimension"]="spu"
            again=self.get(scope,key={"offset":0,"selector":{"dimension":"sku"}})
            self.assertEqual(again,{"rows":[{"amount":10},{"amount":10}]})
            self.assertIsNot(again["rows"][0],again["rows"][1])
            self.assertEqual(scope.stats()["hits"],1)

    def test_lru_count_byte_limits_and_legal_oversize_passthrough(self):
        loader=Mock(return_value={"value":"x"*50})
        with self.scope(max_entries=2) as scope:
            self.get(scope,key={"n":1},loader=loader);self.get(scope,key={"n":2},loader=loader)
            self.get(scope,key={"n":1},loader=loader);self.get(scope,key={"n":3},loader=loader)
            self.assertEqual(scope.stats()["evictions"],1)
            self.get(scope,key={"n":2},loader=loader)
            self.assertEqual(loader.call_count,4)
        with self.scope(max_bytes=160) as scope:
            for n in range(4):self.get(scope,key={"n":n},loader=loader)
            self.assertLessEqual(scope.stats()["bytes"],160)
            self.assertEqual(scope.stats()["entries"],1)
        loader.reset_mock()
        with self.scope(max_bytes=1) as scope:
            self.get(scope,loader=loader);self.get(scope,loader=loader)
            self.assertEqual((scope.stats()["entries"],scope.stats()["uncached"]),(0,2))
            self.assertEqual(loader.call_count,2)

    def test_illegal_values_do_not_escape_through_uncached_path(self):
        cyclic={};cyclic["loop"]=cyclic
        nested={};cursor=nested
        for _ in range(26):cursor["next"]={};cursor=cursor["next"]
        values=[[],{"value":float("nan")},{"value":float("inf")},{"value":2**53},
            {"value":object()},{"value":(1,2)},{1:"key"},cyclic,nested,{"value":"x"*38000}]
        with self.scope(max_bytes=1) as scope:
            for value in values:
                with self.subTest(type=type(value)),self.assertRaises(AiError):self.get(scope,loader=lambda:value)
            self.assertEqual(scope.stats()["entries"],0)
            with patch.object(reuse,"MAX_NODES",10),self.assertRaises(AiError):
                self.get(scope,loader=lambda:{"items":[0]*11})

    def test_reentry_callbacks_and_cross_thread_are_rejected(self):
        with self.scope() as scope:
            with self.assertRaises(AiError):self.get(scope,loader=lambda:self.get(scope))
            self.assertEqual(scope.stats()["entries"],0)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(AiError):pool.submit(self.get,scope).result()
                with self.assertRaises(AiError):pool.submit(scope.close).result()
            self.validate.side_effect=lambda:self.get(scope)
            with self.assertRaises(AiError):self.get(scope)
            self.validate.side_effect=lambda:deepcopy(self.live)
            self.assertEqual(self.get(scope),{"value":7})

    def test_not_entered_closed_reentered_and_exception_scope_cleanup(self):
        scope=self.scope()
        with self.assertRaises(AiError):self.get(scope)
        with self.assertRaises(RuntimeError):
            with scope:
                self.get(scope)
                raise RuntimeError("cancelled")
        self.assertEqual((scope.stats()["entries"],scope.stats()["bytes"]),(0,0))
        with self.assertRaises(AiError):self.get(scope)
        with self.assertRaises(AiError):scope.__enter__()
        scope.close() # idempotent cleanup, still cannot reopen
        with self.scope() as second:
            with self.assertRaises(AiError):second.__enter__()
            self.get(second)

    def test_corrupt_cached_bytes_reject_without_recompute(self):
        with self.scope() as scope:
            self.get(scope)
            key=next(iter(scope._entries));raw,sha,cost=scope._entries[key]
            scope._entries[key]=(raw+b" ",sha,cost)
            loader=Mock(return_value={"value":999})
            with self.assertRaises(AiError):self.get(scope,loader=loader)
            loader.assert_not_called()

    def test_strict_binding_key_kind_and_capacity_configuration(self):
        for kwargs in ({"max_bytes":True},{"max_entries":True},{"max_bytes":reuse.MAX_BYTES+1},
                {"max_entries":65},{"max_entries":0}):
            with self.assertRaises(AiError):self.scope(**kwargs)
        for binding in ({},[],{"scope":float("nan")},{"owner":object()}):
            with self.assertRaises(AiError):reuse.CompletedReuse(binding,validate=self.validate)
        with self.scope() as scope:
            for key in ([],{"offset":0.0},{"key":"x"*8192}):
                with self.assertRaises(AiError):scope.resolve("analysis",key,lambda:{},binding=self.binding)
            with self.assertRaises(AiError):scope.resolve("unknown",{},lambda:{},binding=self.binding)

    def test_independent_scopes_do_not_share_completed_results(self):
        loader=Mock(return_value={"value":7})
        for _ in range(2):
            with self.scope() as scope:
                self.get(scope,loader=loader);self.get(scope,loader=loader)
        self.assertEqual(loader.call_count,2)

    def test_exact_protocol_byte_limits_and_default_cache_bound(self):
        from .policy import canonical
        analysis={"v":"中"*((reuse.VALUE_BYTES["analysis"]-8)//3)}
        analysis["v"]+="x"*(reuse.VALUE_BYTES["analysis"]-len(canonical(analysis).encode()))
        self.assertEqual(len(canonical(analysis).encode()),38000)
        budget={"v":"x"*(reuse.VALUE_BYTES["budget"]-8)}
        self.assertEqual(len(canonical(budget).encode()),2*1024*1024)
        with self.scope() as scope:
            self.assertEqual(self.get(scope,loader=lambda:analysis),analysis)
            with self.assertRaises(AiError):self.get(scope,key={"new":True},loader=lambda:{"v":analysis["v"]+"x"})
            self.assertEqual(self.get(scope,key={"budget":1},kind="budget",loader=lambda:budget),budget)
            self.assertEqual(self.get(scope,key={"budget":2},kind="budget",loader=lambda:budget),budget)
            self.assertLessEqual(scope.stats()["bytes"],reuse.MAX_BYTES)
            self.assertEqual(scope.stats()["entries"],1) # two 2MiB payloads plus keys cannot both fit
            with self.assertRaises(AiError):self.get(scope,kind="budget",loader=lambda:{"v":budget["v"]+"x"})
        with self.scope() as scope:
            for i in range(65):self.get(scope,key={"offset":i})
            self.assertEqual((scope.stats()["entries"],scope.stats()["evictions"]),(64,1))

    def test_nonplain_hooks_never_run_and_failed_enter_releases_binding(self):
        class HostileDict(dict):
            def items(self):raise AssertionError("object hook must not run")
        class HostileString(str):
            def encode(self,*a,**k):raise AssertionError("object hook must not run")
        with self.scope() as scope:
            for value in (HostileDict(value=7),{"value":HostileString("x")}):
                with self.assertRaises(AiError):self.get(scope,loader=lambda:value)
        self.validate.side_effect=AiError("revoked","access_denied",403)
        scope=self.scope()
        with self.assertRaises(AiError):scope.__enter__()
        self.assertEqual(scope._binding,b"")
        self.assertIsNone(scope._validate)
        self.assertFalse(scope.stats()["active"])
