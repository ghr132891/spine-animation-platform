"""Plan a 2D walk using the visible sole, rather than only the ankle origin.

All positions use the caller's world units, with Y pointing up. Angles are
absolute world degrees. This module does not infer anatomy, image perspective,
skin weights, a ground plane, or the setup-to-animation rotation conversion.
"""
import math


def _number(value, name):
    if isinstance(value, bool):
        raise ValueError(name + ' must be a finite number')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(name + ' must be a finite number') from None
    if not math.isfinite(value):
        raise ValueError(name + ' must be a finite number')
    return value


def _point(value, name):
    try:
        if len(value) != 2:
            raise ValueError(name + ' must contain two finite coordinates')
        return (_number(value[0], name + '.x'), _number(value[1], name + '.y'))
    except (TypeError, IndexError, KeyError):
        raise ValueError(name + ' must contain two finite coordinates') from None


def walk_phase(cycle, stance=0.6, pitch=6.0):
    """Return a looping stance/swing sample for one leg.

    ``cycle`` can include a half-cycle offset for the other leg. ``dx`` is a
    normalized displacement; multiply by the desired stride and facing sign.
    During stance it moves from +0.5 to -0.5 at constant speed for an in-place
    walk. Swing uses a cubic Hermite curve whose endpoint speed matches stance,
    so both displacement and speed match across landing, liftoff, and the loop.
    A small backward/forward overshoot at the ends is intentional deceleration,
    not a discontinuity. ``clearance`` and ``pitch_deg`` have zero endpoint
    speed; pitch remains zero during stance.

    The default 60% stance gives two brief periods of double support. Applying
    root translation at stride / (stance * period) cancels stance foot motion
    for a travelling walk. Perspective and the visible result still need QA.
    """
    cycle = _number(cycle, 'cycle') % 1.0
    stance = _number(stance, 'stance')
    pitch = _number(pitch, 'pitch')
    if not 0 < stance < 1:
        raise ValueError('stance must be between zero and one')
    if not 0 <= pitch <= 45:
        raise ValueError('pitch must be between zero and 45 degrees')
    supporting = cycle < stance
    if supporting:
        progress = cycle / stance
        dx = 0.5 - progress
        clearance = 0.0
    else:
        progress = (cycle - stance) / (1.0 - stance)
        u = progress
        endpoint_speed = -(1.0 - stance) / stance
        dx = -0.5 + (3*u*u - 2*u*u*u) + endpoint_speed*(2*u*u*u - 3*u*u + u)
        clearance = math.sin(math.pi*u)**2
    return {'cycle': cycle, 'stance': supporting, 'progress': progress,
            'dx': dx, 'clearance': clearance, 'pitch_deg': pitch*clearance}


def spine_bezier_timeline(sample, times, axes=('value',), angular=False):
    """Sample a scalar rotation or XY translation as a Spine 4.2 timeline.

    Control points use absolute time/value coordinates. Neighboring intervals
    share numerically estimated endpoint tangents, reducing the sharp speed
    changes of sparse linear keys. ``sample(time)`` must return a mapping with
    the requested axes and support times just outside the supplied interval;
    a looping caller should wrap its phase there. Rotation differences use the
    shortest angular path so crossing +/-180 cannot create a 360-degree spin.

    This is a curve encoding, not a contact guarantee or strict runtime C1
    claim. The official runtime approximates Beziers; textured contact and
    finite-difference speed still need to be checked between keys.
    """
    times = [_number(time, 'time') for time in times]
    axes = tuple(axes)
    if axes not in (('value',), ('x', 'y')):
        raise ValueError('axes must be value for rotation or x/y for translation')
    if len(times) < 2 or times[0] < 0 or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError('times must contain at least two increasing nonnegative times')
    if angular and axes != ('value',):
        raise ValueError('angular timelines must use the value axis')

    def values(time):
        value = sample(time)
        if not isinstance(value, dict) or any(axis not in value for axis in axes):
            raise ValueError('sample must provide the requested axes')
        return {axis: _number(value[axis], 'sample.'+axis) for axis in axes}

    delta = (times[-1]-times[0])*1e-5
    samples = [values(time) for time in times]
    if angular:
        for previous, current in zip(samples, samples[1:]):
            current['value'] = previous['value']+(current['value']-previous['value']+180) % 360-180
    tangents = []
    for time in times:
        before, after = values(time-delta), values(time+delta)
        tangents.append({axis: (((after[axis]-before[axis]+180) % 360-180)
                               if angular else after[axis]-before[axis])/(2*delta)
                         for axis in axes})
    result = []
    for i, (time, value) in enumerate(zip(times, samples)):
        frame = {'time': time, **value}
        if i+1 < len(times):
            third = (times[i+1]-time)/3
            frame['curve'] = [coordinate for axis in axes for coordinate in
                              (time+third, value[axis]+tangents[i][axis]*third,
                               times[i+1]-third, samples[i+1][axis]-tangents[i+1][axis]*third)]
        result.append(frame)
    return result


