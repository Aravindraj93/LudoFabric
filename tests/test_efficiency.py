import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from gameagent.agents import Studio, ProjectContext, _design_summary
from gameagent.config import Config
from gameagent.llm import LLM, BudgetReached
from gameagent.metrics import summarize
from gameagent.orchestrator import Orchestrator
from gameagent.schemas import (GameDesign, ArtDirection, Architecture, Plan, TaskSpec, Brief,
                              CodeContract, AgentOutput, FileWrite, FilePatch, AssetRequest)
from gameagent.unity.project import UnityProject
from gameagent.unity.runner import UnityResult
from gameagent.docs import design_md, plan_md


def design():
    return GameDesign(title="Coins", genre="Arcade", pitch="Collect", perspective="Top-down", core_loop="Collect",
        mechanics=["Collect coins"], controls=["WASD"], win_lose="Collect all", levels=["Arena"], ui_screens=["HUD"],
        art=ArtDirection(style="Primitives"), vfx=[], animation=[], audio=["UNRELATED_AUDIO_DETAILS"*100])


class EfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.cfg=Config()
        self.project=UnityProject(self.cfg,self.root/'Game',self.root/'state')

    def test_old_designs_remain_compatible(self):
        self.assertEqual(design().scope,"vertical_slice")
        self.assertEqual(design().progression,[])

    def test_role_context_omits_unrelated_audio(self):
        self.assertNotIn('UNRELATED_AUDIO_DETAILS',_design_summary(design(),'gameplay'))
        self.assertIn('UNRELATED_AUDIO_DETAILS',_design_summary(design(),'audio'))

    def test_api_index_selects_files_and_obeys_budget(self):
        self.project.write_file('Assets/Useful.cs','public class Useful {}')
        self.project.write_file('Assets/Unrelated.cs','public class Unrelated {}')
        index=ProjectContext(self.project,1000).api_index(['Assets/Useful.cs'],budget=100)
        self.assertIn('Useful',index); self.assertNotIn('Unrelated',index); self.assertLessEqual(len(index),100)

    def test_small_contracted_task_needs_no_ai_brief(self):
        self.project.write_file('Assets/Core.cs','public class Core {}')
        task=TaskSpec(id='two',role='gameplay',title='Coins',description='Collect',outputs=['Assets/Coins.cs'],depends_on=['one'])
        dependency=TaskSpec(id='one',role='gameplay',title='Core',description='Core',outputs=['Assets/Core.cs'],status='done')
        plan=Plan(architecture=Architecture(root_namespace='Game',overview='Arcade',systems=[]),assets=[],tasks=[dependency,task],
            contracts=[CodeContract(path='Assets/Core.cs',owner='gameplay',public_api=['void StartRun()'])])
        llm=Mock(); studio=Studio(self.cfg,llm,self.project)
        brief=studio.brief(task,design(),plan,'none',[dependency])
        llm.structured.assert_not_called()
        self.assertIn('StartRun',brief.prompt); self.assertIn('Assets/Core.cs',brief.relevant_files)

    def test_procedural_design_cannot_request_downloads(self):
        data=design(); data.asset_policy='procedural_only'
        plan=Plan(architecture=Architecture(root_namespace='Game',overview='Arcade',systems=[]),
                  tasks=[],assets=[AssetRequest(key='unwanted',query='barrel')])
        llm=Mock(); llm.structured.return_value=plan
        self.assertEqual(Studio(self.cfg,llm,self.project).plan(data).assets,[])

    def test_metrics_distinguish_cache_hits_from_requests(self):
        llm=LLM(self.cfg); llm.set_log_dir(self.root/'llm_logs'); llm.backend=Mock()
        llm.backend.call.return_value='answer'
        for _ in range(2): llm.complete(model='',instructions='role',prompt='task')
        result=summarize(self.root/'metrics.jsonl')
        self.assertEqual(result['model_requests'],1); self.assertEqual(result['cache_hits'],1)

    def test_budget_pause_preserves_request_and_allows_cached_answers(self):
        self.cfg.max_llm_calls=1
        llm=LLM(self.cfg); llm.set_log_dir(self.root/'llm_logs'); llm.backend=Mock()
        llm.backend.call.return_value='answer'
        llm.complete(model='',instructions='role',prompt='first')
        with self.assertRaises(BudgetReached): llm.complete(model='',instructions='role',prompt='second')
        self.assertEqual(json.loads((self.root/'pending_call.json').read_text())['status'],'budget_paused')
        self.assertEqual(llm.complete(model='',instructions='role',prompt='first'),'answer')
        self.assertEqual(llm.backend.call.call_count,1)

    def test_exact_patch_is_safe_and_replayable(self):
        orch=Orchestrator(self.cfg,self.root/'Game')
        orch.project.write_file('Assets/Coin.cs','old code')
        change=AgentOutput(patches=[FilePatch(path='Assets/Coin.cs',old='old code',new='new code')])
        orch._apply(change); orch._apply(change)
        self.assertEqual(orch.project.read_file('Assets/Coin.cs'),'new code')

    def test_stale_patch_does_not_apply_other_files(self):
        orch=Orchestrator(self.cfg,self.root/'Game')
        orch.project.write_file('Assets/Coin.cs','user changes')
        with self.assertRaises(ValueError):
            orch._apply(AgentOutput(files=[FileWrite(path='Assets/New.cs',content='new')],
                patches=[FilePatch(path='Assets/Coin.cs',old='stale',new='fixed')]))
        self.assertIsNone(orch.project.read_file('Assets/New.cs'))
        self.assertEqual(orch.project.read_file('Assets/Coin.cs'),'user changes')

    def test_repeated_compile_errors_stop_spending_calls(self):
        orch=Orchestrator(self.cfg,self.root/'Game')
        result=UnityResult(1,'',compile_errors=[dict(file='Assets/A.cs',line='1',col='1',code='CS1002',msg='expected semicolon')])
        orch.project.compile=Mock(return_value=result); orch.studio.fix=Mock(return_value=AgentOutput())
        with self.assertRaisesRegex(RuntimeError,'same compiler errors'): orch._compile_fix_loop()
        orch.studio.fix.assert_called_once()

    def test_missing_unity_test_results_are_not_success(self):
        self.project.runner.run=Mock(return_value=UnityResult(0,''))
        self.assertFalse(self.project.run_tests('PlayMode')[0])

    def test_zero_unity_tests_are_not_success(self):
        def run(*args,**kwargs):
            path=self.project.state_dir/'unity_logs/tests_PlayMode.xml'
            path.write_text('<test-run result="Passed" total="0"/>')
            return UnityResult(0,'')
        self.project.runner.run=Mock(side_effect=run)
        self.assertFalse(self.project.run_tests('PlayMode')[0])

    def task_setup(self):
        orch=Orchestrator(self.cfg,self.root/'Game')
        task=TaskSpec(id='two',role='gameplay',title='Feature',description='Implement',outputs=['Assets/Feature.cs'],depends_on=['one'])
        dependency=TaskSpec(id='one',role='gameplay',title='Core',description='Core',outputs=['Assets/Core.cs'],status='done')
        orch.state.plan=Plan(architecture=Architecture(root_namespace='Game',overview='Arcade',systems=[]),assets=[],tasks=[dependency,task])
        orch.state.design=design(); orch.assets=Mock(); orch.assets.describe_for_agents.return_value='none'
        orch.project.write_file('Assets/Core.cs','core v1')
        orch.studio.brief=Mock(return_value=Brief(prompt='Implement',relevant_files=['Assets/Core.cs']))
        orch.studio.execute=Mock(return_value=AgentOutput(files=[FileWrite(path='Assets/Feature.cs',content='generated')]))
        return orch,task

    def test_changed_dependency_invalidates_task_journal(self):
        orch,task=self.task_setup(); orch._do_task(task)
        task.status='pending'; orch.project.write_file('Assets/Core.cs','core v2')
        orch.studio.execute.return_value=AgentOutput(files=[FileWrite(path='Assets/Feature.cs',content='updated')])
        orch._do_task(task)
        self.assertEqual(orch.studio.execute.call_count,2)
        self.assertEqual(orch.project.read_file('Assets/Feature.cs'),'updated')

    def test_changed_task_invalidates_journal(self):
        orch,task=self.task_setup(); orch._do_task(task)
        task.status='pending'; task.description='New requirement'
        orch._do_task(task)
        self.assertEqual(orch.studio.execute.call_count,2)

    def test_partial_patch_application_replays_without_new_model_call(self):
        orch,task=self.task_setup(); orch.project.write_file('Assets/Feature.cs','old')
        orch.studio.execute.return_value=AgentOutput(patches=[FilePatch(path='Assets/Feature.cs',old='old',new='new')])
        apply=orch._apply
        def interrupted(output):
            apply(output)
            raise RuntimeError('Interrupted after file write')
        orch._apply=interrupted
        with self.assertRaises(RuntimeError): orch._do_task(task)
        orch._apply=apply; orch._do_task(task)
        self.assertEqual(orch.studio.execute.call_count,1)
        self.assertEqual(orch.project.read_file('Assets/Feature.cs'),'new')

    def test_user_changes_during_generation_are_preserved(self):
        orch,task=self.task_setup(); orch.project.write_file('Assets/Feature.cs','original')
        def generate(*args):
            orch.project.write_file('Assets/Feature.cs','user edit')
            return AgentOutput(files=[FileWrite(path='Assets/Feature.cs',content='generated')])
        orch.studio.execute.side_effect=generate
        with self.assertRaisesRegex(RuntimeError,'changed while'): orch._do_task(task)
        self.assertEqual(orch.project.read_file('Assets/Feature.cs'),'user edit')

    def test_package_errors_do_not_spend_ai_repair_calls(self):
        orch=Orchestrator(self.cfg,self.root/'Game')
        orch.project.compile=Mock(return_value=UnityResult(1,'',compile_errors=[
            dict(file='Library/PackageCache/package/Old.cs',line='1',col='1',code='CS0619',msg='obsolete')]))
        orch.project.repair_package_compatibility=Mock(return_value=False)
        orch.studio.fix=Mock()
        with self.assertRaisesRegex(RuntimeError,'package compatibility'): orch._compile_fix_loop()
        orch.studio.fix.assert_not_called()

    def test_cycle_is_rejected_during_plan_validation(self):
        tasks=[TaskSpec(id='a',role='gameplay',title='A',description='A',depends_on=['b']),
               TaskSpec(id='b',role='gameplay',title='B',description='B',depends_on=['a'])]
        with self.assertRaises(ValueError):
            Plan(architecture=Architecture(root_namespace='Game',overview='Game',systems=[]),assets=[],tasks=tasks)

    def test_campaign_scope_requires_real_distinct_stages(self):
        data=design().model_dump(mode='json'); data['scope']='campaign'
        with self.assertRaises(ValueError): GameDesign.model_validate(data)

    def test_exported_documents_keep_progression_and_contracts(self):
        doc=design_md({'title':'Runner','scope':'campaign','progression':[
            {'id':'stage-1','title':'First Run','objective':'Reach exit','new_challenge':'Gaps','difficulty':'Easy','unlock_rule':'Start'}]})
        self.assertIn('Reach exit',doc); self.assertIn('Gaps',doc)
        plan=plan_md({'contracts':[{'path':'Assets/Runner.cs','owner':'gameplay','public_api':['void Jump()']}]})
        self.assertIn('void Jump()',plan)

    def test_protected_integration_error_does_not_call_coder(self):
        orch=Orchestrator(self.cfg,self.root/'Game')
        orch.project.compile=Mock(return_value=UnityResult(1,'',compile_errors=[
            dict(file='Assets/LudoFabricStarter/Runtime/Shared.cs',line='1',col='1',code='CS1002',msg='syntax')]))
        orch.studio.fix=Mock()
        with self.assertRaisesRegex(RuntimeError,'Protected LudoFabric'): orch._compile_fix_loop()
        orch.studio.fix.assert_not_called()

    def test_invalid_patch_is_corrected_before_any_file_is_applied(self):
        self.project.write_file('Assets/Core.cs','old')
        llm=LLM(self.cfg)
        llm.complete=Mock(side_effect=[
            json.dumps({'patches':[{'path':'Assets/Core.cs','old':'wrong','new':'new'}]}),
            json.dumps({'patches':[{'path':'Assets/Core.cs','old':'old','new':'new'}]})])
        studio=Studio(self.cfg,llm,self.project)
        result=llm.structured(AgentOutput,model='',instructions='role',prompt='task',
            validate=lambda out: studio._validate_output(out,{'Assets/Core.cs':'old'}))
        self.assertEqual(llm.complete.call_count,2)
        self.assertEqual(result.patches[0].old,'old')
        self.assertEqual(self.project.read_file('Assets/Core.cs'),'old')

    def test_reported_api_tokens_are_measured_without_estimation(self):
        llm=LLM(self.cfg); llm.set_log_dir(self.root/'llm_logs'); llm.backend=Mock()
        llm.backend.call.return_value='answer'; llm.backend.last_usage={'input_tokens':100,'output_tokens':20}
        llm.complete(model='',instructions='role',prompt='task')
        result=summarize(self.root/'metrics.jsonl')
        self.assertEqual(result['reported_input_tokens'],100)
        self.assertEqual(result['reported_output_tokens'],20)
        self.assertEqual(result['requests_with_token_usage'],1)


if __name__=='__main__': unittest.main()
