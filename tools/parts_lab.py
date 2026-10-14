"""Local administrator CLI for material experiments; no hidden model switching."""
import argparse
import base64
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.parts_lab import PartsLab
from app.image_provider import ImageProvider
from app.character import CharacterProject
from app.core import Project

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['create','run','status','inspect','probe-bones','review'])
    parser.add_argument('--id',required=True);parser.add_argument('--character');parser.add_argument('--template-project')
    parser.add_argument('--template-png',type=Path);parser.add_argument('--route',choices=['template','see-through'])
    parser.add_argument('--data',type=Path);parser.add_argument('--node')
    args=parser.parse_args();storage=ROOT/'storage';lab=PartsLab(storage,ImageProvider(storage))
    data=json.loads(args.data.read_text()) if args.data else {}
    if args.action=='create':
        data.update(nonce=args.id)
        if args.template_png:data['template_png']=base64.b64encode(args.template_png.read_bytes()).decode()
        from app.parts_lab import identifier
        result=lab.create(CharacterProject(storage/'characters'/identifier(args.character)),Project(storage/'projects'/identifier(args.template_project)),data)
    elif args.action=='run':
        if not args.route:parser.error('--route is required')
        lab.read(args.id);lab._run(args.id,args.route);result=lab.status(args.id)
    elif args.action=='inspect':result=lab.inspect(args.id,args.route,args.node)
    elif args.action=='probe-bones':result=lab.set_probe_bones(args.id,data)
    elif args.action=='review':result=lab.review(args.id,{**data,'route':args.route})
    else:result=lab.status(args.id)
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
