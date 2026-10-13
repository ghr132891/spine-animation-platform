"""Read-only contact routing must neither mutate a rig nor invoke generation."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from app import server


class ContactRoutingTests(unittest.TestCase):
    def setUp(self):
        self.handler=object.__new__(server.Handler)
        self.handler.headers={};self.handler.send=lambda value,*args,**kwargs:value
        self.report={'status':'not_assessed','reason':'explicit mapping required'}
        self.tasks=SimpleNamespace(authorized=Mock(return_value=True),contact_target=Mock(return_value=self.report))

    def test_direct_agent_route_is_authorized_and_readonly(self):
        self.handler.path='/api/automation/agent/contact'
        request={'kind':'rig','id':'a'*32,'animation':'walk','options':{'samples':128}}
        with patch.object(server,'AGENT_TASKS',self.tasks),patch.object(server.MATRIX,'generate') as generate:
            self.assertEqual(self.handler.post(request),self.report)
        self.tasks.contact_target.assert_called_once_with('rig','a'*32,request)
        generate.assert_not_called()

    def test_protected_agent_contact_still_requires_authorization(self):
        self.handler.path='/api/automation/agent/contact';self.tasks.authorized.return_value=False
        with patch.object(server,'AGENT_TASKS',self.tasks):
            response=self.handler.post({'kind':'rig','id':'a'*32})
        self.assertIn('error',response);self.tasks.contact_target.assert_not_called()

    def test_workbench_routes_bypass_mutation(self):
        entity=SimpleNamespace(id='a'*32,mutate=Mock())
        for kind,collection,loader in [('rig','rigs','get_rig'),('project','projects','get_project')]:
            self.handler.path='/api/'+collection+'/'+entity.id+'/contact';request={'animation':'walk'}
            with patch.object(server,'AGENT_TASKS',self.tasks),patch.object(server,loader,return_value=entity),patch.object(server.MATRIX,'generate') as generate:
                self.assertEqual(self.handler.post(request),self.report)
            self.tasks.contact_target.assert_called_with(kind,entity.id,request)
            generate.assert_not_called()
        entity.mutate.assert_not_called()


if __name__=='__main__':unittest.main()
