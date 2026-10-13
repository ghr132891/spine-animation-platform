"""Offline SVG inspection layer QA, composited with runtime-generated geometry."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageColor
from render_rig_evidence import render
ROOT=Path(__file__).resolve().parents[1]

def main():
    for kind in ('robot42','mesh38'):
        folder=ROOT/'evidence/rerig'/kind;data=json.loads((folder/'inspection-pose.json').read_text());w=data['width'];h=data['height'];camera=data['camera'];x=camera['x'];y=camera['y'];zoom=camera['zoom']
        im=render(data['pose'],data['directory'],[x-w*zoom/2,y-h*zoom/2,x+w*zoom/2,y+h*zoom/2],(w,h),padding=0)
        for e in ET.parse(folder/'bone-selection.svg').getroot():
            layer=Image.new('RGBA',(w,h));d=ImageDraw.Draw(layer);tag=e.tag.split('}')[-1];get=lambda name,default='0':float(e.get(name,default))
            def color(key):
                value=e.get(key)
                if not value or value=='none':return None
                return (*ImageColor.getrgb(value),int(get(key+'-opacity','1')*255))
            fill=color('fill');stroke=color('stroke');width=max(1,round(get('stroke-width','1')))
            if tag=='polygon':
                points=[tuple(float(v) for v in point.split(',')) for point in e.get('points').split()];d.polygon(points,fill=fill)
                if stroke:d.line([*points,points[0]],fill=stroke,width=width)
            elif tag=='line':d.line((get('x1'),get('y1'),get('x2'),get('y2')),fill=stroke,width=width)
            elif tag=='circle':
                cx=get('cx');cy=get('cy');r=get('r');d.ellipse((cx-r,cy-r,cx+r,cy+r),fill=fill,outline=stroke,width=width)
            elif tag=='rect':d.rounded_rectangle((get('x'),get('y'),get('x')+get('width'),get('y')+get('height')),radius=get('rx'),fill=fill)
            elif tag=='text':d.text((get('x'),get('y')-13),e.text or '',font=ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc',12),fill=fill)
            im.alpha_composite(layer)
        im.save(folder/'bone-inspection.png')

if __name__=='__main__':main()
