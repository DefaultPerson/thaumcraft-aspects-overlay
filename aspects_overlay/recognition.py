"""Template recognition for the unmodified Thaumcraft 4 research parchment."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time

import cv2
import numpy as np

from .assets import Assets
from .matching import fit_rainbow_icon


class RecognitionError(ValueError):
    pass


class BoardChanged(RecognitionError):
    pass


@dataclass
class Board:
    cells: set[tuple[int, int]]
    anchors: dict[tuple[int, int], str]
    center: tuple[float, float]
    scale: float
    parchment: tuple[int, int, int, int]
    confidence: dict[tuple[int, int], float] = field(default_factory=dict)
    tentative: set[tuple[int, int]] = field(default_factory=set)

    def pixel(self, cell: tuple[int, int]) -> tuple[float, float]:
        q, r = cell
        return (self.center[0] + q * 13.5 * self.scale,
                self.center[1] + (r + q / 2) * 9 * math.sqrt(3) * self.scale)

    def signature(self):
        return (frozenset(self.cells), frozenset(self.anchors.items()))

    def to_dict(self):
        return {"cells": [list(c) for c in sorted(self.cells)],
                "anchors": [{"q": q, "r": r, "aspect": a,
                             "confidence": round(self.confidence.get((q, r), 0), 3)}
                            for (q, r), a in sorted(self.anchors.items())],
                "center": list(self.center), "scale": self.scale,
                "parchment": list(self.parchment)}


def find_parchment(image: np.ndarray) -> tuple[int, int, int, int]:
    if image.ndim != 3 or image.shape[2] != 3:
        raise RecognitionError("Ожидается цветной снимок окна.")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([12, 60, 130]), np.array([40, 210, 255]))
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    found = []
    for x, y, w, h, area in stats[1:]:
        if w < 140 or h < 140 or not .88 < w / h < 1.12 or area < w * h * .65:
            continue
        found.append((int(area), (int(x), int(y), int(w), int(h))))
    if not found:
        raise RecognitionError("Поле исследований не найдено. Откройте стол с запиской; уберите подсказку с поля.")
    return max(found)[1]


def research_complete(image: np.ndarray, assets: Assets, parchment=None) -> bool:
    """The native golden copy button appears only for a completed research note."""
    rect = find_parchment(image)
    if parchment is not None and rect != parchment:
        return False
    x, y, w, h = rect
    scale = (w+h)/296
    left, top = round(x-65*scale), round(y-5*scale)
    crop = image[top:round(y+21*scale), left:round(x-29*scale)]
    icon = cv2.resize(assets.table[203:220, 235:253], (round(18*scale), round(17*scale)))
    if crop.shape[0] < icon.shape[0] or crop.shape[1] < icon.shape[1]:
        return False
    alpha = icon[..., 3:]/255
    bg = np.median(crop.reshape(-1, 3), axis=0)
    template = (icon[..., :3][..., ::-1]*alpha + bg*(1-alpha)).astype(np.uint8)
    score = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
    return float(np.nan_to_num(score, nan=-1).max()) >= .85


@dataclass
class RecognitionCache:
    context: tuple | None = None
    cells: dict = field(default_factory=dict)


def _uncovered_glyph(crop, icons, excluded, padding, dynamic_colors=()):
    """Recognize the visible part of a glyph without its known neighbours."""
    size = crop.shape[0] - 2*padding
    usable = 255-excluded[padding:padding+size, padding:padding+size]
    if not excluded.any() or np.count_nonzero(usable) < size*size*.6:
        return None
    background = np.median(crop[excluded == 0], axis=0)
    ranking = []
    rendered = {}
    for name, icon in icons.items():
        if name in dynamic_colors:
            match = fit_rainbow_icon(crop, icon, background, excluded)
            if match is not None:
                score, rendered[name], position = match
                ranking.append((score, name, position))
            continue
        alpha = icon[..., 3:]/255.0
        template = (icon[..., :3][..., ::-1]*alpha + background*(1-alpha)).astype(np.uint8)
        scores = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED, mask=usable)
        scores = np.nan_to_num(scores, nan=-1, posinf=-1, neginf=-1)
        _, score, _, position = cv2.minMaxLoc(scores)
        ranking.append((score, name, position))
    ranking.sort(reverse=True)
    score, aspect, (ox, oy) = ranking[0]
    if score < .70 or score-ranking[1][0] < .18:
        return None
    icon = rendered.get(aspect, icons[aspect])
    visible = excluded[oy:oy+size, ox:ox+size] == 0
    glyph = icon[..., 3] > 25
    if np.count_nonzero(glyph & visible) < np.count_nonzero(glyph)*.65:
        return None
    core = icon[..., 3] > 230
    if core.sum() < size*size*.02:
        core = icon[..., 3] > 216
    if icon[..., 3].max() < 216:
        core = icon[..., 3] > .8*icon[..., 3].max()
    core &= visible
    if core.sum() < size*size*.02:
        return None
    alpha = icon[..., 3:]/255.0
    template = (icon[..., :3][..., ::-1]*alpha + background*(1-alpha)).astype(np.uint8)
    observed = crop[oy:oy+size, ox:ox+size]
    if np.mean(np.abs(observed[core].astype(float)-template[core])) >= 18:
        return None
    return aspect, score


def recognize(image: np.ndarray, assets: Assets, cache: RecognitionCache | None = None,
              temporal: bool = False) -> Board:
    x, y, w, h = find_parchment(image)
    scale = (w + h) / (2 * 148)
    board = Board(set(), {}, (x + w / 2, y + h / 2), scale, (x, y, w, h))
    if cache is not None:
        context = (id(assets), board.parchment)
        if cache.context != context:
            cache.context = context
            cache.cells.clear()
    size = round(16 * scale)
    padding = max(2, round(scale))
    half = size // 2
    hex_alpha = cv2.resize(assets.hex[..., 3], (size, size), interpolation=cv2.INTER_LINEAR)
    hex_template = 255 - hex_alpha
    icons = {name: cv2.resize(icon, (size, size), interpolation=cv2.INTER_LINEAR)
             for name, icon in assets.icons.items()}
    dynamic_colors = getattr(assets, 'dynamic_colors', ())
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    uncertain = []
    empty_candidates = []
    for q in range(-4, 5):
        for r in range(-4, 5):
            if max(abs(q), abs(r), abs(q + r)) > 4:
                continue
            cell = q, r
            cx, cy = map(round, board.pixel(cell))
            left, top = cx - half - padding, cy - half - padding
            side = size + padding * 2
            crop = image[top:top + side, left:left + side]
            if crop.shape[:2] != (side, side):
                raise RecognitionError("Часть поля находится за краем снимка.")
            # Wide, pale glyphs (Mortuus) occupy over half the crop. Sample
            # outside the icon so their own color cannot become the background.
            border = np.concatenate((crop[:padding].reshape(-1, 3),
                                     crop[-padding:].reshape(-1, 3),
                                     crop[padding:-padding, :padding].reshape(-1, 3),
                                     crop[padding:-padding, -padding:].reshape(-1, 3)))
            def rank(bg):
                ranking = []
                rendered = {}
                for name, icon in icons.items():
                    if name in dynamic_colors:
                        match = fit_rainbow_icon(crop, icon, bg)
                        if match is not None:
                            score, rendered[name], _ = match
                            ranking.append((score, name))
                        continue
                    alpha = icon[..., 3:] / 255.0
                    template = (icon[..., :3][..., ::-1] * alpha + bg * (1 - alpha)).astype(np.uint8)
                    scores = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
                    ranking.append((float(np.nan_to_num(scores, nan=-1).max()), name))
                return sorted(ranking, reverse=True), rendered

            cached = cache.cells.get(cell) if cache is not None else None
            if cached is not None and np.array_equal(crop, cached[0]):
                score, aspect, margin, pale_match, pixel_error = cached[1]
            else:
                background = np.median(crop.reshape(-1, 3), axis=0)
                ranking, rendered = rank(background)
                score, aspect = ranking[0]
                margin = score - ranking[1][0]
                if score >= .49 and (score < .62 or margin < .14):
                    alternate, alternate_rendered = rank(np.median(border, axis=0))
                    if alternate[0][1] == aspect and alternate[0][0] >= .62 and alternate[0][0]-alternate[1][0] >= .14:
                        score = alternate[0][0]
                        margin = score-alternate[1][0]
                        rendered = alternate_rendered
                pale_match = False
                pixel_error = float("inf")
                if score >= .42:
                    icon = rendered.get(aspect, icons[aspect])
                    alpha = icon[..., 3:] / 255.0
                    template = (icon[..., :3][..., ::-1]*alpha + background*(1-alpha)).astype(np.uint8)
                    scores = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
                    _, _, _, (ox, oy) = cv2.minMaxLoc(scores)
                    opaque = icon[..., 3] > 216
                    if opaque.sum() >= size*size*.1:
                        observed = crop[oy:oy+size, ox:ox+size]
                        pixel_error = np.mean(np.abs(observed[opaque].astype(float)-template[opaque]))
                        pale_match = pixel_error < 10 and margin >= .13
                    if not pale_match and score >= .55 and margin >= .14:
                        # Link glows alter the parchment behind a glyph, and
                        # interpolated edges still contain that background.
                        # Confirm the opaque core instead. Thin glyphs such as
                        # Vacuos have too few opaque pixels for the broad mask.
                        core = icon[..., 3] > 230
                        if core.sum() < size*size*.02:
                            core = opaque
                        if core.sum() >= size*size*.02:
                            observed = crop[oy:oy+size, ox:ox+size]
                            core_error = np.mean(np.abs(observed[core].astype(float)-template[core]))
                            pixel_error = min(pixel_error, core_error)
                            pale_match = core_error < 10
                    if icon[..., 3].max() < 216:
                        # Spiritus has no opaque pixels. Validate its strongest
                        # alpha pixels against the composited colour instead of
                        # treating its low contrast as an empty hex.
                        support = icon[..., 3] > .8*icon[..., 3].max()
                        if support.sum() >= size*size*.05:
                            observed = crop[oy:oy+size, ox:ox+size]
                            pixel_error = np.mean(np.abs(observed[support].astype(float)-template[support]))
                            pale_match = score >= .55 and margin >= .25 and pixel_error < 18
                        if score >= .5 and margin >= .25 and not pale_match and score < .62:
                            raise RecognitionError(f"Неуверенно распознан полупрозрачный аспект {cell}; повторите снимок.")
                if cache is not None:
                    cache.cells[cell] = (crop.copy(), (score, aspect, margin, pale_match, pixel_error))
            confident = (score >= .62 and margin >= .14) or pale_match
            # A distinct shape and matching foreground colour can survive a
            # glow even when whole-patch correlation drops. These candidates
            # are usable only after read_board confirms the entire board again.
            tentative = temporal and not confident and score >= .55 and margin >= .25 and pixel_error < 18
            if confident or tentative:
                board.cells.add(cell)
                board.anchors[cell] = aspect
                board.confidence[cell] = score
                if tentative:
                    board.tentative.add(cell)
                continue
            empty_candidates.append((cell, left, top, side, score, pixel_error))
    def occupied_mask():
        occupied = np.zeros(gray.shape, np.uint8)
        for cell, aspect in board.anchors.items():
            cx, cy = map(round, board.pixel(cell))
            mask = (icons[aspect][..., 3] > 25).astype(np.uint8)*255
            region = occupied[cy-half:cy-half+size, cx-half:cx-half+size]
            np.maximum(region, mask, out=region)
        return cv2.dilate(occupied, np.ones((padding*2+1, padding*2+1), np.uint8))

    # A neighbour can cover part of an occupied glyph too (Herba over Messis).
    # Exclude known neighbours before rejecting a glyph, not just when finding
    # empty hex borders. Rebuild once to account for newly recovered neighbours.
    occupied = occupied_mask()
    for _ in range(2):
        recovered = False
        for cell, left, top, side, score, _ in empty_candidates:
            if cell in board.anchors or score < .42:
                continue
            match = _uncovered_glyph(image[top:top+side, left:left+side], icons,
                                     occupied[top:top+side, left:left+side], padding, dynamic_colors)
            if match is not None:
                aspect, confidence = match
                board.cells.add(cell)
                board.anchors[cell] = aspect
                board.confidence[cell] = confidence
                recovered = True
        if not recovered:
            break
        occupied = occupied_mask()
    for cell, left, top, side, score, pixel_error in empty_candidates:
        if cell in board.anchors:
            continue
        patch = gray[top:top+side, left:left+side]
        crop = image[top:top+side, left:left+side]
        neighbour_mask = occupied[top:top+side, left:left+side]
        usable = 255-neighbour_mask[padding:padding+size, padding:padding+size]
        scores = cv2.matchTemplate(patch, hex_template, cv2.TM_CCOEFF_NORMED, mask=usable)
        hex_score = float(np.nan_to_num(scores, nan=-1, posinf=-1, neginf=-1).max())
        hsv_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        alien_color = (((hsv_crop[..., 0] > 45) | (hsv_crop[..., 0] < 8)) &
                       (hsv_crop[..., 1] > 110) & (neighbour_mask == 0))
        # Unknown coloured glyphs can obscure the hex border completely. They
        # must not disappear from the board just because no empty hex matches.
        if float(alien_color.mean()) > .09:
            uncertain.append(cell)
        elif hex_score >= .28:
            board.cells.add(cell)
            board.confidence[cell] = hex_score
        elif score > .49 and pixel_error < 30:
            uncertain.append(cell)
    if uncertain:
        raise RecognitionError("Неуверенно распознаны клетки " + ", ".join(map(str, uncertain)) +
                               ". Уберите курсор с поля и повторите расчёт; стандартные текстуры обязательны.")
    if len(board.anchors) < 2 or len(board.cells) < len(board.anchors) + 1:
        raise RecognitionError("Не найдено достаточно клеток и исходных аспектов для исследования.")
    # All recognized cells must belong to one spatial grid; reject isolated noise.
    from .solver import NEIGHBORS
    seen = {next(iter(board.cells))}
    pending = list(seen)
    while pending:
        q, r = pending.pop()
        for dq, dr in NEIGHBORS:
            adjacent = q + dq, r + dr
            if adjacent in board.cells and adjacent not in seen:
                seen.add(adjacent)
                pending.append(adjacent)
    if seen != board.cells:
        raise RecognitionError("Распознанная сетка разорвана; повторите снимок без всплывающих подсказок.")
    return board


def read_board(capture_frame, assets, *, cache=None, expected=None, previous=None,
               allow_complete=False, timeout=5.0, check=lambda: None,
               wait=time.sleep, on_retry=lambda message: None, recognizer=None,
               clock=time.monotonic):
    """Wait for a whole-board agreement; never assemble cells from old frames.

    A known board can pass immediately on a confident frame. Initial readings,
    tentative glyphs, and mismatches require consecutive agreeing captures.
    During a drop acknowledgement only the exact pre-drop board may linger.
    """
    recognizer = recognizer or recognize
    cache = cache if cache is not None else RecognitionCache()
    end = clock() + timeout
    last_signature, streak, changed = None, 0, False
    reason = "Жду устойчивого изображения поля."
    while clock() < end:
        check()
        frame = capture_frame()
        try:
            if allow_complete and research_complete(frame, assets, expected.parchment if expected else None):
                check()
                return None, frame
            current = recognizer(frame, assets, cache, True)
        except RecognitionError as exc:
            last_signature, streak = None, 0
            changed = False
            reason = str(exc)
        else:
            check()
            signature = (current.parchment, current.signature())
            streak = streak + 1 if signature == last_signature else 1
            last_signature = signature
            matches = expected is None or signature == (expected.parchment, expected.signature())
            changed = False
            required = 2 if expected is None or current.tentative else 1
            if matches and streak >= required:
                return current, frame
            if not matches:
                unchanged = previous is not None and signature == (previous.parchment, previous.signature())
                changed = not unchanged
                reason = "Жду подтверждения изменения клетки." if unchanged else "Перепроверяю изменение поля."
            else:
                reason = "Подтверждаю поле на следующем кадре."
        on_retry(reason)
        remaining = end - clock()
        if remaining > 0:
            wait(min(.10, remaining))
    check()
    if changed and streak >= 2:
        raise BoardChanged("Поле устойчиво отличается от схемы. Остановлено без дальнейших действий.")
    raise RecognitionError(f"Не удалось получить устойчивый снимок за {timeout:g} с. {reason}")
