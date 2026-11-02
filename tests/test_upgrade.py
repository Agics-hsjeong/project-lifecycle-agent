"""Migration and relocation regressions using synthetic customer files only."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
from tools import starter, lifecycle, upgrade

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        test_root = starter.ROOT / '.test-output'
        test_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.root = starter.init_project(Path(self.temp.name), 'legacy', 'Synthetic legacy', 'product')
        data = json.loads((self.root / 'project.json').read_text(encoding='utf-8'))
        data.update(starter_version='0.5.1', current_stage='planning')
        (self.root / 'project.json').write_text(json.dumps(data), encoding='utf-8')
        (self.root / 'registers/customer.csv').write_text('id,custom\nCUSTOM-1,preserved\n', encoding='utf-8')
        (self.root / 'docs/customer.md').write_text('Synthetic customer content, never a blueprint.', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_upgrade_preserves_customer_files_stage_and_legacy_profile(self):
        before={p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        plan=upgrade.preview(self.root)
        self.assertEqual(lifecycle.events(self.root), [])
        result=upgrade.apply(self.root,plan)
        for name, content in before.items():
            if name!='project.json': self.assertEqual((self.root/name).read_bytes(),content,name)
        data=starter.load_project(self.root)[1]
        self.assertEqual(data['starter_version'],upgrade.target_version())
        self.assertEqual(data['current_stage'],'planning')
        self.assertNotIn('workflow',data)
        self.assertEqual(len(lifecycle.inspect_project(self.root)['workflow']['stages']),11)
        self.assertTrue((self.root/result['backup_ref']).is_file())

    def test_manual_csv_change_invalidates_plan_without_writes(self):
        plan=upgrade.preview(self.root)
        (self.root/'registers/customer.csv').write_text('changed\n',encoding='utf-8')
        before=(self.root/'project.json').read_bytes()
        with self.assertRaises(ValueError):upgrade.apply(self.root,plan)
        self.assertEqual((self.root/'project.json').read_bytes(),before)
        self.assertEqual(lifecycle.events(self.root),[])

    def test_new_revision_invalidates_plan(self):
        plan=upgrade.preview(self.root)
        lifecycle.apply_patch(self.root,{'expected_revision':None,'project':{'objective':'Synthetic newer change'}})
        with self.assertRaises(ValueError):upgrade.apply(self.root,plan)

    def test_same_version_is_idempotent_and_downgrade_rejected(self):
        upgrade.apply(self.root,upgrade.preview(self.root))
        count=len(lifecycle.events(self.root))
        self.assertTrue(upgrade.apply(self.root,upgrade.preview(self.root))['unchanged'])
        self.assertEqual(len(lifecycle.events(self.root)),count)
        data=starter.load_project(self.root)[1];data['starter_version']='99.0.0'
        (self.root/'project.json').write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(ValueError):upgrade.preview(self.root)

    def test_restore_keeps_upgrade_audit_and_original_metadata(self):
        original=starter.load_project(self.root)[1]
        result=upgrade.apply(self.root,upgrade.preview(self.root))
        plan=upgrade.restore_preview(self.root,result['upgrade_id'])
        self.assertEqual(plan['conflicts'],[])
        upgrade.restore(self.root,plan)
        data=starter.load_project(self.root)[1]
        for key in original:
            if key!='updated_at':self.assertEqual(data[key],original[key])
        self.assertEqual([e['action'] for e in lifecycle.events(self.root)],['upgrade','upgrade-restore'])
        self.assertTrue((self.root/result['backup_ref']).is_file())

    def test_restore_refuses_later_user_edit_or_event(self):
        result=upgrade.apply(self.root,upgrade.preview(self.root))
        plan=upgrade.restore_preview(self.root,result['upgrade_id'])
        lifecycle.apply_patch(self.root,{'expected_revision':result['revision'],'project':{'objective':'Keep this change'}})
        with self.assertRaises(ValueError):upgrade.restore(self.root,plan)
        self.assertEqual(starter.load_project(self.root)[1]['objective'],'Keep this change')

    def test_backup_tampering_is_detected(self):
        result=upgrade.apply(self.root,upgrade.preview(self.root))
        path=self.root/result['backup_ref'];data=json.loads(path.read_text(encoding='utf-8'));data['source_version']='0.0.0'
        path.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(ValueError):upgrade.restore_preview(self.root,result['upgrade_id'])

    def test_project_can_move_and_upgrade_without_original_path(self):
        moved=Path(self.temp.name)/'relocated'
        shutil.move(str(self.root),moved);self.root=moved
        upgrade.apply(moved,upgrade.preview(moved))
        status=lifecycle.doctor(moved)
        self.assertTrue(status['ready'])
        self.assertEqual(status['project_path'],str(moved.resolve()))
        self.assertFalse(status['upgrade_required'])

    def test_semver_order_and_invalid_versions(self):
        versions=['0.5.1','2.0.0-alpha.2','2.0.0-alpha.10','2.0.0-beta.1','2.0.0','2.0.1']
        self.assertEqual(sorted(versions,key=upgrade.version),versions)
        for bad in ['2','2.0.0-01','2.0.0-alpha..1','garbage']:
            with self.assertRaises(ValueError):upgrade.version(bad)

    def test_cli_upgrade_and_restore_from_customer_working_directory(self):
        import subprocess, sys
        request=Path(self.temp.name)/'plan.json'
        def cli(*args):
            r=subprocess.run([sys.executable,'-B','-X','utf8',str(starter.ROOT/'tools/starter.py'),*map(str,args)],cwd=self.temp.name,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(r.returncode,0,r.stderr)
            return json.loads(r.stdout)
        plan=cli('upgrade-preview',self.root)
        request.write_text(json.dumps(plan),encoding='utf-8')
        applied=cli('upgrade',self.root,'--file',request)
        plan=cli('upgrade-restore-preview',self.root,'--upgrade-id',applied['upgrade_id'])
        request.write_text(json.dumps(plan),encoding='utf-8')
        self.assertEqual(cli('upgrade-restore',self.root,'--file',request)['version'],'0.5.1')

    def test_release_checker_detects_manual_and_version_drift(self):
        from tools.check_release import check
        self.assertTrue(check()['ready'])
        with mock.patch('tools.check_release.json.loads',return_value={'name':'bad','version':'0.0.0','starter_version':'99.0.0'}):
            self.assertFalse(check()['ready'])
