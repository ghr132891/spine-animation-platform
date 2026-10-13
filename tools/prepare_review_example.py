"""Build isolated review examples from the current guide, without publishing."""
import copy,io,json,shutil,sys,uuid,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.core import atomic_json,png_bytes
folder=ROOT/'evidence/assembly-review';folder.mkdir(exist_ok=True)
source=CharacterProject(ROOT/'storage/characters/76a6aa6972a4466a976f798b115bac65');storage=folder/'qa-storage';c=CharacterProject.create(storage,'组装校准独立验收')
meta=copy.deepcopy(source.meta);meta.update(id=c.id,name='组装校准独立验收',revision=0,versions=[],part_review={'adjustments':{},'draw_order':[]});atomic_json(c.directory/'character.json',meta)
for name in ('images','part-images'):shutil.copytree(source.directory/name,c.directory/name,dirs_exist_ok=True)
c=CharacterProject(c.directory)
def build(label):
    v=c.build(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'label':label,**{k:c.meta[k] for k in ('landmarks','settings','masks')}})['versions'][-1]
    out=folder/label;out.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(c.export(storage,v['id']))) as z:z.extractall(out)
    return v
baseline=build('baseline');order=c.review()['draw_order'];order.remove('back');order.insert(2,'back')
c.update_review({'revision':c.meta['revision'],'part_review':{'adjustments':{'l-upper-arm':{'dx':8,'dy':2,'rotation':3,'scale':1.02},'back':{'dx':-6,'dy':0,'rotation':0,'scale':1}},'draw_order':order}})
adjusted=build('adjusted');(folder/'source.png').write_bytes(png_bytes(source.image()));(folder/'assembly.png').write_bytes(png_bytes(c.assembly()));atomic_json(folder/'review.json',c.review());atomic_json(folder/'example.json',{'character':c.id,'source_revision':source.meta['revision'],'baseline':baseline,'adjusted':adjusted,'user_project_unchanged':True,'ai_submissions':0})
print(json.dumps({'isolated_character':c.id,'parts':len(adjusted['parts']),'ai_submissions':0}))
