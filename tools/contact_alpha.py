"""Read PNG alpha boundaries for local runtime diagnostics; never write images."""
import json
import sys

import numpy as np
from PIL import Image


def main():
    request = json.loads(sys.stdin.read(32769))
    with Image.open(request['page']) as image:
        if image.format != 'PNG' or image.width * image.height > 40_000_000:
            raise ValueError('unsupported-image')
        x0, y0, x1, y1 = request['bounds']
        if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
            raise ValueError('invalid-alpha-bounds')
        cropped = image.crop((x0, y0, x1, y1))
        alpha = np.asarray(cropped.getchannel('A') if cropped.mode in ('RGBA', 'LA') else cropped.convert('RGBA').getchannel('A'))
    mask = alpha >= request['alpha_threshold']
    padded = np.pad(mask, 1, constant_values=False)
    interior = padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    yy, xx = np.nonzero(mask & ~interior)
    if len(xx) > 32_768:
        raise ValueError('alpha-boundary-limit')
    print(json.dumps({'points': [[float(x + x0) + .5, float(y + y0) + .5] for x, y in zip(xx, yy)]}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Do not return paths, source data, or third-party parser messages.
        print(json.dumps({'error': 'alpha-boundary-unavailable'}))
        sys.exit(1)
