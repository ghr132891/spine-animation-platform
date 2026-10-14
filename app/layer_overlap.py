"""Pre-rig overlap diagnostics; synthetic separation is not animation acceptance."""
import hashlib,json,math
import numpy as np
from PIL import Image,ImageDraw
from .core import png_bytes,UserError
CHECKS=('complete_silhouettes','joint_overlap','draw_order','spread_no_holes')

def inspect(reference,parts,order,landmarks):
    w,h=reference.size;by={p['name']:p for p in parts};canvases={}
    for n in order:
        p=by[n];im=Image.new('RGBA',(w,h));im.alpha_composite(p['image'],tuple(p['box'][:2]));canvases[n]=im
    scenarios=[('assembled',{}),('arms-spread',{'l-upper-arm':25,'r-upper-arm':-25}),('arms-reverse',{'l-upper-arm':-25,'r-upper-arm':25}),('legs-spread',{'l-upper-leg':18,'r-upper-leg':-18}),('legs-reverse',{'l-upper-leg':-18,'r-upper-leg':18})]
    sheet=Image.new('RGB',(w*len(scenarios),h),(224,231,240));panels=[]
    for i,(name,angles) in enumerate(scenarios):
        panel=Image.new('RGBA',(w,h))
        for n in order:
            im=canvases[n];p=by[n]
            motion=p.get('overlay_of',n)
            if motion in angles:
                pivot=landmarks[p['start']];im=im.rotate(angles[motion],resample=Image.Resampling.BICUBIC,center=(pivot[0]*w,pivot[1]*h))
            panel.alpha_composite(im)
        sheet.paste(panel,(i*w,0),panel);ImageDraw.Draw(sheet).text((i*w+10,10),name,fill='black');panels.append(name)
    overlap=[];body=canvases.get('torso')
    if body:
        a=np.asarray(body)[:,:,3]>127
        for n in ('l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg'):
            if n not in canvases:continue
            joint_body=canvases.get('pelvis') if n.endswith('-leg') else body
            joint_alpha=np.asarray(joint_body if joint_body is not None else body)[:,:,3]>127
            p=by[n];point=landmarks[p['start']];x,y=round(point[0]*w),round(point[1]*h);radius=max(3,round(h*.025));ys=slice(max(0,y-radius),min(h,y+radius+1));xs=slice(max(0,x-radius),min(w,x+radius+1))
            b=np.asarray(canvases[n])[:,:,3]>127;overlap.append({'part':n,'joint':p['start'],'opaque_overlap_pixels':int((joint_alpha[ys,xs]&b[ys,xs]).sum())})
    payload={'layers':[(n,hashlib.sha256(png_bytes(canvases[n])).hexdigest()) for n in order],'landmarks':landmarks,'scenarios':scenarios,'version':'layer-overlap-v1'}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    return {'contract':'layer-overlap-v1','digest':digest,'panels':panels,'joint_overlap':overlap,'missing_overlap':[r['part'] for r in overlap if r['opaque_overlap_pixels']==0],'visual_verdict':'not_reviewed','limits':'旋转展开用于暴露缺片，不是最终骨骼动画；重叠像素数不能证明连接正确。'},sheet

def require(report,review):
    if not isinstance(review,dict) or review.get('digest')!=report['digest']:raise UserError('先查看当前摆开诊断图并记录完整部件验收')
    checks=review.get('checks',{})
    if set(checks)!=set(CHECKS) or any(type(v)!=bool for v in checks.values()):raise UserError('摆开验收须记录四项布尔结论')
    if report['missing_overlap'] or review.get('verdict')!='ready' or not all(checks.values()):raise UserError('完整图面/关节重叠/摆开验收未通过，只能交付草稿')
