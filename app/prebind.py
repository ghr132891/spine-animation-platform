"""Content-bound pre-animation acceptance for newly generated full asset sheets."""
import hashlib,json
from PIL import Image,ImageDraw
from .core import UserError,png_bytes
CHECKS=('complete_parts','assembly_matches','spread_intact')
def inspect(character):
    parts=character.parts();order=character.review_order([p['name'] for p in parts])
    overlap,spread=character.layer_overlap()
    tiles=Image.new('RGB',(320*4,420*((len(parts)+3)//4)),(225,232,240))
    for i,p in enumerate(parts):
        im=p['image'].copy();im.thumbnail((300,380));x=(i%4)*320;y=(i//4)*420
        tiles.paste(im,(x+(320-im.width)//2,y+30),im)
        ImageDraw.Draw(tiles).text((x+8,y+8),p['name'],fill='black')
    assembly=Image.new('RGBA',character.image().size)
    by={p['name']:p for p in parts}
    for n in order:assembly.alpha_composite(by[n]['image'],tuple(by[n]['box'][:2]))
    payload={'overlap':overlap['digest'],'source':character.meta['image_id'],'settings':character.meta['settings'],'masks':character.meta.get('masks',{}),'parts':[(p['name'],hashlib.sha256(png_bytes(p['image'])).hexdigest()) for p in parts]}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    return {'contract':'complete-assets-v1','digest':digest,'checks':list(CHECKS),'missing_overlap':overlap['missing_overlap'],'part_count':len(parts),'incomplete_parts':[p['name'] for p in parts if not p.get('complete')],'missing_parts':sorted({'head','torso','l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg'}-set(order)),'limits':'独立图、试拼图和摆开图均须实际视觉检查，数值不替代美术验收。'}, {'parts':tiles,'assembly':assembly,'spread':spread}
def validate_review(report,data):
    if data.get('digest')!=report['digest']:raise UserError('素材已变化，请重新查看三项验收图')
    checks=data.get('checks',{})
    if set(checks)!=set(CHECKS) or any(type(v)!=bool for v in checks.values()):raise UserError('须分别记录完整部件、试拼与摆开结论')
    if data.get('verdict') not in ('ready','needs_repair') or not isinstance(data.get('reason'),str) or not data['reason'].strip():raise UserError('请记录实际观察与结论')
    if data['verdict']=='ready' and (not all(checks.values()) or report['missing_overlap'] or report.get('incomplete_parts') or report.get('missing_parts')):raise UserError('三项验收未通过，停留在素材阶段')
    return {k:data[k] for k in ('digest','checks','verdict','reason')}
def require(report,review):
    if not isinstance(review,dict):raise UserError('先验收完整部件、试拼和摆开，再制作动画')
    validate_review(report,review)
    if review['verdict']!='ready':raise UserError('素材验收未通过，尚不能制作动画')
