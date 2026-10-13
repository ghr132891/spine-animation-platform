"""Conservative secondary motion for reviewed, continuous long costume meshes.

This is authored 2D motion, not cloth simulation or collision resolution.
"""
import math
from .gait import spine_bezier_timeline


def add_long_hem_motion(skeleton, period, facing=1):
    T=float(period)
    if not math.isfinite(T) or T<=0:raise ValueError('period must be positive')
    bones=skeleton['bones']; index={b['name']:i for i,b in enumerate(bones)}
    if any(n not in index for n in ('hip','l-upper-leg','l-lower-leg','r-upper-leg','r-lower-leg')):
        return False
    if any(n in index for n in ('hem-front','hem-back')):
        return False
    att=skeleton.get('skins',[{}])[0].get('attachments',{}).get('torso',{}).get('torso',{})
    v=att.get('vertices',[])
    if att.get('type')!='mesh' or len(v)==len(att.get('uvs',[])):
        return False
    leg=min(sum(bones[index[side+'-'+part]]['length'] for part in ('upper-leg','lower-leg')) for side in ('l','r'))
    rows=[];p=0;depth=0
    while p<len(v):
        n=int(v[p]);p+=1;row=[]
        for _ in range(n):
            bi,x,y,w=v[p:p+4];p+=4;row.append((int(bi),x,y,w))
            if int(bi)==index['hip'] and w>0:depth=max(depth,-y)
        rows.append(row)
    # Only a continuous reviewed costume reaching below the knee qualifies.
    if leg<=0 or depth<leg*.6:return False
    front=len(bones);back=front+1
    bones.extend([{'name':n,'parent':'hip','x':0,'y':0,'rotation':0} for n in ('hem-front','hem-back')])
    result=[]
    for row in rows:
        weights=[]
        for bi,x,y,w in row:
            if bi!=index['hip'] or y>=0:
                weights.append((bi,x,y,w));continue
            u=min(1,max(0,(-y)/(depth*.85)));falloff=u*u*(3-2*u)
            # Smooth across the centre: a hard x sign split tears adjacent triangles.
            blend=min(1,max(0,.5+float(facing)*x/max(leg*.4,1)))
            blend=blend*blend*(3-2*blend)
            weights.extend((i,x,y,weight) for i,weight in ((bi,w*(1-falloff)),(front,w*falloff*blend),(back,w*falloff*(1-blend))) if weight>1e-9)
        result.append(len(weights))
        for item in weights:result.extend(item)
    att['vertices']=result
    for clip,duration,amp in [('walk',T,1.0),('idle',2.0,.08)]:
        timelines=skeleton['animations'][clip].setdefault('bones',{})
        times=[duration*i/128 for i in range(129)]
        for name,phase,gain in [('hem-front',-.7,1),('hem-back',-1.2,.65)]:
            def sample(t,phase=phase,gain=gain):
                # Neutral hanging reference; no absolute thigh offsets or rectified max.
                return {'value':float(facing)*amp*gain*(.6+2.2*math.sin(4*math.pi*t/duration+phase)+.6*math.sin(2*math.pi*t/duration+phase))}
            timelines[name]={'rotate':spine_bezier_timeline(sample,times,angular=True)}
    skeleton['skeleton']['garment_motion']={'version':'long-hem-v1','amplitude_bound_degrees':3.4,'requires_visual_review':True,'checks':['front_hem_hang','slit_fold','leg_occlusion','cycle_continuity'],'physics':False}
    return True
