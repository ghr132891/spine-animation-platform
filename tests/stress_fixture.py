"""Synthetic evidence for unit tests of unrelated transitions; not production QA.
Runtime rendering and gate behavior are covered separately in test_assembly_stress.
"""
from app.assembly_stress import signature,CLIPS,CHECKS,review
from app.core import png_bytes,sha,atomic_json
from PIL import Image

def accept_synthetic_stress(c):
 digest=signature(c);folder=c.directory/'stress-cache'/digest;folder.mkdir(parents=True,exist_ok=True);panels=[];raw=png_bytes(Image.new('RGBA',(8,8),'navy'))
 for clip in CLIPS:
  for anchor in (None,'__hip__','__straps__'):
   key='p'+str(len(panels));(folder/(key+'.png')).write_bytes(raw);panels.append({'key':key,'file':key+'.png','animation':clip,'anchor':anchor,'frames':8,'sha256':sha(raw),'runtime_passed':True})
 atomic_json(folder/'manifest.json',{'digest':digest,'panels':panels,'visual_verdict':'not_reviewed','checks':list(CHECKS)})
 review(c,{'revision':c.meta['revision'],'digest':digest,'checks':dict.fromkeys(CHECKS,True),'verdict':'ready','reason':'Synthetic fixture for unit tests; renderer is mocked, not a production art approval.'})
