"""Immutable assembly provenance for comparing static and animated versions."""
import json
from .core import sha, png_bytes


def snapshot(character_id, meta, landmarks, settings, masks, review, parts):
    payload={'image_id':meta['image_id'],'landmarks':landmarks,'settings':settings,
             'masks':masks,'part_layers':meta.get('part_layers',{}),'review':review,
             'parts':[{'name':p['name'],'sha256':sha(png_bytes(p['image']))} for p in parts]}
    return {'character_id':character_id,'character_revision':meta['revision'],
            'source_image_id':meta['image_id'],
            'snapshot_sha256':sha(json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False).encode())}
