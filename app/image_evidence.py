"""Lossless per-pose evidence, scoped crops and explicit zoom for inspection."""
from PIL import Image
from .core import UserError

def select(image,args,view):
 original=image.size;pose=args.get('pose');box=None
 if pose is not None:
  if view not in ('prebind-spread','layer-overlap-image') or type(pose)!=int or not 0<=pose<5 or image.width%5:raise UserError('pose仅支持五姿态摆开图的0–4编号')
  w=image.width//5;box=[w*pose,0,w*(pose+1),image.height];image=image.crop(box)
 crop=args.get('crop')
 if crop is not None:
  if not isinstance(crop,list) or len(crop)!=4 or any(type(v)!=int for v in crop):raise UserError('crop须为当前姿态内整数像素范围')
  x,y,x1,y1=crop
  if not 0<=x<x1<=image.width or not 0<=y<y1<=image.height:raise UserError('裁切范围越界')
  image=image.crop(crop)
 zoom=args.get('zoom',1)
 if type(zoom) not in (int,float) or not 1<=zoom<=4:raise UserError('放大倍率须为1–4')
 quality=args.get('resolution','original' if pose is not None or crop is not None else 'preview')
 if quality not in ('original','preview'):raise UserError('resolution须为original或preview')
 if image.width*image.height*zoom*zoom>24_000_000:raise UserError('请先按姿态或关节裁切再放大，最多2400万像素')
 if zoom!=1:image=image.resize((round(image.width*zoom),round(image.height*zoom)),Image.Resampling.NEAREST)
 if quality=='preview':image.thumbnail((1024,1024))
 return image,{'source_size':list(original),'pose':pose,'pose_box':box,'crop':crop,'zoom':zoom,'resolution':quality,'returned_size':list(image.size),'coordinates':'crop uses original pixels within the selected pose; zoom never changes source coordinates'}
