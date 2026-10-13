"""Render a labeled contact sheet of actual exported part PNGs."""
import sys
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
folder=Path(sys.argv[1]).resolve();files=sorted((folder/'export/images').glob('*.png'))
sheet=Image.new('RGB',(1200,1200),'#d4dce6');draw=ImageDraw.Draw(sheet)
font=ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc',18)
for i,p in enumerate(files):
    image=Image.open(p).convert('RGBA');image.thumbnail((250,245))
    x=i%4*300+20;y=i//4*300+42;sheet.paste(image,(x,y),image)
    draw.text((x,y-30),p.stem,fill='#14243a',font=font)
sheet.save(folder/'parts.png')
