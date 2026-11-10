import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from workflows import Context, WorkflowError, audit_evidence, instance_reset_preview, public_state, reset_instance_organizations


class EnterpriseChecks(unittest.TestCase):
    def context(self, directory):
        return Context(directory, '/not-read/org.env', 'test', emit=lambda event: None)

    def test_native_denial_cannot_pass_on_success_or_auth_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            env = Path(directory) / 'persona.env'
            env.touch()
            persona = {'envFile': str(env)}
            for status in (200, 401, 500):
                with self.subTest(status=status), patch('workflows.subprocess.run', return_value=Mock(stdout=json.dumps({'status': status, 'body': {}}))):
                    with self.assertRaises(WorkflowError):
                        ctx.native('Expected denial', 'prompts.setLabels', {'projectId':'demo'}, persona, expected=(403,))
            with patch('workflows.subprocess.run', return_value=Mock(stdout='{"status":403,"body":{}}')) as run:
                ctx.native('Expected denial', 'prompts.setLabels', {'projectId':'demo'}, persona, expected=(403,))
                self.assertIn('--env-file='+str(env), run.call_args.args[0])
                self.assertNotIn('password', run.call_args.kwargs['input'])
                self.assertNotIn('ADMIN_API_KEY', run.call_args.kwargs['env'])

    def test_browser_projection_omits_persona_and_org_credential_paths(self):
        state={'applications':{},'governance':{'projectId':'g','personas':{'ADMIN':{'envFile':'/secret/admin.env','password':'hidden'}}},
               'organizations':{'o':{'id':'o','name':'Demo','envFile':'/secret/org.env','keyId':'k','presenterId':'u'}}}
        public=json.dumps(public_state(state))
        for private in ('/secret','password','hidden','presenterId','keyId'):
            self.assertNotIn(private, public)
        self.assertIn('Demo', public)

    def fixture(self, directory, extra=None):
        ctx=self.context(directory)
        ctx.state['organizations']={'o':{'id':'o','name':'Sandbox','keyId':'key','envFile':'/not-read/key.env','projectId':'p','presenterId':'presenter'}}
        current={'id':'o','metadata':{'demo':'langfuse-operations'},'projects':[{'id':'p','name':'Starter','metadata':{'demo':'langfuse-operations'}}]}
        ctx.instance=Mock(side_effect=lambda label,method,path,**kw: {'apiKeys':[{'id':'key'},*([{'id':'extra'}] if extra=='key' else [])]} if path.endswith('/apiKeys') else current)
        ctx.call=Mock(return_value={'memberships':[{'userId':'presenter'},*([{'userId':'other'}] if extra=='member' else [])]})
        return ctx,current

    def test_extra_org_resources_or_missing_marker_block_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            for extra in ('key','member','project','marker'):
                ctx,current=self.fixture(directory,extra)
                if extra=='project':current['projects'].append({'id':'untracked','name':'Other'})
                if extra=='marker':current['metadata']={}
                rows=instance_reset_preview(ctx)
                self.assertTrue(rows[0]['blocked'],extra)
                self.assertFalse(any(call.args[1]=='DELETE' for call in ctx.instance.call_args_list))

    def test_stale_org_preview_stops_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx,current=self.fixture(directory)
            rows=instance_reset_preview(ctx)
            current['projects'].append({'id':'untracked','name':'Other'})
            with self.assertRaisesRegex(WorkflowError,'scope changed'):
                reset_instance_organizations(ctx,rows)
            self.assertFalse(any(call.args[1]=='DELETE' for call in ctx.instance.call_args_list))

    def test_failed_audit_proof_still_restores_member(self):
        ctx=Mock()
        ctx.state={'users':{'member@example.invalid':{'id':'member'}}}
        app={'id':'p'}
        governance={'personas':{'ADMIN':{},'MEMBER':{'email':'member@example.invalid'}}}
        ctx.native.return_value={'data':[]}
        with patch('workflows.prepare_governance',return_value=(app,governance)):
            with self.assertRaisesRegex(WorkflowError,'not found'):
                audit_evidence(ctx,{})
        self.assertEqual([call.kwargs['body']['role'] for call in ctx.call.call_args_list],['VIEWER','MEMBER'])

    def test_old_audit_record_cannot_prove_a_new_run(self):
        ctx=Mock()
        ctx.state={'users':{'member@example.invalid':{'id':'member'}}}
        ctx.native.return_value={'data':[{'resourceType':'projectMembership','createdAt':'2020-01-01T00:00:00Z','before':'{"role":"MEMBER"}','after':'{"role":"VIEWER","userId":"member"}'}]}
        with patch('workflows.prepare_governance',return_value=({'id':'p'},{'personas':{'ADMIN':{},'MEMBER':{'email':'member@example.invalid'}}})):
            with self.assertRaises(WorkflowError):audit_evidence(ctx,{})
        self.assertEqual(ctx.call.call_args.kwargs['body']['role'],'MEMBER')

class OrganizationCleanupChecks(unittest.TestCase):
    def test_async_purge_preserves_org_record_and_key_until_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=Context(directory,'/not-read/org.env','test',emit=lambda e:None)
            org={'id':'o','name':'Sandbox','projectId':'p','envFile':'/not-read/key.env'}
            ctx.state['organizations']={'o':org}
            row={'id':'o','name':'Sandbox','present':True,'blocked':False,'reason':'','projects':[]}
            current={'id':'o','metadata':{'demo':'langfuse-operations'},'projects':[]}
            ctx.instance=Mock(side_effect=[current,current,{'error':'Cannot delete organization with existing projects','message':'Deletion of your projects is still being processed, please try deleting the organization later'}])
            with patch('workflows.instance_reset_preview',return_value=[row]),patch('workflows.remove_project_key_files') as cleanup:
                self.assertEqual(reset_instance_organizations(ctx,[row]),['o'])
                cleanup.assert_not_called()
            self.assertEqual(ctx.state['organizations']['o']['resetStatus'],'deletion_requested')
            ctx.instance=Mock(side_effect=[current,current,{'success':True},{}])
            with patch('workflows.instance_reset_preview',return_value=[row]),patch('workflows.remove_project_key_files') as cleanup:
                self.assertEqual(reset_instance_organizations(ctx,[row]),[])
                cleanup.assert_called_once_with(ctx,'o')
            self.assertNotIn('o',ctx.state['organizations'])

    def test_unexpected_org_delete_error_does_not_clear_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=Context(directory,'/not-read/org.env','test',emit=lambda e:None)
            ctx.state['organizations']={'o':{'id':'o','name':'Sandbox'}}
            row={'id':'o','name':'Sandbox','present':True,'blocked':False,'reason':'','projects':[]}
            current={'metadata':{'demo':'langfuse-operations'},'projects':[]}
            ctx.instance=Mock(side_effect=[current,current,{'error':'Some other failure'}])
            with patch('workflows.instance_reset_preview',return_value=[row]):
                with self.assertRaises(WorkflowError):reset_instance_organizations(ctx,[row])
            self.assertIn('o',ctx.state['organizations'])
