"""Create reversible second-step examples; never edits the source projects."""
import base64
import copy
import json
import sys
import uuid
from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.core import Project,png_bytes,sha,atomic_json,decode_files
from app.rig import RigProject

def main():
    evidence=ROOT/'evidence/rerig';evidence.mkdir(parents=True,exist_ok=True)
    pointer=evidence/'examples.json'
    if pointer.exists():
        print(pointer.read_text());return
    records=[]
    for source_id,kind in [('b0ec016ff71e4914ad4eb58264a9bdd1','robot42'),('e97b3c3eb3894d498300639a6e272918','mesh38')]:
        source=Project(ROOT/'storage/projects'/source_id);before={p['file']:sha(source.pages_data[p['file']]) for p in source.pages};revision=source.revision
        rig_id=uuid.uuid4().hex;rig=RigProject.create(ROOT/'storage/rigs'/rig_id,source)
        label='机器人 · 新骨架与肩甲示例' if kind=='robot42' else 'zhujue · 新骨架与贴图示例'
        rig.meta['name']=label
        def edit(action,**kw):rig.mutate(action,dict(revision=rig.meta['revision'],**kw))
        edit('add-bone',name='body-control',parent='root',x=0,y=0)
        if kind=='robot42':
            edit('bone',bone='hip',name='hip',parent='body-control',scaleX=1.06,scaleY=1.16)
            edit('recolor',part='r1',hue=55)
            im=Image.new('RGBA',(60,36));draw=ImageDraw.Draw(im);draw.rounded_rectangle((1,1,58,34),radius=10,fill='#c1dae2',outline='#264255',width=3);draw.line((10,12,46,12),fill='#3e6990',width=3)
            edit('add-part',name='new-shoulder-armor',bone='right-upper-arm',x=0,y=-8,image=base64.b64encode(png_bytes(im)).decode())
        else:
            edit('bone',bone='bone',name='character-control',parent='body-control')
            edit('bone',bone='root',name='root',scaleX=1.04,scaleY=1.04)
            edit('recolor',part='r1',hue=20)
        edit('save',label='完成骨架与贴图示例')
        dest=evidence/kind;dest.mkdir(exist_ok=True)
        # Separate original/edited resources for direct official-runtime verification.
        original=dest/'before';original.mkdir(exist_ok=True)
        for name in [source.skeleton,source.atlas,*source.pages_data]:
            path=original/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(source.pages_data.get(name,source.files[name]))
        output=dest/'after';output.mkdir(exist_ok=True)
        files,_=rig.files()
        for name,data in files.items():(output/name).write_bytes(data)
        (dest/'export.zip').write_bytes(rig.export());atomic_json(dest/'validation.json',rig.validate())
        after={p['file']:sha(source.pages_data[p['file']]) for p in source.pages}
        assert before==after and source.revision==revision
        records.append({'kind':kind,'id':rig_id,'name':label,'source_id':source_id,'source_revision':revision,'source_unchanged':True,'runtime':source.runtime,'before_skeleton':source.skeleton,'before_atlas':source.atlas,'revision':rig.meta['revision']})
    # Independent 3.8 region fixture, without generating another user-visible project.
    fixture=decode_files([{'name':'robot38.zip','data':base64.b64encode((ROOT/'tests/fixtures/robot38.zip').read_bytes()).decode()}])
    source=Project(evidence/'region38-source',fixture,'3.8 region verification');rig=RigProject.create(evidence/'region38-rig',source)
    for name,data in rig.files()[0].items():(rig.directory/name).write_bytes(data)
    atomic_json(pointer,records);print(json.dumps(records,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
