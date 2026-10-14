"""Assemble reviewed PSD layers and actual completed limbs on a shared canvas.

This module performs registration only. It never invents an occluded limb from a
segmentation mask or clips a completed silhouette to the old visible pixels.
"""
import hashlib
import numpy as np
from PIL import Image
from .core import UserError, png_bytes
from .material_finish import finish_material

def register_sprite(image, source_pair, target_pair, size, pins):
    source=np.asarray(source_pair,dtype=float);target=np.asarray(target_pair,dtype=float)
    if source.shape!=(2,2) or target.shape!=(2,2) or not np.isfinite(source).all() or not np.isfinite(target).all():
        raise UserError('部件定位需要两对有效锚点')
    a=source[1]-source[0];b=target[1]-target[0]
    if np.linalg.norm(a)<1 or np.linalg.norm(b)<1:raise UserError('部件锚点距离太小')
    angle=np.arctan2(b[1],b[0])-np.arctan2(a[1],a[0]);scale=np.linalg.norm(b)/np.linalg.norm(a)
    matrix=scale*np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
    offset=target[0]-matrix@source[0];inverse=np.linalg.inv(matrix)
    params=np.column_stack((inverse,-inverse@offset)).ravel()
    canvas=image.convert('RGBA').transform(tuple(size),Image.Transform.AFFINE,params,resample=Image.Resampling.BICUBIC)
    mapped={name:(matrix@np.asarray(point)+offset).tolist() for name,point in pins.items()}
    report={'scale':float(scale),'matrix':matrix.tolist(),'offset':offset.tolist(),
            'source_sha256':hashlib.sha256(png_bytes(image)).hexdigest(),'alpha_clipped_to_previous_layer':False}
    return canvas,mapped,report

def group_layers(layers, size, cleanup=False):
    """Input order is explicit PSD draw order, never alphabetic gallery order."""
    result=Image.new('RGBA',tuple(size))
    for image in layers:
        if image.size!=tuple(size):raise UserError('PSD 图层必须保持原画布坐标')
        result.alpha_composite(image.convert('RGBA'))
    report=None
    if cleanup:
        result,report=finish_material(result,{'reason':'已查看实际分层，清理与主体断开的残留像素，保留连接区域',
            'operations':[{'kind':'remove-specks','polygon':[[0,0],[1,0],[1,1],[0,1]],'max_pixels':32,'max_alpha':255}]})
    return result,report

def inspect_limb_identity(limbs):
    """Reject duplicate pasted legs; this is a structural check, not art approval."""
    if set(limbs)!= {'l-upper-leg','r-upper-leg'}:raise UserError('需要两条独立完整腿鞋')
    fingerprints=[]
    for image in limbs.values():
        box=image.getbbox()
        if not box:raise UserError('腿鞋部件没有可见像素')
        fingerprints.append(hashlib.sha256(png_bytes(image.crop(box))).hexdigest())
    if len(set(fingerprints))!=2:raise UserError('左右腿不能使用完全相同的裁片冒充独立补全')
    return {'separate_limb_images':True,'fingerprints':fingerprints,'visual_verdict':'not_reviewed'}

def restore_observed_texture(completed, observed, mask):
    """Restore explicitly owned visible pixels over real completed artwork.

    Ownership limits restoration, not the independent completed silhouette.
    Callers must provide a reviewed per-side mask and retain all hidden fill.
    """
    if completed.size!=observed.size or mask.size!=observed.size:
        raise UserError('可见纹理恢复须使用同一画布')
    a=np.array(observed.convert('RGBA'));selected=np.asarray(mask.convert('L'),dtype=float)/255
    # A source fragment outside the new registered limb cannot become a loose
    # strip. Constrain the restoration only, never the completed alpha itself.
    selected*=np.asarray(completed.convert('RGBA'))[:,:,3]/255
    a[:,:,3]=np.rint(a[:,:,3].astype(float)*selected).astype('uint8')
    result=completed.convert('RGBA').copy();result.alpha_composite(Image.fromarray(a))
    return result,{'restored_visible_pixels':int((a[:,:,3]>0).sum()),'complete_alpha_not_clipped':True}
