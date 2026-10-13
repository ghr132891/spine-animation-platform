"""Rasterize official-runtime sampled triangles for offline visual inspection."""
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'evidence/rerig'

def sample_linear(texture, uv):
    """Match atlas Linear filtering at texel centers (straight-alpha RGBA)."""
    h,w=texture.shape[:2]
    x=np.clip(uv[0]*w-.5,0,w-1);y=np.clip(uv[1]*h-.5,0,h-1)
    x0=np.floor(x).astype(int);y0=np.floor(y).astype(int)
    x1=np.minimum(x0+1,w-1);y1=np.minimum(y0+1,h-1)
    fx=(x-x0)[:,None];fy=(y-y0)[:,None]
    return ((texture[y0,x0]*(1-fx)+texture[y0,x1]*fx)*(1-fy)+
            (texture[y1,x0]*(1-fx)+texture[y1,x1]*fx)*fy)

def render(pose,directory,view,size,padding=30,background=(229,235,243,255),textures=None):
    w,h=size;x0,y0,x1,y1=view;scale=min((w-padding*2)/(x1-x0),(h-padding*2)/(y1-y0));cx=(x0+x1)/2;cy=(y0+y1)/2
    canvas=Image.new('RGBA',size,background)
    if textures is None:textures={}
    for item in pose['geometry']:
        texture_name=item['page'];path=Path(directory)/texture_name
        if texture_name not in textures:textures[texture_name]=np.array(Image.open(path).convert('RGBA'))
        texture=textures[texture_name];th,tw=texture.shape[:2];xy=np.array(item['xy']).reshape(-1,2);xy[:,0]=(xy[:,0]-cx)*scale+w/2;xy[:,1]=h/2-(xy[:,1]-cy)*scale
        uv=np.array(item['uv']).reshape(-1,2);layer=Image.new('RGBA',size)
        triangles=np.array(item['tri']).reshape(-1,3)
        # Batch the exact viewport rejection formerly repeated per triangle.
        # This changes neither sampling nor alpha compositing for visible geometry.
        points=xy[triangles]
        lows=np.maximum(np.floor(points.min(axis=1)).astype(int),0)
        highs=np.minimum(np.ceil(points.max(axis=1)).astype(int),[w,h])
        visible=np.flatnonzero(np.all(highs>lows,axis=1))
        for tri_index in visible:
            indices=triangles[tri_index];p=points[tri_index];tex=uv[indices];lo=lows[tri_index];hi=highs[tri_index]
            mat=np.vstack([p.T,np.ones(3)])
            area=(p[1,0]-p[0,0])*(p[2,1]-p[0,1])-(p[2,0]-p[0,0])*(p[1,1]-p[0,1])
            if not np.isfinite(area) or abs(area)<1e-8:continue
            xx,yy=np.meshgrid(np.arange(lo[0],hi[0])+.5,np.arange(lo[1],hi[1])+.5);bary=np.linalg.inv(mat)@np.stack([xx.ravel(),yy.ravel(),np.ones(xx.size)])
            mask=np.all(bary>=-1e-7,axis=0)
            # Only covered pixels need atlas lookup. Triangle bounding boxes can
            # be mostly empty on narrow hair/limb meshes; alpha output is unchanged.
            coords=tex.T@bary[:,mask]
            pixels=np.zeros((xx.size,4),dtype=float)
            pixels[mask]=sample_linear(texture,coords)*np.array(item['color'])
            patch=Image.fromarray(np.clip(pixels.reshape(hi[1]-lo[1],hi[0]-lo[0],4),0,255).astype('uint8'))
            # Each slot's triangles share edges; keep a single alpha at boundaries.
            layer.paste(patch,tuple(lo),Image.fromarray((mask.reshape(hi[1]-lo[1],hi[0]-lo[0])*255).astype('uint8')))
        canvas.alpha_composite(layer)
    return canvas

def main():
    for item in json.loads((EVIDENCE/'examples.json').read_text()):
        folder=EVIDENCE/item['kind'];poses=json.loads((folder/'pose.json').read_text());xy=np.array([coord for key in ('before','after') for geometry in poses[key]['geometry'] for coord in geometry['xy']]).reshape(-1,2)
        lo=xy.min(axis=0);hi=xy.max(axis=0);view=[*lo,*hi];before=render(poses['before'],poses['beforeDir'],view,(650,760));after=render(poses['after'],poses['afterDir'],view,(650,760))
        sheet=Image.new('RGB',(1320,850),'#f2f5f9');sheet.paste(before,(5,60));sheet.paste(after,(665,60));draw=ImageDraw.Draw(sheet);font=ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc',20)
        draw.text((20,18),'创建时的角色',font=font,fill='#22324b');draw.text((685,18),'新骨架与贴图',font=font,fill='#22324b');draw.text((20,821),f"Spine {item['runtime']}  ·  {poses['animation']}  ·  原项目保留  ·  官方 runtime 顶点采样",font=font,fill='#66748a')
        sheet.save(folder/'comparison.png')

if __name__=='__main__':main()
