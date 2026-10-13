"""Keep a boot's front rim above the shin without moving the ankle pivot."""
import copy
import numpy as np
from PIL import Image,ImageDraw

def cuff_image(image):
    """Extract the front cuff from a right-facing, upright boot sprite.

    The opening/back rim remains on the rear shoe layer. No pixels are invented.
    Current generated foot sprites use the standard heel-at-upper-left layout.
    """
    result=image.copy();W,H=image.size;mask=Image.new('L',image.size)
    ImageDraw.Draw(mask).polygon([(0,H*.13),(W*.52,H*.13),(W*.55,H*.39),(0,H*.39)],fill=255)
    result.putalpha(Image.fromarray(np.minimum(np.array(image.getchannel('A')),np.array(mask))))
    return result

def joined_order(order):
    order=list(order)
    for side in ('l','r'):
        foot,shin=side+'-foot',side+'-lower-leg'
        if foot in order and shin in order:
            order.remove(foot);order.insert(order.index(shin),foot)
    return order

def add_cuffs(skeleton,parts,cuff_images=None):
    """Add two helper slots bound to existing foot bones; leave motion untouched."""
    from .rig import skins
    from .core import UserError
    skeleton=copy.deepcopy(skeleton);parts=list(parts);by={p['name']:p for p in parts}
    order=joined_order([s['name'] for s in skeleton['slots']]);slots={s['name']:s for s in skeleton['slots']}
    skeleton['slots']=[slots[n] for n in order]
    for side in ('l','r'):
        foot,shin=side+'-foot',side+'-lower-leg';name=foot+'-cuff'
        if foot not in by or shin not in slots:raise UserError('鞋口接合需要完整的双脚和小腿')
        if name in slots or name in by:raise UserError('鞋口接合辅助层已存在')
        sprite=cuff_images[foot] if cuff_images else cuff_image(by[foot]['image'])
        if sprite.size!=by[foot]['image'].size or not sprite.getbbox():raise UserError('鞋口前缘无效，请检查完整鞋子贴图')
        parts.append({'name':name,'image':sprite})
        for _,attachments,_ in skins(skeleton):
            if foot not in attachments:continue
            items={}
            for key,value in attachments[foot].items():
                if value.get('type','region') not in ('region','mesh') or value.get('parent'):raise UserError('鞋口接合暂不支持链接网格或特殊脚部附件')
                attachment=copy.deepcopy(value);attachment['path']=name;items[name if key==foot else key]=attachment
            attachments[name]=items
        index=next(i for i,s in enumerate(skeleton['slots']) if s['name']==shin)
        skeleton['slots'].insert(index+1,{'name':name,'bone':slots[foot]['bone'],'attachment':name})
    return skeleton,parts
