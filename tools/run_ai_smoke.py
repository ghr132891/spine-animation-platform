"""One bounded, resumable Matrix image-edit test. Never prints credentials."""
import argparse,base64,json,sys
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.core import Project,png_bytes,UserError
from app.provider import Matrix,DEFAULT_MODEL

parser=argparse.ArgumentParser();parser.add_argument('action',choices=['submit','poll']);parser.add_argument('--model',default=DEFAULT_MODEL,choices=[DEFAULT_MODEL,'nano-banana-2','gpt-image-2']);parser.add_argument('--reference-url',help='Flare requires a public HTTPS reference image');args=parser.parse_args()
record=ROOT/'evidence'/('ai-flare-job.json' if args.model==DEFAULT_MODEL else 'ai-smoke-job.json' if args.model=='nano-banana-2' else 'ai-gpt-job.json');matrix=Matrix(ROOT/'storage')
if args.action=='submit':
    if record.exists():
        print('Existing submission record; use poll, not a repeated POST.');sys.exit(0)
    project=Project(ROOT/'storage'/'smoke-fixture',{p.name:p.read_bytes() for p in (ROOT/'demo').iterdir() if p.is_file()},'AI smoke')
    r=next(r for r in project.regions if r['name']=='torso');part=project.part(r['id'],True)
    im=Image.new('RGB',(512,512),'#eeeeee');scaled=part.resize((324,375),Image.Resampling.LANCZOS);im.paste(scaled,(94,68),scaled)
    reference=png_bytes(im);(ROOT/'evidence/ai-reference.png').write_bytes(reference)
    payload={'input':{'prompt':'Edit only the robot torso shell in this image. Keep its exact silhouette, exact position, scale, orientation, buttons and display placement. Change the turquoise shell into deep midnight blue ceramic-metal armor with elegant thin silver seams, small orange accents, subtle brushed metal surface. Same isolated part, same light gray background. No head, no limbs, no extra objects. Keep crisp 2D game art, no shadows on the background.','ref_images':['data:image/png;base64,'+base64.b64encode(reference).decode()],'aspect_ratio':'1:1','resolution':'1K'},'request_num':1}
    if args.model in ('gpt-image-2',DEFAULT_MODEL):
        payload['input']['input_images']=payload['input'].pop('ref_images')
        payload['input'].update(quality='high',output_format='png')
        payload['input']['input_images']=[matrix.reference_url(args.reference_url or '')]
    record.write_text(json.dumps({'state':'submission_unknown','model':args.model}))
    try:
        result=matrix.request('/models/'+args.model+'/generate',payload)
        result['model']=args.model;record.write_text(json.dumps(result,indent=2,ensure_ascii=False));print(json.dumps({'model':args.model,'gen_id':(result.get('data') or {}).get('gen_id')}))
    except UserError as e:
        record.write_text(json.dumps({'state':'rejected_or_unknown','error':str(e)}));print(str(e))
else:
    job=json.loads(record.read_text());gen_id=(job.get('data') or {}).get('gen_id')
    if not gen_id:print(job);sys.exit(1)
    response=matrix.request('/models/'+job.get('model',args.model)+'/'+str(gen_id)+'/status');(ROOT/'evidence'/('ai-smoke-status.json' if args.model=='nano-banana-2' else 'ai-gpt-status.json')).write_text(json.dumps(response,indent=2,ensure_ascii=False))
    d=response.get('data') or {};print(json.dumps({'status':d.get('status'),'outputs':[{'item_status':o.get('item_status'),'data':o.get('data'),'error':str(o.get('error','')).split('data:image')[0][:160]} for o in d.get('outputs',[])]},ensure_ascii=False))
