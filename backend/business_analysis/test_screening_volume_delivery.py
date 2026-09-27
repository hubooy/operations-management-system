"""Pure exact optional screening bindings; no authority or publication claims."""
from copy import deepcopy
import io
from unittest import TestCase
from . import volume_delivery as delivery, volume_files, volume_plan
from . import test_volume_delivery as fixture
from .contracts import AnalysisContractError, canonical
from .report_files import Table, Column


def metadata():
    return {"screeningRef":{"schemaVersion":"business-screening-storage-reference-v1","id":"screening-test",
        "reportId":fixture.BINDINGS["report_id"],**{key:"a"*64 for key in
            ("bindingDigest","selectionPlanDigest","resultDigest","contentRootDigest","manifestDigest")}},
        "screeningPackagePolicy":"screening-role-package-policy-v1",
        "screeningPackageDigests":{role:"b"*64 for role in ("commerce","promotion","market_b2b","independent_review","report")}}


class ScreeningDeliveryTests(TestCase):
    def render(self, meta):
        tables=[Table("one","合成","合成",(Column("x","x"),),iter([[1]]),1)]
        outputs=[volume_files.VolumeStreams(io.BytesIO(),io.BytesIO())]
        plan=volume_plan.build(volume_files.request_for(tables,report_id=fixture.BINDINGS["report_id"],evidence_digest=fixture.EVIDENCE,renderer_version=4))
        return volume_files.render(tables,outputs,plan=plan,report_id=fixture.BINDINGS["report_id"],evidence_digest=fixture.EVIDENCE,
            renderer_version=4,title="合成",metadata=meta)

    def test_real_render_exact_group_and_compact_unchanged(self):
        full=self.render(metadata())
        self.assertEqual({k:full[k] for k in delivery.SCREENING_KEYS},metadata())
        root,raw=delivery.make(full,**fixture.IDENTITY)
        self.assertEqual(set(root),delivery.ROOT_FIELDS)
        self.assertEqual(delivery.verify_full(root,raw,**fixture.BINDINGS),full)

    def test_missing_group_unknown_policy_wrong_role_and_type_reject_after_resign(self):
        full=self.render(metadata())
        changes=[]
        for key in delivery.SCREENING_KEYS:
            bad=deepcopy(full);del bad[key];changes.append(bad)
        bad=deepcopy(full);bad["screeningPackagePolicy"]="other";changes.append(bad)
        bad=deepcopy(full);bad["screeningRef"]["reportId"]="other";changes.append(bad)
        bad=deepcopy(full);bad["screeningPackageDigests"]["commerce"]=True;changes.append(bad)
        bad=deepcopy(full);bad["screeningPackageDigests"]["other"]="b"*64;changes.append(bad)
        bad=deepcopy(full);bad["screeningRef"]["extra"]=1;changes.append(bad)
        for bad in changes:
            with self.assertRaises(AnalysisContractError):delivery.make(fixture.resign(bad),**fixture.IDENTITY)

    def test_legacy_full_stays_exact_and_does_not_gain_defaults(self):
        old=fixture.synthetic_manifest();before=canonical(old).encode()
        root,raw=delivery.make(old,**fixture.IDENTITY,max_tables=1)
        self.assertEqual(raw,before)
        self.assertFalse(delivery.SCREENING_KEYS & old.keys())
        self.assertEqual(delivery.verify_full(root,raw,**fixture.BINDINGS,max_tables=1),old)
