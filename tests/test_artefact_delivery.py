"""Real authenticated HTTP delivery tests using registered synthetic outputs."""
import contextlib
import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from tools import starter,lifecycle,workspace,workspace_server,artefact_delivery


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        parent=starter.ROOT/'.test-output';parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='delivery-',dir=parent);self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root=Path(starter.init_project(Path(self.temp.name),'example','Delivery fixture','product',profile='api'))
        self.server=workspace_server.make_server(self.root,port=0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)

    def register(self,path,data):
        target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        history=lifecycle.events(self.root)
        lifecycle.apply_patch(self.root,{'expected_revision':history[-1]['revision'] if history else None,'tasks':[
            {'role':'technical-writer','owner':'Synthetic writer','status':'done','next_action':'Deliver output','evidence_refs':path}]})
        return 'file:'+path,hashlib.sha256(data).hexdigest()

    def request(self,identifier,checksum,authorized=True,origin=None):
        headers={'Authorization':'Bearer '+self.server.human_token} if authorized else {}
        if origin:headers['Origin']=origin
        url=self.server.origin+'/api/artefact?'+urlencode({'id':identifier,'sha256':checksum})
        try:
            with urlopen(Request(url,headers=headers),timeout=8) as response:return response.status,response.headers,response.read()
        except HTTPError as error:return error.code,error.headers,error.read()

    def test_registered_file_download_uses_hash_auth_and_safe_filename(self):
        identifier,checksum=self.register('reports/결과.md','실제 파일 수령'.encode())
        status,headers,data=self.request(identifier,checksum)
        self.assertEqual(status,200,data);self.assertEqual(data,'실제 파일 수령'.encode())
        self.assertEqual(headers['X-Content-SHA256'],checksum)
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertIn("filename*=UTF-8",headers['Content-Disposition'])
        self.assertEqual(self.request(identifier,checksum,authorized=False)[0],403)
        self.assertEqual(self.request(identifier,checksum,origin='https://outside.invalid')[0],403)

    def test_stale_hash_and_unregistered_paths_are_not_served(self):
        identifier,checksum=self.register('reports/result.md',b'Synthetic output')
        self.assertEqual(self.request(identifier,'0'*64)[0],409)
        self.assertEqual(self.request('file:../outside.txt',checksum)[0],404)
        (self.root/'reports/result.md').write_bytes(b'Changed while observer was open')
        self.assertEqual(self.request(identifier,checksum)[0],409)

    def test_active_html_and_spoofed_images_are_download_only(self):
        for path in ['reports/output.html','reports/spoof.png','reports/image.svg']:
            identifier,checksum=self.register(path,b'<html><script>window.synthetic=true</script></html>')
            status,headers,_=self.request(identifier,checksum)
            self.assertEqual(status,200)
            self.assertEqual(headers['Content-Type'],'application/octet-stream')
            self.assertIn('sandbox',headers['Content-Security-Policy'])

    def test_sensitive_json_content_is_blocked_even_under_a_regular_name(self):
        identifier,checksum=self.register('reports/output.json',b'{"password":"REALISTIC_PRIVATE_SENTINEL_873523"}')
        status,_,body=self.request(identifier,checksum)
        self.assertEqual(status,403,body)
        self.assertNotIn(b'REALISTIC_PRIVATE_SENTINEL',body)

    def test_file_size_is_bounded_before_delivery_read(self):
        identifier,checksum=self.register('reports/large.bin',b'initial')
        target=self.root/'reports/large.bin'
        with target.open('wb') as handle:handle.truncate(artefact_delivery.MAX_BYTES+1)
        checksum=workspace.file_hash(self.root,'reports/large.bin')
        self.assertEqual(self.request(identifier,checksum)[0],413)
