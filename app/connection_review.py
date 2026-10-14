"""Targeted assembly checks; values must be based on current runtime panels."""
from .core import UserError
from .asset_integrity import garment_frame_count
LEGACY_CONTRACT='assembly-connections-v1'
V2_CONTRACT='assembly-connections-v2'
CONTRACT='assembly-connections-v3'
CONTRACTS=(LEGACY_CONTRACT,V2_CONTRACT,CONTRACT)
CHECKS={'head':('neck_pin_aligned','collar_occlusion'),
        '__hip__':('waist_continuity','hip_overlap','root_stable'),
        '__back__':('worn_camera','strap_face_inward','attachment_stable')}
LEGACY_CHECKS=CHECKS
CHECKS={**CHECKS,'__hip__':CHECKS['__hip__']+('texture_continuity','upper_edge_covered'),
 '__back__':CHECKS['__back__']+('complete_silhouette','occluded_artwork_complete')}
V2_CHECKS=CHECKS
CHECKS={**CHECKS,'head':CHECKS['head']+('rear_collar_behind_neck','front_collar_in_front','runtime_pose_consistent')}
def anchors(skeleton):
    bones={b['name'] for b in skeleton.get('bones',[])}
    slots={s['name'] for s in skeleton.get('slots',[])}
    integrated=skeleton.get('skeleton',{}).get('assembly_features',{}).get('back_accessory_part')
    return (['head'] if 'head' in bones else [])+(['__hip__'] if 'hip' in bones and any(n.endswith('-upper-leg') for n in bones) else [])+(['__back__'] if ('back' in slots or integrated in slots) and 'torso' in bones else [])
def checked(data,anchor,contract=CONTRACT):
    required=(LEGACY_CHECKS if contract==LEGACY_CONTRACT else V2_CHECKS if contract==V2_CONTRACT else CHECKS).get(anchor,());values=data.get('connection_checks')
    if not isinstance(values,dict) or set(values)!=set(required) or any(type(v) is not bool for v in values.values()):
        raise UserError('connection_checks 须逐项记录：'+', '.join(required))
    if data.get('verdict')=='ready' and not all(values.values()):
        raise UserError('颈口、裤腰或背包连接仍有问题，先修复再验收')
    return dict(values)
def require(review,digest,clips,skeleton,contract=CONTRACT):
    if review.get('sha256')!=digest:raise UserError('连接检查已失效，须重看当前资源')
    for clip in clips:
        for anchor in anchors(skeleton):
            item=review.get('regions',{}).get(clip,{}).get(anchor,{})
            report=item.get('report',{});assessment=item.get('review',{})
            count=garment_frame_count(clip) if anchor=='__hip__' or (anchor=='__back__' and contract!=LEGACY_CONTRACT) or (anchor=='head' and contract==CONTRACT) else 8
            if report.get('isolated') or report.get('frames',0)<count or assessment.get('verdict')!='ready':
                raise UserError('须检查当前组合动画的连接：'+clip+':'+anchor)
            checked(assessment,anchor,contract)
