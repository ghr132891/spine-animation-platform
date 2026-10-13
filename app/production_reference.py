"""Authoring patterns observed in the supplied cooking-game Spine exports.

This is guidance for independently prepared character art, not motion retargeting
or a claim that every reference contains all pixels needed for a new action.
"""
REFERENCE_PROFILE={
    'id':'compact-weighted-character-v1',
    'evidence':'evidence/source-spine-reference/structure.json',
    'source_count':8,'source_runtime':'3.8.99',
    'simple_character_slots':[6,8],
    'observed_clips':['idle','run','run2','guo','renwu'],
    'run_cycle_seconds':1.0666666667,'idle_cycle_seconds':2.0,
    'preferred_body_layout':'combined',
    'rules':[
        'Keep a continuous main costume through the waist, belt and hem; use torso group=costume with hip-to-neck pins when appropriate.',
        'Use complete arm/palm and leg/shoe artwork with several weighted bones, rather than cutting each joint into a separate alpha seam.',
        'Retain unobscured mounting overlaps at shoulders, hips, wrists and shoe collars.',
        'Inspect actual near/far draw order; most examples place legs behind the main costume, but their arm and hair order differs.',
        'Preserve face, garment shape, palette and original asymmetric decoration; adapt proportions to the new character.',
        'A continuous costume should follow torso/hip motion, not lower-leg or shoe motion; independent long hems need extra art and bones.',
        'Original behind-the-back arms may omit palms; source mesh validity does not prove that a new swing can expose them.',
        'Reference run timing and axes are observations, not a new walk template; inspect new stance, sole contact, phase and loop.',
    ],
    'limits':['No arbitrary skeleton retargeting','No guaranteed natural gait','Hair/props/independent cloth chains require task-specific layers and bones'],
}
