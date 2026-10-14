import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from app.core import png_bytes, sha
from app.provider import Matrix
from app.reference_policy import publication_authorization


class PublicationPolicyTests(unittest.TestCase):
    def test_installation_consent_can_be_revoked_and_is_not_inherited(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            policy = Path(one) / 'reference-publication-policy.json'
            self.assertFalse(publication_authorization(one)['authorized'])
            record = dict(authorized=True, scope='submitted-task-images', provider='quick-tunnel',
                          purpose='matrix-reference-generation', max_url_ttl=3600)
            policy.write_text(json.dumps(record))
            self.assertFalse(publication_authorization(one)['ask_per_image'])
            self.assertFalse(publication_authorization(two)['authorized'])
            for change in ({'authorized': False}, {'scope': 'all-local-files'}, {'max_url_ttl': 86400}):
                policy.write_text(json.dumps({**record, **change}))
                self.assertFalse(publication_authorization(one)['authorized'])
            policy.write_text('[]')
            self.assertFalse(publication_authorization(one)['authorized'])

    def test_publication_expiry_is_capped_by_consent(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / 'reference-publication-policy.json'
            p.write_text(json.dumps(dict(authorized=True, scope='submitted-task-images',
                provider='quick-tunnel', purpose='matrix-reference-generation', max_url_ttl=3600)))
            matrix = Matrix(root)
            try:
                matrix.tunnel.enable(86400)
                matrix.tunnel.local._hostname = 'test.trycloudflare.com'
                data = png_bytes(Image.new('RGBA', (2, 2), (10, 20, 30, 255)))
                with patch.object(matrix.tunnel.local, '_ensure_started'):
                    before = time.time()
                    result = matrix.tunnel.local.publish(data, sha(data))
                self.assertLessEqual(result['expires_at'], time.time() + 3600)
                self.assertGreaterEqual(result['expires_at'], before + 3599)
                self.assertTrue(matrix.reference_status()['publication_authorization']['authorized'])
            finally:
                matrix.tunnel.close()


if __name__ == '__main__':
    unittest.main()
