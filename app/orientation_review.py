"""Per-side visual evidence, not a classifier or an automatic mirror operation."""
from .core import UserError

CONTRACT = 'limb-orientation-v1'
ASSEMBLY_CHECKS = ('left_arm_mapping', 'right_arm_mapping', 'left_palm_facing',
                   'right_palm_facing', 'left_shoe_facing', 'right_shoe_facing')
ARM_CHECKS = ('mapping_correct', 'palm_facing', 'wrist_continuity', 'occlusion_readable')
LEG_CHECKS = ('shoe_facing', 'shoe_profile')


def required_checks(anchor=None):
    if anchor is None:
        return ASSEMBLY_CHECKS
    if anchor in ('l-upper-arm', 'r-upper-arm'):
        return ARM_CHECKS
    if anchor in ('l-upper-leg', 'r-upper-leg'):
        return LEG_CHECKS
    return ()


def checked_orientation(data, anchor=None):
    required = required_checks(anchor)
    checks = data.get('orientation_checks')
    if not isinstance(checks, dict) or set(checks) != set(required) or any(type(v) is not bool for v in checks.values()):
        raise UserError('orientation_checks 须逐侧记录实际朝向检查：' + ', '.join(required))
    if data.get('verdict') == 'ready' and not all(checks.values()):
        raise UserError('左右映射、手掌或鞋朝向仍有问题，不能 ready；先修整现有绑定并复检')
    return dict(checks)


def require_orientation_reviews(review, digest, clips, anchors):
    if review.get('sha256') != digest:
        raise UserError('朝向验收已失效，须查看当前资源')
    for clip in clips:
        for anchor in anchors:
            if not required_checks(anchor):
                continue
            item = review.get('regions', {}).get(clip, {}).get(anchor, {})
            report = item.get('report', {})
            assessment = item.get('review', {})
            if report.get('isolated') or report.get('frames', 0) < 8 or assessment.get('verdict') != 'ready':
                raise UserError('先检查当前组合动画的双臂／双鞋朝向与遮挡：' + clip + ':' + anchor)
            checked_orientation(assessment, anchor)
