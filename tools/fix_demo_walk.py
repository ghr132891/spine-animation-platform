"""Create a corrected built-in demo project, preserving current reskin pixels.

Never change the immutable source files of an imported/user-edited project.
"""
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.core import Project

SOURCE_ID='5544baa6b28349f0a77a3da3c3cdb050'
source=Project(ROOT/'storage/projects'/SOURCE_ID)
old=json.loads(source.files['robot.json'])
fixed=json.loads((ROOT/'demo/robot.json').read_text())
assert old['bones']==fixed['bones'] and old['slots']==fixed['slots'] and old['skins']==fixed['skins']
assert old['animations']['idle']==fixed['animations']['idle']
# Preserve all original bytes except this intentionally repaired sample animation.
old['animations']['walk']=fixed['animations']['walk']
files=dict(source.files)
files['robot.json']=(json.dumps(old,indent=2)+'\n').encode()
target_id=uuid.uuid4().hex
target=Project(ROOT/'storage/projects'/target_id,files,'快递机器人 · 行走修正版')
target.pages_data=dict(source.pages_data)
target.commit()
if (source.directory/'jobs').exists():shutil.copytree(source.directory/'jobs',target.directory/'jobs')
report={'source_project':source.id,'source_revision':source.revision,'corrected_project':target.id,'bones_unchanged':old['bones']==fixed['bones'],'reskin_pages_preserved':source.pages_data==target.pages_data,'validation':target.validate()}
(ROOT/'evidence/walk-fix.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
print(json.dumps({k:v for k,v in report.items() if k!='validation'},ensure_ascii=False))
