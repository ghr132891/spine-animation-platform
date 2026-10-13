"""Read a rigid shoe's visible lower contour in the foot's setup frame.

This estimates a flat orientation from source pixels. It is not a perspective
conversion or a certificate that the final weighted render touches the ground.
"""
import math
import numpy as np


def sole_profile(part, ankle, foot_angle, scale, origin, height):
    """Return opaque lower-envelope samples, or None for missing shoe pixels.

    ``ankle`` is an image-pixel point. Parts and their boxes already include
    assembly transforms. Only complete rigid feet or a reviewed leg/foot chain
    are eligible; incomplete blended source segments use the ankle fallback.
    """
    if not part or not part.get('complete'):
        return None
    if part.get('chain') not in (None, 'leg'):
        return None
    alpha=np.array(part['image'].convert('RGBA'))[:,:,3]
    x0,y0,_,_=part['box']
    if part.get('chain')=='leg':
        # Ignore pants and upper-leg outlines; the visible sole lies distal to
        # the reviewed ankle. Include a small collar margin, not the whole leg.
        alpha=alpha.copy()
        cutoff=max(0,int(ankle[1]-y0-4))
        alpha[:cutoff]=0
    mask=alpha>=160
    columns=np.flatnonzero(mask.any(axis=0))
    if len(columns)<8:
        return None
    bottom=np.array([np.flatnonzero(mask[:,x])[-1]+.5 for x in columns])
    global_pixels=np.column_stack((columns+x0+.5,bottom+y0))
    # Use the broad interior lower envelope, excluding rounded tip corners.
    lo,hi=np.quantile(columns,[.1,.9])
    interior=(columns>=lo)&(columns<=hi)
    fit=global_pixels[interior]
    if len(fit)<6:
        return None
    slope,intercept=np.polyfit(fit[:,0],fit[:,1],1)
    for _ in range(3):
        residual=fit[:,1]-(fit[:,0]*slope+intercept)
        mad=np.median(np.abs(residual-np.median(residual)))
        keep=np.abs(residual-np.median(residual))<=max(2,3*mad)
        if keep.sum()<6:break
        slope,intercept=np.polyfit(fit[keep,0],fit[keep,1],1)
    # Y is up in world space; compensate the observed sole slope, rather than
    # assuming the ankle-to-toe axis is parallel to the actual shoe sole.
    observed=math.degrees(math.atan2(-float(slope),1))
    if abs(observed)>30:
        return None  # Strong perspective/pose needs a better source view.
    # Keep every lower-envelope column. Uniform subsampling can omit the true
    # contact pixel after flattening and leave the two shoes on different planes.
    sampled=global_pixels
    world=np.column_stack(((sampled[:,0]-origin[0])*scale,(height-sampled[:,1])*scale))
    ankle_world=np.array([(ankle[0]-origin[0])*scale,(height-ankle[1])*scale])
    r=math.radians(-foot_angle);c,s=math.cos(r),math.sin(r)
    rotation=np.array([[c,-s],[s,c]])
    local=(world-ankle_world)@rotation.T
    return {'points':local.tolist(),'flat_angle':float(foot_angle-observed),
            'observed_sole_angle':observed,'source':'opaque-lower-envelope',
            'setup_ground_y':float(world[:,1].min()),'sample_count':len(local)}
