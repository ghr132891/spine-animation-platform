import sys,json,base64
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.provider import Matrix
from app.core import UserError
m=Matrix(ROOT/'storage')
for route in ['/models/gpt-image-2/schema','/models/gpt-image-2.5-flare/schema']:
    try:
        response=m.request(route)
        (ROOT/'evidence'/(route.split('/')[2]+'-schema.json')).write_text(json.dumps(response,indent=2,ensure_ascii=False))
        print(route,json.dumps(response,ensure_ascii=False)[:7000])
    except UserError as e:print(route,str(e))
