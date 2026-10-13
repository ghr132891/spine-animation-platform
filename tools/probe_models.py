import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.provider import Matrix
response=Matrix(ROOT/'storage').request('/models')
(ROOT/'evidence/matrix-models.json').write_text(json.dumps(response,indent=2,ensure_ascii=False))
print(json.dumps(response,ensure_ascii=False)[:16000])
