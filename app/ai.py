"""Reference preparation and reversible fitting of generated surface artwork."""
from PIL import Image
import numpy as np
from .core import png_bytes, read_image


def reference_for(part):
    size=512
    scale=min(384/part.width,384/part.height)
    width,height=max(1,round(part.width*scale)),max(1,round(part.height*scale))
    x,y=(size-width)//2,(size-height)//2
    ref=Image.new('RGB',(size,size),'#eeeeee')
    enlarged=part.resize((width,height),Image.Resampling.LANCZOS)
    ref.paste(enlarged,(x,y),enlarged)
    return png_bytes(ref),{'size':size,'x':x,'y':y,'width':width,'height':height,'target_width':part.width,'target_height':part.height}


def fit_result(data,layout,original=None):
    if layout.get('mode')=='material':
        image=read_image(data).resize((layout['target_width'],layout['target_height']),Image.Resampling.LANCZOS)
        if original is not None:
            old=np.array(original).astype(np.float32)
            new=np.array(image).astype(np.float32)
            light=old[...,:3] @ np.array([.2126,.7152,.0722])
            new[...,:3]*=(.45+.75*light/255)[...,None]
            dark=light<75
            new[dark,:3]=old[dark,:3]
            image=Image.fromarray(np.clip(new,0,255).astype(np.uint8))
        return image
    image=read_image(data).resize((layout['size'],layout['size']),Image.Resampling.LANCZOS)
    image=image.crop((layout['x'],layout['y'],layout['x']+layout['width'],layout['y']+layout['height']))
    return image.resize((layout['target_width'],layout['target_height']),Image.Resampling.LANCZOS)
