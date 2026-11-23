"""Portable Windows aliases cannot turn control/input paths into work outputs."""
from pathlib import Path
import tempfile
import unittest
from tools import workspace

class WorkspacePathIdentityTests(unittest.TestCase):
    def test_private_control_aliases_are_rejected_before_filesystem_lookup(self):
        for ref in ['STATE/runs.json','State/events.jsonl','reports/.CoDeX/private.md','reports/.ENV.production','STATE./runs.json','state /runs.json','reports//qa.md','reports/./qa.md','reports/file.md.','reports/file.md ','reports/qa\x00.md']:
            with self.subTest(ref=ref),self.assertRaises(ValueError):workspace._artifact_reference(ref)

    def test_producer_output_guard_rejects_all_input_and_management_case_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            for ref in ['INPUTS/source.md','REGISTERS/tasks.csv','Templates/style.md','PROCESS/RULES.md','Project.json','HaNdOfF.Md','AGENTS.MD','Claude.MD','REPORTS/WORKSPACE-DECISIONS/private.md']:
                with self.subTest(ref=ref),self.assertRaises(ValueError):workspace.execution_path(root,ref)

    def test_ordinary_cased_output_names_remain_usable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            self.assertEqual(workspace.execution_path(root,'Reports/Public-QA.md'),root/'Reports/Public-QA.md')
            workspace._artifact_reference('Reports/QA 결과.md')
            self.assertEqual(workspace.execution_path(root,'Reports/'),root/'Reports')

if __name__=='__main__':unittest.main()
