"""Build a clean tagged-source release without runtime data or credentials."""
import argparse,hashlib,io,json,subprocess,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'web/downloads');a=p.parse_args()
 dirty=subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()
 if dirty:raise SystemExit('Refusing release: commit reviewed changes first')
 version=(ROOT/'VERSION').read_text().strip();commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();a.output.mkdir(parents=True,exist_ok=True)
 raw=subprocess.check_output(['git','archive','--format=zip','HEAD'],cwd=ROOT);source=a.output/f'spine-animation-platform-{version}.zip';prefix='spine-animation-platform-'+version+'/'
 with zipfile.ZipFile(io.BytesIO(raw)) as original,zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as z:
  for n in original.namelist():
   if n.startswith(('storage/','evidence/')) or n.endswith(('.token','.key','.pem')):raise SystemExit('Unexpected private file in source archive')
   z.writestr(prefix+n,original.read(n))
  z.writestr(prefix+'release-build.json',json.dumps({'version':version,'commit':commit}))
  for n in ('spine-animation-skill.zip','spine-agent-client.zip'):
   path=ROOT/'web/downloads'/n
   if not path.is_file():raise SystemExit('Run package_spine_skills.py before committing')
   z.writestr(prefix+'web/downloads/'+n,path.read_bytes())
 files=[source,ROOT/'web/downloads/spine-animation-skill.zip',ROOT/'web/downloads/spine-agent-client.zip']
 manifest={'version':version,'commit':commit,'files':{f.name:{'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'bytes':f.stat().st_size} for f in files}}
 (a.output/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
