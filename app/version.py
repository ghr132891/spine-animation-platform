"""Identity of the source loaded by this process, not latest remote version."""
import json,subprocess,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION').read_text().strip()
def identity():
 result={'version':VERSION,'commit':None,'dirty':None,'source':'unknown'}
 try:
  result['commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL,timeout=3).strip()
  result['dirty']=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL,timeout=3).strip());result['source']='git'
 except (OSError,subprocess.SubprocessError):
  path=ROOT/'release-build.json'
  if path.exists():
   b=json.loads(path.read_text())
   if b.get('version')==VERSION and re.fullmatch('[a-f0-9]{40}',b.get('commit','')):result.update(commit=b['commit'],dirty=False,source='release-bundle')
  else:
   path=ROOT/'.git_archival.txt';text=path.read_text().strip() if path.exists() else ''
   if re.fullmatch('[a-f0-9]{40}',text):result.update(commit=text,dirty=False,source='git-archive')
 return result
BUILD=identity()
