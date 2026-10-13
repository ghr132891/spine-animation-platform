#!/usr/bin/env python3
"""Create an original geometric robot fixture (not AI generated)."""
import json
import math
import sys
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core import png_bytes


def make_part(name, w, h):
    # Draw at 3x for antialiased edges, which also exercise alpha preservation.
    im = Image.new("RGBA", (w*3, h*3))
    d = ImageDraw.Draw(im)
    def box(xy, fill, radius=10, outline="#264255", width=2):
        d.rounded_rectangle(tuple(round(v*3) for v in xy),radius=radius*3,fill=fill,outline=outline,width=width*3)
    def circle(xy, fill, outline=None):
        d.ellipse(tuple(round(v*3) for v in xy),fill=fill,outline=outline,width=6)
    orange, light, teal, navy = "#eda243", "#ffe2a1", "#499796", "#264255"
    if name == "head":
        box((10,8,w-10,h-14),orange,22)
        box((18,27,w-18,65),navy,12)
        circle((29,36,40,49),"#c7f2e8");circle((59,36,70,49),"#c7f2e8")
        box((33,57,65,60),"#c7f2e8",1,outline=None)
        box((39,h-17,59,h-3),teal,4)
        box((24,16,73,20),light,2,outline=None)
        box((45,0,52,12),navy,2);circle((43,0,54,9),teal)
    elif name == "torso":
        box((6,4,w-6,h-5),teal,25)
        box((21,30,w-21,h-25),"#e2dfcd",10)
        box((28,43,w-28,58),navy,3)
        box((28,67,44,77),orange,3)
        box((51,67,w-28,77),teal,3)
        box((16,13,27,22),light,3)
        circle((w-28,13,w-16,25),orange,navy)
    elif name == "pelvis":
        box((3,3,w-3,h-3),navy,13)
        box((12,9,w-12,h-13),orange,6)
    elif "foot" in name:
        box((3,7,w-3,h-5),orange,12)
        box((4,h-15,w-3,h-4),navy,4)
        box((10,9,27,14),light,2,outline=None)
    else:
        is_upper = "upper" in name
        color = orange if is_upper else teal
        box((5,5,w-5,h-8),color,14)
        circle((w/2-10,1,w/2+10,21),navy)
        circle((w/2-5,6,w/2+5,16),"#a8bec6")
        box((11,25,16,h-20),light if is_upper else "#84c3b6",2,outline=None)
        circle((w/2-10,h-23,w/2+10,h-3),navy)
        circle((w/2-5,h-18,w/2+5,h-8),"#a8bec6")
    return im.resize((w,h),Image.Resampling.LANCZOS)


def main():
    out = ROOT / "demo"
    out.mkdir(exist_ok=True)
    bones=[{"name":"root"},{"name":"hip","parent":"root","y":153},{"name":"torso","parent":"hip"},{"name":"head","parent":"torso","y":127}]
    attachments={"head":{"width":98,"height":94,"x":0,"y":37},"torso":{"width":108,"height":125,"x":0,"y":55},"pelvis":{"width":76,"height":44,"x":0,"y":-4}}
    slot_bones={"head":"head","torso":"torso","pelvis":"hip"}
    for side,s in [('left',-1),('right',1)]:
        for part,parent,x,y,w,h,cy in [('upper-arm','torso',s*65,108,38,77,-28),('lower-arm',side+'-upper-arm',s*3,-58,34,68,-23),('upper-leg','hip',s*25,-10,42,82,-30),('lower-leg',side+'-upper-leg',0,-61,36,79,-30),('foot',side+'-lower-leg',0,-63,66,36,-7)]:
            name=side+'-'+part
            bones.append({'name':name,'parent':parent,'x':x,'y':y,'length':max(h-20,0)})
            attachments[name]={'width':w,'height':h,'x':13 if part=='foot' else 0,'y':cy}
            slot_bones[name]=name
    order=['left-upper-arm','left-lower-arm','left-upper-leg','left-lower-leg','left-foot','right-upper-leg','right-lower-leg','right-foot','pelvis','torso','right-upper-arm','right-lower-arm','head']
    imgs={n:make_part(n,int(a['width']),int(a['height'])) for n,a in attachments.items()}
    canvas=Image.new('RGBA',(512,512))
    lines=['robot.png','size: 512,512','format: RGBA8888','filter: Linear,Linear','repeat: none']
    x=y=4;row_h=0
    for name,im in imgs.items():
        if x+im.width+4>512:x=4;y+=row_h+4;row_h=0
        canvas.paste(im,(x,y))
        lines.extend([name,'  rotate: false',f'  xy: {x}, {y}',f'  size: {im.width}, {im.height}',f'  orig: {im.width}, {im.height}','  offset: 0, 0','  index: -1'])
        x+=im.width+4;row_h=max(row_h,im.height)
    walk={};idle={}
    times=[i*.9/16 for i in range(17)]
    for side,phase in [('left',0),('right',math.pi)]:
        for part,amp,offset in [('upper-arm',-15,0),('lower-arm',-8,-10)]:
            name=side+'-'+part
            walk[name]={'rotate':[{'time':round(t,6),'value':round(offset+amp*math.cos(2*math.pi*t/.9+phase),5)} for t in times]}
        # The art faces +X. During stance the foot travels front -> back;
        # during recovery the shin folds behind the knee, not in front of it.
        for part in ('upper-leg','lower-leg','foot'):
            walk[side+'-'+part]={'rotate':[]}
        for t in times:
            angle=2*math.pi*t/.9+phase
            swing=max(0,-math.sin(angle))
            thigh=22*math.cos(angle)
            shin=-4-34*swing
            sole_pitch=6*swing-4*max(0,math.sin(angle))
            # Compensate for BOTH parents, keeping the sole close to horizontal.
            ankle=sole_pitch-thigh-shin
            for part,value in [('upper-leg',thigh),('lower-leg',shin),('foot',ankle)]:
                walk[side+'-'+part]['rotate'].append({'time':round(t,6),'value':round(value,5)})
    walk['hip']={'translate':[{'time':round(t,6),'x':0,'y':round(3-3*math.cos(4*math.pi*t/.9),5)} for t in times]}
    idle['torso']={'rotate':[{'time':0,'value':-1},{'time':1,'value':1},{'time':2,'value':-1}],'translate':[{'time':0,'y':0},{'time':1,'y':2},{'time':2,'y':0}]}
    idle['head']={'rotate':[{'time':0,'value':1},{'time':1,'value':-1},{'time':2,'value':1}]}
    sk={'skeleton':{'spine':'4.2.0','width':220,'height':385,'x':-110,'y':-5},'bones':bones,'slots':[{'name':n,'bone':slot_bones[n],'attachment':n} for n in order],'skins':[{'name':'default','attachments':{n:{n:attachments[n]} for n in order}}],'animations':{'idle':{'bones':idle},'walk':{'bones':walk}}}
    (out/'robot.json').write_text(json.dumps(sk,indent=2))
    (out/'robot.atlas').write_text('\n'.join(lines)+'\n')
    canvas.save(out/'robot.png')
    (out/'README.txt').write_text('本工具原创几何机器人演示，由 tools/create_demo.py 绘制；不是 AI 生图。骨骼动画仅用作换皮一致性测试。\n')
    print('Created original robot demo:',len(bones),'bones,',len(imgs),'regions')


if __name__=='__main__':main()
