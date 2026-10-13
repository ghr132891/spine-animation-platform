import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.provider import Matrix
from app.core import UserError
try:
    response=Matrix(ROOT/'storage').request('/api/matrix/publish/v1/aliyun/token')
    data=response.get('data') or {}
    print({'success':True,'fields':list(data),'credential_fields':list((data.get('credential') or {})),'server':data.get('server'),'bucket_name':data.get('bucket_name')})
except UserError as e:print({'success':False,'error':str(e)})