def sole_target(home_ankle, sole_points_local, angle_deg, phase, stride, lift,
                facing=1, ground_y=0):
    """Place an ankle so its sampled sole lies on or above the requested plane.

    ``sole_points_local`` are visible sole samples in the foot bone's local
    frame, relative to its ankle origin. Convert source pixels to this frame
    before calling. ``angle_deg`` is the desired absolute foot angle, including
    any intentional swing pitch; this function does not add pitch itself.

    The lowest rotated sole sample touches ``ground_y`` in stance and
    ``ground_y + lift * phase['clearance']`` in swing. This guarantees contact
    for the supplied samples only; mixed skin weights, unsampled toe pixels,
    a wrong profile, or an unreachable leg can invalidate rendered contact.
    ``sole_points`` in the result are the corresponding world positions.
    """
    home_ankle = _point(home_ankle, 'home_ankle')
    try:
        points = [_point(p, 'sole_point') for p in sole_points_local]
    except TypeError:
        raise ValueError('sole_points_local must contain visible sole points') from None
    if not points:
        raise ValueError('sole_points_local must contain visible sole points')
    angle_deg = _number(angle_deg, 'angle_deg')
    stride = _number(stride, 'stride')
    lift = _number(lift, 'lift')
    facing = _number(facing, 'facing')
    ground_y = _number(ground_y, 'ground_y')
    if stride < 0 or lift < 0:
        raise ValueError('stride and lift must be nonnegative')
    if facing not in (-1, 1):
        raise ValueError('facing must be -1 or 1')
    if not isinstance(phase, dict) or type(phase.get('stance')) is not bool:
        raise ValueError('phase must contain a boolean stance flag')
    dx = _number(phase.get('dx'), 'phase.dx')
    clearance = _number(phase.get('clearance'), 'phase.clearance')
    if not 0 <= clearance <= 1:
        raise ValueError('phase.clearance must be between zero and one')
    radians = math.radians(angle_deg)
    c, s = math.cos(radians), math.sin(radians)
    rotated = [(c*x-s*y, s*x+c*y) for x, y in points]
    contact_y = ground_y + (0.0 if phase['stance'] else lift*clearance)
    ankle = (home_ankle[0]+dx*stride*facing,
             contact_y-min(y for _, y in rotated))
    sole = [(ankle[0]+x, ankle[1]+y) for x, y in rotated]
    return {'ankle': ankle, 'sole_points': sole,
            'sole_min_y': min(y for _, y in sole), 'contact_y': contact_y,
            'stance': phase['stance'], 'angle_deg': angle_deg}


def solve_two_bone(hip, ankle, upper_length, lower_length, bend=-1):
    """Solve a fixed-length planar leg and expose any unreachable target.

    ``bend`` selects one of the two elbow/knee branches (+1 or -1). For the
    platform's Y-up humanoid walk, use ``-facing``. Returned upper/lower angles
    are absolute world degrees; ``bend_angle`` is lower relative to upper.
    Convert them to the skeleton's setup-relative rotations at the call site.

    Targets outside [abs(L1-L2), L1+L2] are projected radially onto that interval;
    lengths are never stretched. ``reach`` identifies projection or a singular
    fully folded/straight pose. ``clamped`` marks projection only. At a zero
    target distance the otherwise undefined direction is chosen downward.
    Always check this diagnostic before claiming the sole is on the ground.
    """
    hip = _point(hip, 'hip')
    ankle = _point(ankle, 'ankle')
    upper_length = _number(upper_length, 'upper_length')
    lower_length = _number(lower_length, 'lower_length')
    bend = _number(bend, 'bend')
    if upper_length <= 0 or lower_length <= 0:
        raise ValueError('bone lengths must be positive')
    if bend not in (-1, 1):
        raise ValueError('bend must be -1 or 1')
    vx, vy = ankle[0]-hip[0], ankle[1]-hip[1]
    distance = math.hypot(vx, vy)
    minimum, maximum = abs(upper_length-lower_length), upper_length+lower_length
    solved_distance = min(maximum, max(minimum, distance))
    clamped = solved_distance != distance
    if distance > maximum:
        reach = 'too_far'
    elif distance < minimum:
        reach = 'too_close'
    elif math.isclose(distance, minimum, abs_tol=maximum*1e-10) or math.isclose(distance, maximum, abs_tol=maximum*1e-10):
        reach = 'singular'
    else:
        reach = 'reachable'
    direction = math.atan2(vy, vx) if distance else -math.pi/2
    cosine = (solved_distance**2-upper_length**2-lower_length**2)/(2*upper_length*lower_length)
    q2 = bend*math.acos(min(1.0, max(-1.0, cosine)))
    q1 = direction-math.atan2(lower_length*math.sin(q2), upper_length+lower_length*math.cos(q2))
    knee = (hip[0]+upper_length*math.cos(q1), hip[1]+upper_length*math.sin(q1))
    solved_ankle = (knee[0]+lower_length*math.cos(q1+q2),
                   knee[1]+lower_length*math.sin(q1+q2))
    return {'hip': hip, 'knee': knee, 'ankle': solved_ankle,
            'upper_angle': math.degrees(q1), 'lower_angle': math.degrees(q1+q2),
            'bend_angle': math.degrees(q2), 'requested_ankle': ankle,
            'requested_distance': distance, 'solved_distance': solved_distance,
            'reach': reach, 'clamped': clamped}
