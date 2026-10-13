"""Explicit visual attestations; numeric geometry is not visual acceptance."""
from .core import UserError

CONTRACT = 'asset-integrity-v1'
ASSEMBLY_CHECKS = ('connection_overlap', 'garment_continuity', 'layer_separation',
                   'silhouette_valid', 'edge_clean')

def required_checks(anchor=None):
    if anchor is None or anchor == 'torso' or '-leg' in anchor:
        return ASSEMBLY_CHECKS
    if anchor == '__hair__':
        return ('silhouette_valid', 'edge_clean', 'occlusion_valid')
    return ('connection_overlap', 'layer_separation', 'silhouette_valid', 'edge_clean')

def checked_material_review(data, anchor=None):
    """Require observed outcomes, including failures, rather than a blanket ready."""
    checks = data.get('material_checks')
    required = required_checks(anchor)
    if not isinstance(checks, dict) or set(checks) != set(required) or any(type(v) is not bool for v in checks.values()):
        raise UserError('material_checks 须逐项记录实际图面检查：' + ', '.join(required))
    if data.get('verdict') == 'ready' and not all(checks.values()):
        raise UserError('连接、裙片完整性、分层或轮廓仍有缺陷，不能记录 ready；修整或补齐真实图面后复检')
    return dict(checks)

def require_material_reviews(review, digest, clips, anchors):
    if review.get('sha256') != digest:
        raise UserError('材质与分层验收已失效，须检查当前导出的资源')
    for clip in clips:
        for anchor in anchors:
            item = review.get('regions', {}).get(clip, {}).get(anchor, {}).get('review', {})
            if item.get('verdict') != 'ready':
                raise UserError('尚未通过图面验收：' + clip + ':' + anchor)
            checked_material_review(item, anchor)

GARMENT_CONTRACT = 'garment-occlusion-v1'
GARMENT_CHECKS = ('hip_coverage', 'thigh_contour', 'knee_readable', 'garment_clearance')

def garment_anchors(skeleton):
    names={b['name'] for b in skeleton.get('bones',[])}
    if not (skeleton.get('skeleton',{}).get('garment_motion') or {'hem-front','hem-back'} <= names):return []
    return [n for n in ('l-upper-leg','r-upper-leg') if n in names]

def garment_frame_count(clip):
    return 32 if any(word in clip.lower() for word in ('walk','run','行走','跑')) else 8

def checked_garment_review(data):
    checks=data.get('garment_checks')
    if not isinstance(checks,dict) or set(checks)!=set(GARMENT_CHECKS) or any(type(v) is not bool for v in checks.values()):
        raise UserError('garment_checks 须记录 hip_coverage/thigh_contour/knee_readable/garment_clearance 四项实际结论')
    if data.get('verdict')=='ready' and not all(checks.values()):
        raise UserError('腿根外露、大腿挤尖、膝部被误遮或衣料穿插未修复，不能 ready')
    return dict(checks)

def require_garment_reviews(review,digest,clips,skeleton):
    anchors=garment_anchors(skeleton)
    if not anchors:return
    if review.get('sha256')!=digest:raise UserError('衣裙遮挡验收已失效，须复检当前资源')
    for clip in clips:
        for anchor in anchors:
            item=review.get('regions',{}).get(clip,{}).get(anchor,{})
            report=item.get('report',{})
            if report.get('isolated') or report.get('frames',0)<garment_frame_count(clip):
                raise UserError('衣裙遮挡须查看组装后的腿部放大图：待机8帧，行走32帧；隔离图不能替代验收')
            assessment=item.get('review',{})
            if assessment.get('verdict')!='ready':raise UserError('衣裙与腿形尚未验收：'+clip+':'+anchor)
            checked_garment_review(assessment)
