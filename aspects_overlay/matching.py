"""Match aspect artwork whose mod changes its RGB tint every frame."""
import cv2
import numpy as np


def fit_rainbow_icon(crop, icon, background, excluded=None):
    """Fit a saturated rainbow tint, then verify the full coloured shape.

    Alfheim uses HSB saturation/value 1: at least one RGB channel is full
    brightness. Those channels propose alignment; opaque pixels estimate tint.
    """
    height, width = icon.shape[:2]
    alpha = icon[..., 3:]/255.0
    ink = icon[..., :3][..., ::-1]*alpha
    paper = background*(1-alpha)
    # Resized antialiased edges mix tint and background differently in-game.
    # Estimate colour from the opaque centre, retaining the whole shape check.
    core = (icon[..., 3] > 230) & (icon[..., :3].max(axis=2) > 180)
    if core.sum() < height*width*.05:
        return None
    mask = None
    if excluded is not None:
        py, px = (crop.shape[0]-height)//2, (crop.shape[1]-width)//2
        mask = 255-excluded[py:py+height, px:px+width]
        if np.count_nonzero(mask) < height*width*.6:
            return None
    positions = set()
    for channel in range(3):
        template = (ink[..., channel]+paper[..., channel]).astype(np.uint8)
        scores = cv2.matchTemplate(crop[..., channel], template, cv2.TM_CCOEFF_NORMED, mask=mask)
        scores = np.nan_to_num(scores, nan=-1, posinf=-1, neginf=-1)
        _, score, _, position = cv2.minMaxLoc(scores)
        if score >= .7:
            positions.add(position)
    best = None
    for ox, oy in positions:
        visible = core.copy()
        if excluded is not None:
            visible &= excluded[oy:oy+height, ox:ox+width] == 0
        if visible.sum() < max(height*width*.05, core.sum()*.65):
            continue
        observed = crop[oy:oy+height, ox:ox+width].astype(float)
        tint = np.clip((ink[visible]*(observed-paper)[visible]).sum(axis=0) /
                       (np.square(ink[visible]).sum(axis=0)+1e-9), 0, 1)
        if tint.max() < .85 or tint.min() > .35:
            continue
        template = (ink*tint+paper).astype(np.uint8)
        if np.mean(np.abs(observed[visible]-template[visible])) >= 18:
            continue
        scores = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED, mask=mask)
        score = float(np.nan_to_num(scores, nan=-1, posinf=-1, neginf=-1)[oy, ox])
        if score >= .80 and (best is None or score > best[0]):
            tinted = icon.copy()
            tinted[..., :3] = np.round(icon[..., :3]*tint[::-1]).astype(np.uint8)
            best = score, tinted, (ox, oy)
    return best
