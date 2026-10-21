"""Bounded, read-only numerical shoe-contact diagnostics using the official runtime.

This is deliberately not a natural-gait, side-view, occlusion, or physics certification.
An arbitrary Spine rig needs an explicit leg mapping and known support requirements.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from app.core import MAX_PIXELS, parse_atlas, safe_name, UserError


def require_natural_walk_contact(report):
    if report.get('status')!='assessed' or report.get('posture_checks_passed') is not True or report.get('numerical_checks_passed') is not True:
        raise UserError('自然行走的接地、滑步、循环或膝弯检查尚未全部通过；请修正后复检，可先交付待修草稿')


ROOT = Path(__file__).resolve().parents[1]
BASE_LIMITS = {
    'numerical_only': True,
    'does_not_certify': ['natural_gait', 'side_view', 'occlusion', 'physics_contact', 'asset_completeness'],
    'selected_skin_only': True,
    'read_only': True,
    'maximum_samples': 512,
    'maximum_keyframes': 512,
    'maximum_pairs': 8,
    'maximum_foot_pixels': 4096,
    'timeout_seconds': 30,
}
REASONS = {
    'missing-contact-requirements': '缺少已知支撑相位、共同地面或场景速度；请提供 stance、ground_y、scene_speed 和 pairs，不能推测任意骨架。',
    'missing-leg-mapping': '此骨架没有已知的行走接地元数据；请提供每条腿的 slot、hip、knee、foot 和 phase。',
    'unverified-contact-profile': '模板足部标记为踝骨回退，未确认实际鞋底；请修复完整足部或提供显式 pairs 和已知接地条件后重新测量。',
    'invalid-options': '接地诊断参数不合法；请使用有限数值、32 至 512 帧，以及完整腿链映射。',
    'inconsistent-scene-speed': '双脚必须使用同一个 scene_speed；不能分别指定速度来掩盖滑步。',
    'animation-not-found': '未找到指定动画；请指定资源中实际存在的动画名称。',
    'animation-not-found-or-no-duration': '指定动画不存在或没有可采样时长；静态姿势不能推断步态接地。',
    'unsupported-runtime': '当前接地诊断支持 Spine 3.8 和 4.2；请提供对应版本的资源。',
    'runtime-version-mismatch': '传入运行时版本与资源声明版本不一致；请使用匹配的 Spine 版本。',
    'resource-path-outside-export': '资源路径必须位于导出目录内，不允许跨目录路径或外部符号链接。',
    'invalid-export-resources': '导出资源不完整或格式不合法；请提供骨架 JSON、atlas 和有效 PNG 贴图。',
    'resource-complexity-limit': '资源超过接地诊断的大小或复杂度限制；请先缩减贴图页、骨骼或网格。',
    'mesh-complexity-limit': '所选部件网格过于复杂，无法在诊断预算内可靠采样。',
    'unsupported-atlas-page': '接地诊断只支持本地 PNG 图集页，且单页不超过 4000 万像素。',
    'mapping-is-not-a-continuous-leg': '腿链映射不成立；要求 knee 直接继承 hip、foot 直接继承 knee，并且 slot 有可见贴图。',
    'degenerate-leg-chain': '实际关节原点之间的腿链长度为零，无法归一化运动。',
    'degenerate-foot-transform': '脚骨在动画帧中缩放至零或变换退化，不能用塌缩的鞋形验收接地。',
    'arm-mapping-not-found': '摆臂映射骨骼不存在；请修正 arm 字段或省略该项。',
    'foot-attachment-not-visible': '所选脚部附件在当前皮肤不可见，无法判断鞋底。',
    'foot-attachment-not-opaque': '所选脚部附件在采样帧或关键帧附近含透明度变化；当前固定不透明像素跟踪不适用。',
    'alpha-boundary-unavailable': '无法读取足部不透明像素边界；请检查 PNG 图集和 UV 范围。',
    'unsupported-foot-weights': '无法读取实际脚骨权重；该网格不能用于刚性足部像素接地诊断。',
    'insufficient-rigid-foot-pixels': '属于脚骨且权重至少 98% 的不透明边界像素不足；不能用踝骨位置代替鞋底验收。',
    'foot-contour-limit': '足部边界像素超过诊断上限；请缩减贴图或简化映射部件。',
    'sole-line-not-readable': '鞋底下轮廓不足以稳定拟合倾角；当前姿态不能可靠测量平底支撑。',
    'contact-attachment-or-topology-changes': '动画更换了脚部附件、UV 或网格拓扑；当前连续像素跟踪不适用。',
    'insufficient-support-or-swing-samples': '支撑或摆动采样不足；请增加 samples 或修正已知的 stance 和 phase。',
    'skin-not-found': '指定皮肤不存在；请使用实际皮肤名称。',
    'rotate-field-version-mismatch': 'Spine 4.2 旋转关键帧使用了被忽略的 angle 字段；请修正为 value 后重新诊断。',
    'diagnostic-timeout': '接地诊断超过 30 秒预算；请减少采样、腿链数量或网格复杂度。',
    'runtime-unavailable-or-resource-limit': '本地运行时不可用或诊断资源预算不足；未完成鞋底验收。',
    'runtime-contact-diagnostic-failed': '运行时鞋底诊断失败；请先通过资源加载和运行时语义校验。',
    'non-finite-report': '运行时返回了非有限数值，鞋底验收未完成。',
}


class _NotAssessed(ValueError):
    pass


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _result(code, *, status='not_assessed'):
    return {'schema': 'contact-diagnostics-v1', 'status': status, 'reason_code': code,
            'reason': REASONS.get(code, REASONS['runtime-contact-diagnostic-failed']),
            'numerical_checks_passed': False, 'checks': {}, 'feet': [], 'limits': dict(BASE_LIMITS)}


def _name(value):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise _NotAssessed('invalid-options')
    return value


def _file(folder, name):
    try:
        relative = safe_name(name)
        target = (folder / relative).resolve(strict=True)
        if not target.is_relative_to(folder) or not target.is_file():
            raise _NotAssessed('resource-path-outside-export')
        return target
    except _NotAssessed:
        raise
    except ValueError as error:
        raise _NotAssessed('resource-path-outside-export') from error
    except OSError as error:
        raise _NotAssessed('invalid-export-resources') from error


def _resources(folder, skeleton_name, atlas_name):
    skeleton = _file(folder, skeleton_name)
    atlas = _file(folder, atlas_name)
    if skeleton.stat().st_size > 16 * 1024 * 1024 or atlas.stat().st_size > 1024 * 1024:
        raise _NotAssessed('resource-complexity-limit')
    try:
        source = json.loads(skeleton.read_text('utf-8-sig'))
        pages, _ = parse_atlas(atlas.read_text('utf-8-sig'))
        if not isinstance(source, dict) or not isinstance(source.get('bones'), list) or not isinstance(source.get('slots'), list):
            raise _NotAssessed('invalid-export-resources')
        if len(source['bones']) > 1000 or len(source['slots']) > 4096 or len(pages) > 16:
            raise _NotAssessed('resource-complexity-limit')
        # Validate every page before the 3.8 runtime's atlas callback reads it.
        for page in pages:
            image = _file(folder, str(Path(atlas_name).parent / page['name']))
            if image.stat().st_size > 64 * 1024 * 1024:
                raise _NotAssessed('resource-complexity-limit')
            with Image.open(image) as im:
                if im.format != 'PNG' or im.width * im.height > MAX_PIXELS:
                    raise _NotAssessed('unsupported-atlas-page')
                if all(size > 0 for size in page['size']) and page['size'] != [im.width, im.height]:
                    raise _NotAssessed('invalid-export-resources')
                im.verify()
        return source
    except _NotAssessed:
        raise
    except Exception as error:
        raise _NotAssessed('invalid-export-resources') from error


def _options(source, animation, options):
    if not isinstance(options, dict):
        raise _NotAssessed('invalid-options')
    allowed = {'samples', 'skin', 'pairs', 'ground_y', 'scene_speed', 'stance', 'alpha_threshold',
               'contact_tolerance', 'normalized_slip_limit', 'velocity_epsilon_phase',
               'include_samples', 'skeleton_name', 'atlas_name'}
    if set(options) - allowed:
        raise _NotAssessed('invalid-options')
    samples = options.get('samples', 128)
    if isinstance(samples, bool) or not isinstance(samples, int) or not 32 <= samples <= 512:
        raise _NotAssessed('invalid-options')
    skin = options.get('skin')
    if skin is not None:
        _name(skin)
    alpha = options.get('alpha_threshold', 160)
    if isinstance(alpha, bool) or not isinstance(alpha, int) or not 160 <= alpha <= 255:
        raise _NotAssessed('invalid-options')
    include_samples = options.get('include_samples', False)
    if not isinstance(include_samples, bool):
        raise _NotAssessed('invalid-options')
    result = {'samples': samples, 'skin': skin, 'alpha_threshold': alpha, 'include_samples': include_samples,
              'skeleton_name': options.get('skeleton_name', 'skeleton.json'),
              'atlas_name': options.get('atlas_name', 'skeleton.atlas')}
    for name, default, minimum, maximum in (
            ('contact_tolerance', .5, 1e-6, 100),
            ('normalized_slip_limit', .0005, 1e-8, 1),
            ('velocity_epsilon_phase', 1 / 4096, 1e-6, 1 / 1024)):
        value = options.get(name, default)
        if not _finite(value) or not minimum <= value <= maximum:
            raise _NotAssessed('invalid-options')
        result[name] = value
    gait = source.get('skeleton', {}).get('gait', {}) if isinstance(source.get('skeleton'), dict) else {}
    known = animation == 'walk' and isinstance(gait, dict) and gait.get('version') == 'sole-contact-v1'
    for name in ('ground_y', 'scene_speed', 'stance'):
        value = options.get(name, gait.get(name) if known else None)
        if value is None:
            raise _NotAssessed('missing-contact-requirements')
        if not _finite(value) or (name == 'stance' and not 0 < value < 1):
            raise _NotAssessed('invalid-options')
        result[name] = value
    pairs = options.get('pairs')
    if pairs is None:
        if not known:
            raise _NotAssessed('missing-leg-mapping')
        bone_names = {bone.get('name') for bone in source['bones'] if isinstance(bone, dict)}
        slot_names = {slot.get('name') for slot in source['slots'] if isinstance(slot, dict)}
        pairs = []
        for side, phase in (('l', 0), ('r', .5)):
            names = [f'{side}-upper-leg', f'{side}-lower-leg', f'{side}-foot']
            feet = gait.get('feet', {})
            foot_metadata = feet.get(side, {}) if isinstance(feet, dict) else {}
            if isinstance(foot_metadata, dict) and foot_metadata.get('source') in ('ankle-fallback', 'fallback'):
                raise _NotAssessed('unverified-contact-profile')
            advertised = (foot_metadata.get('slot') or foot_metadata.get('selected_slot')) if isinstance(foot_metadata, dict) else None
            slot = advertised or (names[2] if names[2] in slot_names else names[0])
            if not isinstance(slot, str) or not set(names) <= bone_names or slot not in slot_names:
                raise _NotAssessed('missing-leg-mapping')
            pair = {'slot': slot, 'hip': names[0], 'knee': names[1], 'foot': names[2], 'phase': phase}
            if f'{side}-hand' in bone_names:
                pair['arm'] = f'{side}-hand'
            pairs.append(pair)
        result['phase_source'] = 'sole-contact-v1:left=0,right=0.5'
    else:
        result['phase_source'] = 'explicit-leg-mapping-and-contact-requirements'
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 8:
        raise _NotAssessed('invalid-options')
    sanitized = []
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) - {'slot', 'foot', 'hip', 'knee', 'phase', 'arm', 'scene_speed'}:
            raise _NotAssessed('invalid-options')
        if 'scene_speed' in pair and (not _finite(pair['scene_speed']) or pair['scene_speed'] != result['scene_speed']):
            raise _NotAssessed('inconsistent-scene-speed')
        row = {name: _name(pair.get(name)) for name in ('slot', 'foot', 'hip', 'knee')}
        phase = pair.get('phase')
        if not _finite(phase) or not 0 <= phase < 1:
            raise _NotAssessed('invalid-options')
        row['phase'] = phase
        if pair.get('arm') is not None:
            row['arm'] = _name(pair['arm'])
        sanitized.append(row)
    if len({pair['slot'] for pair in sanitized}) != len(sanitized) or len({pair['foot'] for pair in sanitized}) != len(sanitized):
        raise _NotAssessed('invalid-options')
    result['pairs'] = sanitized
    return result


def _node():
    node = os.getenv('SPINE_AGENT_NODE') or shutil.which('node')
    if node:
        return node
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
    return str(bundled) if bundled.is_file() else None


def _all_finite(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_all_finite(item) for item in value)
    return True


def diagnose_contact(export_folder, version='4.2', animation='walk', options=None):
    """Return bounded JSON diagnostics; never alter exports or invoke paid services.

    Defaults apply only to the platform's known ``sole-contact-v1`` walk template.
    Other rigs need ``pairs=[{slot,foot,hip,knee,phase,arm?}]`` and one shared
    ``ground_y``, ``scene_speed`` and ``stance``. Speed uses the runtime world X
    axis: positive for a rightward-moving scene, negative for leftward travel.
    """
    try:
        if version not in ('3.8', '4.2'):
            raise _NotAssessed('unsupported-runtime')
        _name(animation)
        raw = {} if options is None else options
        if not isinstance(raw, dict):
            raise _NotAssessed('invalid-options')
        skeleton_name = _name(raw.get('skeleton_name', 'skeleton.json'))
        atlas_name = _name(raw.get('atlas_name', 'skeleton.atlas'))
        try:
            folder = Path(export_folder).resolve(strict=True)
        except OSError as error:
            raise _NotAssessed('invalid-export-resources') from error
        if not folder.is_dir():
            raise _NotAssessed('invalid-export-resources')
        source = _resources(folder, skeleton_name, atlas_name)
        declaration = source.get('skeleton', {}).get('spine', '') if isinstance(source.get('skeleton'), dict) else ''
        if isinstance(declaration, str) and declaration and not declaration.startswith(version):
            raise _NotAssessed('runtime-version-mismatch')
        if not isinstance(source.get('animations'), dict) or animation not in source['animations']:
            raise _NotAssessed('animation-not-found')
        validated = _options(source, animation, raw)
        validated['python'] = sys.executable  # Fixed local helper, never an option-controlled executable.
        node = _node()
        if not node:
            raise _NotAssessed('runtime-unavailable-or-resource-limit')
        process = subprocess.run([node, '--max-old-space-size=384', str(ROOT / 'tools/diagnose_contact.cjs'),
                                  str(folder), version, animation], input=json.dumps(validated),
                                 text=True, capture_output=True, timeout=30, cwd=ROOT)
        if process.returncode != 0 or len(process.stdout) > 2 * 1024 * 1024:
            raise _NotAssessed('runtime-unavailable-or-resource-limit')
        report = json.loads(process.stdout)
        if not isinstance(report, dict) or report.get('status') not in ('assessed', 'not_assessed', 'failed'):
            raise _NotAssessed('runtime-unavailable-or-resource-limit')
        if report['status'] != 'assessed':
            return _result(report.get('reason_code', 'runtime-contact-diagnostic-failed'), status=report['status'])
        if not _all_finite(report):
            return _result('non-finite-report', status='failed')
        report['schema'] = 'contact-diagnostics-v1'
        report['limits'] = {**BASE_LIMITS, **report.get('limits', {})}
        return report
    except _NotAssessed as error:
        return _result(str(error))
    except subprocess.TimeoutExpired:
        return _result('diagnostic-timeout')
    except Exception:
        # No source paths, third-party exceptions or runtime output in public errors.
        return _result('runtime-contact-diagnostic-failed', status='failed')
