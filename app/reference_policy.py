"""Installation-local publication consent, never shipped with client packages."""
import json
from pathlib import Path


def publication_authorization(storage):
    try:
        record = json.loads((Path(storage) / 'reference-publication-policy.json').read_text())
    except (OSError, ValueError):
        record = {}
    allowed = (isinstance(record, dict) and record.get('authorized') is True
               and record.get('scope') == 'submitted-task-images'
               and record.get('provider') == 'quick-tunnel'
               and record.get('purpose') == 'matrix-reference-generation'
               and type(record.get('max_url_ttl')) is int
               and record['max_url_ttl'] == 3600)
    return {'authorized': allowed, 'scope': 'submitted-task-images',
            'provider': 'quick-tunnel', 'purpose': 'matrix-reference-generation',
            'max_url_ttl': 3600, 'ask_per_image': not allowed,
            'public_link': True}
