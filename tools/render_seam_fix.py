import sys,json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from render_rig_evidence import render
folder=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'evidence/single-image/seam-fix';data=json.loads((folder/'poses.json').read_text());old=json.loads((folder/'before-poses.json').read_text())
coords=np.array([v for p in data['frames'] for g in p['geometry'] for v in g['xy']]).reshape(-1,2);lo=coords.min(0);hi=coords.max(0);view=[*lo,*hi]
font=ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc',22);sheet=Image.new('RGB',(1440,770),'#f2f5f9');draw=ImageDraw.Draw(sheet)
complete=folder.name=='complete-parts'
preserved=folder.name=='preserved-fill'
if preserved:
 labels=['原图拆分：遮挡缺失','保留原图：遮挡补全','保留原图：另一侧迈步']
else:
 labels=['补全前：原图遮挡缺失' if complete else '修复前：关节粘连' if len(sys.argv)>1 else '修复前：关节裂开','亿创完整部件：行走' if complete else '修复后：分肢蒙皮','亿创完整部件：另一侧迈步' if complete else '修复后：另一侧迈步']
for x,frames,directory,index,label in [(0,old['frames'],old['dir'],18,labels[0]),(480,data['frames'],data['dir'],18,labels[1]),(960,data['frames'],data['dir'],0,labels[2])]:
 pic=render(frames[index],directory,view,(470,670),24);sheet.paste(pic,(x+5,55));draw.text((x+20,15),label,font=font,fill='#22324b')
sheet.save(folder/'comparison.png')
frames=[render(data['frames'][i],data['dir'],view,(420,600),24).convert('RGB') for i in range(0,48,3)]
frames[0].save(folder/'walk.gif',save_all=True,append_images=frames[1:],duration=63,loop=0)
