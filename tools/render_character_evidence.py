import json,sys
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from render_rig_evidence import render
folder=ROOT/'evidence/single-image';data=json.loads((folder/'poses.json').read_text());poses=data['frames'];xy=np.array([v for p in poses for g in p['geometry'] for v in g['xy']]).reshape(-1,2);lo=xy.min(0);hi=xy.max(0);view=[*lo,*hi]
frames=[render(p,data['dir'],view,(470,650),24).convert('RGB') for p in poses[:-1]]
frames[0].save(folder/'walk.gif',save_all=True,append_images=frames[1:],duration=round(1000/48),loop=0)
sheet=Image.new('RGB',(1440,800),'#f2f5f9');draw=ImageDraw.Draw(sheet);font=ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc',22)
source=Image.open(folder/'source.png').convert('RGB');source.thumbnail((440,670));sheet.paste(source,((480-source.width)//2,65));sheet.paste(frames[0],(485,65));sheet.paste(frames[18],(970,65));draw.text((25,20),'亿创生成的单张角色图',fill='#22324b',font=font);draw.text((505,20),'行走：支撑与摆臂',fill='#22324b',font=font);draw.text((990,20),'行走：抬脚',fill='#22324b',font=font);draw.text((25,755),'独立 Spine 4.2 骨架 · 16 个部件 · 1 秒循环 · 官方 runtime 顶点采样',fill='#536881',font=font);sheet.save(folder/'comparison.png')
