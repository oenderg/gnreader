"""Render a decoded Goodnotes page to PNG."""
from __future__ import annotations

import io
import math
import random
import re
import time
import zipfile
from dataclasses import dataclass, field

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw, ImageFont

from .model import (FillShapeItem, LineItem, Notebook, Page, ShapeItem,
                    StickyNoteItem, Stroke, TextBox)
from . import live_attachments

FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Helvetica.ttf",
    "/System/Library/Fonts/SFNS.ttf",
)

DEFAULT_W, DEFAULT_H = 595.28, 841.89


@dataclass
class RenderResult:
    path: str
    warnings: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"path": self.path, "warnings": self.warnings, "stats": self.stats}

# Dynamic template SVGs in Goodnotes.app: grid.svg / ruledWide.svg /
# ruledNarrow.svg / dottedgrid.svg.  All use a 0.5 pt line or dot on 247,246,233.
TEMPLATE_LINE = (208, 210, 211)
TEMPLATE_PAPER = (247, 246, 233)


def _paper_color(template: str) -> tuple[int, int, int]:
    tail = template.rsplit(" - ", 1)[-1].strip().lower() if " - " in template else ""
    if tail in ("white", "blank"):
        return (255, 255, 255)
    if tail in ("black", "darkgrey", "dark"):
        return (40, 40, 40)
    return TEMPLATE_PAPER


def _template_background(page: Page, size: tuple[int, int]) -> Image.Image | None:
    kind = page.template_kind
    if not kind:
        return None
    img = Image.new("RGB", size, _paper_color(page.template))
    draw = ImageDraw.Draw(img)
    sx = size[0] / max(1e-6, page.paper_width)
    sy = size[1] / max(1e-6, page.paper_height)
    lw = max(1, round(0.5 * min(sx, sy)))
    if kind == "squared":
        step = 16.0
        # The exported PDF puts the first 16 pt grid line at 15 pt, not at the
        # SVG primitive's 8 pt offset (measured on private pages).
        x = 15.0
        while x < page.paper_width:
            draw.line([(x * sx, 0), (x * sx, size[1])], fill=TEMPLATE_LINE, width=lw)
            x += step
        y = 15.0
        while y < page.paper_height:
            draw.line([(0, y * sy), (size[0], y * sy)], fill=TEMPLATE_LINE, width=lw)
            y += step
    elif kind in ("ruled_wide", "ruled_narrow"):
        step = 25.0 if kind == "ruled_wide" else 16.0
        y0 = 50.0
        y1 = page.paper_height * 0.9
        y = y0
        while y < y1:
            draw.line([(0, y * sy), (size[0], y * sy)], fill=TEMPLATE_LINE, width=lw)
            y += step
    elif kind == "dotted":
        step = 16.0
        r = max(1.0, 0.5 * min(sx, sy))
        y = 15.0
        while y < page.paper_height:
            x = 10.0
            while x < page.paper_width:
                cx, cy = x * sx, y * sy
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=TEMPLATE_LINE)
                x += step
            y += step
    return img


def _background(notebook: Notebook, page: Page, size: tuple[int, int]) -> Image.Image:
    if page.template_kind:
        img = _template_background(page, size)
        if img is not None:
            return img
    if page.attachment:
        try:
            with zipfile.ZipFile(notebook.path) as z:
                data = z.read(page.attachment)
            doc = pdfium.PdfDocument(io.BytesIO(data))
            idx = min(page.attachment_page, len(doc) - 1)
            pdf_page = doc[idx]
            w, h = pdf_page.get_size()
            img = pdf_page.render(scale=max(size[0] / w, size[1] / h)).to_pil().convert("RGB")
            return img.resize(size, Image.LANCZOS)
        except Exception:
            pass
    return Image.new("RGB", size, "white")


def _paths(stroke: Stroke, steps: int = 8):
    """Yield subpaths as lists of (x, y, width).

    `quadCurveTo` is a real quadratic Bézier from the current point through
    `control` to `end`; the stored radii are interpolated.  A new `moveTo`
    starts a new subpath (pen lift), so separate subpaths are never joined.
    """
    current = None
    out = []
    for cmd in stroke.commands:
        pts = cmd.get("points") or []
        if cmd["type"] == "moveTo" and pts:
            if current:
                out.append(current)
            current = [tuple(pts[0])]
        elif cmd["type"] == "quadCurveTo" and len(pts) == 2 and current:
            control, end = pts
            p0 = current[-1]
            for i in range(1, steps + 1):
                t = i / steps
                mt = 1.0 - t
                current.append((
                    mt * mt * p0[0] + 2 * mt * t * control[0] + t * t * end[0],
                    mt * mt * p0[1] + 2 * mt * t * control[1] + t * t * end[1],
                    mt * mt * p0[2] + 2 * mt * t * control[2] + t * t * end[2],
                ))
    if current:
        out.append(current)
    return out


def _samples(stroke: Stroke, steps: int = 8):
    for path in _paths(stroke, steps):
        yield from path


def _outline_polygons(stroke: Stroke) -> list[list[tuple[float, float]]]:
    """Build the exact PencilStroke outline from fields 5-11.

    counts[i] is the number of point-commands in outline group i; types is the
    flat command list (0=move point, 2=cubic via 3 points, 3=arc via
    center/radius/start/end).  Arrays are consumed in that order.
    """
    o = stroke.outline or {}
    counts = o.get("counts") or []
    types = o.get("types") or []
    move = o.get("move") or []
    cubic = o.get("cubic") or []
    arc = o.get("arc") or []
    cw = o.get("clockwise") or []
    mi = ci = ai = wi = ti = 0
    polys = []
    for count in counts:
        if count <= 0 or ti >= len(types):
            continue
        if types[ti] != 0 or mi + 2 > len(move):
            return []
        x, y = move[mi], move[mi + 1]
        mi += 2
        ti += 1
        path = [(x, y)]
        for _ in range(count - 1):
            if ti >= len(types):
                break
            t = types[ti]
            ti += 1
            if t == 2:
                if ci + 6 > len(cubic):
                    return []
                c1 = (cubic[ci], cubic[ci + 1])
                c2 = (cubic[ci + 2], cubic[ci + 3])
                end = (cubic[ci + 4], cubic[ci + 5])
                ci += 6
                x0, y0 = path[-1]
                for step in range(1, 9):
                    u = step / 8.0
                    mu = 1.0 - u
                    path.append((mu**3*x0 + 3*mu*mu*u*c1[0] + 3*mu*u*u*c2[0] + u**3*end[0],
                                 mu**3*y0 + 3*mu*mu*u*c1[1] + 3*mu*u*u*c2[1] + u**3*end[1]))
            elif t == 3:
                if ai + 5 > len(arc):
                    return []
                cx, cy, r, a0, a1 = arc[ai:ai + 5]
                ai += 5
                clockwise = bool(cw[wi]) if wi < len(cw) else True
                wi += 1
                if clockwise and a1 > a0:
                    a1 -= 2.0 * math.pi
                elif not clockwise and a1 < a0:
                    a1 += 2.0 * math.pi
                for step in range(1, 11):
                    a = a0 + (a1 - a0) * step / 10.0
                    path.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            else:
                return []
        if len(path) >= 3:
            polys.append(path)
    return polys


def _draw_translucent_constant(target: Image.Image, stroke: Stroke,
                               scale: float, supersample: int):
    """Multiply one broad translucent highlighter into an RGB page image.

    `target` must be the flattened page (paper/background + everything drawn
    below the highlighter).  A transparent RGBA layer has no backdrop, so
    callers that keep one must flush it first and composite at the stroke's z
    position with `_apply_highlighter`.
    """
    if target.mode != "RGB":
        raise ValueError("translucent constant-width stroke needs the flattened "
                         "RGB page at its z position; use _apply_highlighter")
    target.paste(_apply_highlighter(target, stroke, scale, supersample))


def _draw_stroke(draw: ImageDraw.ImageDraw, stroke: Stroke, scale: float, supersample: int):
    color = stroke.color
    if color[3] == 0:
        return
    if (stroke.kind == "variable" and stroke.grain
            and (stroke.thickness or 0.0) >= 1.5):
        _draw_grain_stroke(draw, stroke, scale, supersample)
        return
    if stroke.kind == "constant" and 0 < color[3] < 255:
        target = getattr(draw, "_image", None)
        if target is None:
            raise ValueError("translucent constant-width stroke has no target image")
        _draw_translucent_constant(target, stroke, scale, supersample)
        return
    if stroke.kind == "pencil" and stroke.outline:
        polys = _outline_polygons(stroke)
        if polys:
            scale *= supersample
            for poly in polys:
                draw.polygon([(p[0] * scale, p[1] * scale) for p in poly], fill=color)
            return
    factor = 2.5 if stroke.kind == "pencil" else 1.0
    scale *= supersample
    any_path = False
    for path in _paths(stroke):
        any_path = True
        if len(path) < 2:
            x, y, w = path[0]
            r = max(1.0, w * factor * scale / 2)
            draw.ellipse([x * scale - r, y * scale - r, x * scale + r, y * scale + r], fill=color)
            continue
        if stroke.kind == "pencil":
            for a, b in zip(path, path[1:]):
                wd = max(1, round(((a[2] + b[2]) / 2) * factor * scale))
                draw.line([(a[0] * scale, a[1] * scale), (b[0] * scale, b[1] * scale)],
                          fill=color, width=wd)
        else:
            mean_w = sum(p[2] for p in path) / len(path)
            wd = max(1, round(mean_w * scale))
            draw.line([(p[0] * scale, p[1] * scale) for p in path],
                      fill=color, width=wd, joint="curve")
    if not any_path:
        return



def _composite_alpha_stroke(base: Image.Image, stroke: Stroke, scale: float,
                            supersample: int) -> Image.Image:
    """Source-over composite one opaque/translucent sticker stroke onto the page.

    Sticker colour bodies are short constant-width strokes with alpha 0.5.
    Several overlapping passes darken the fill, so each stroke must be
    composited separately (ImageDraw alone replaces alpha instead of stacking).
    """
    size = base.size
    mask = Image.new("L", (size[0] * supersample, size[1] * supersample), 0)
    md = ImageDraw.Draw(mask)
    sc = scale * supersample
    for path in _paths(stroke):
        if len(path) < 2:
            continue
        pts = [(p[0] * sc, p[1] * sc) for p in path]
        wd = max(1, round((stroke.thickness or 1.0) * sc))
        md.line(pts, fill=255, width=wd, joint="curve")
        r = wd / 2.0
        for x, y in pts:
            md.ellipse([x - r, y - r, x + r, y + r], fill=255)
    if supersample != 1:
        mask = mask.resize(size, Image.LANCZOS)
    a = max(0, min(255, int(stroke.color[3])))
    if a < 255:
        mask = mask.point(lambda v: (v * a + 127) // 255)
    fg = Image.new("RGBA", size, tuple(stroke.color[:3]) + (0,))
    fg.putalpha(mask)
    return Image.alpha_composite(base.convert("RGBA"), fg).convert("RGB")


def _shape_anchor(page: Page, anchor_id: str):
    for shape in page.shapes:
        if shape.id == anchor_id:
            return (shape.x + shape.width / 2.0, shape.y + shape.height / 2.0,
                    shape.width / 2.0, shape.height / 2.0)
    return None


def _resolve_endpoint(endpoint: dict, page: Page):
    if endpoint.get("kind") == "point":
        return (float(endpoint.get("x", 0.0)), float(endpoint.get("y", 0.0)))
    if endpoint.get("kind") == "connection":
        anchor = _shape_anchor(page, endpoint.get("anchor", ""))
        if anchor is None:
            return None
        cx, cy, rx, ry = anchor
        angle = float(endpoint.get("angle", 0.0))
        return (cx + rx * math.cos(angle), cy - ry * math.sin(angle))
    return None


def _round_corners(points: list[tuple[float, float]], radius: float = 7.0,
                   samples: int = 6):
    """Replace each interior corner with a short quadratic round."""
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    for i in range(1, len(points) - 1):
        p0, p1, p2 = points[i - 1], points[i], points[i + 1]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 < 1e-6 or l2 < 1e-6:
            out.append(p1)
            continue
        r = min(radius, l1 * 0.45, l2 * 0.45)
        a = (p1[0] + v1[0] / l1 * r, p1[1] + v1[1] / l1 * r)
        b = (p1[0] + v2[0] / l2 * r, p1[1] + v2[1] / l2 * r)
        for j in range(samples + 1):
            t = j / samples
            mt = 1.0 - t
            out.append((mt * mt * a[0] + 2 * mt * t * p1[0] + t * t * b[0],
                        mt * mt * a[1] + 2 * mt * t * p1[1] + t * t * b[1]))
    out.append(points[-1])
    return out


def _hobby_spline(points, curl_start: float = 1.0, curl_end: float = 1.0,
                  samples: int = 40):
    """Hobby (1986) interpolating spline through ``points``.

    Goodnotes' "hobby" connector stores only knots; the smooth teardrop loop
    comes from Hobby's tangent construction, not a Catmull-Rom average.  The
    tridiagonal system and rho velocity function follow Hobby's paper
    (Jackowski's formulation); default curl 1.0 matches the app's rounded
    endpoint tangents.
    """
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 3:
        return pts
    n = len(pts) - 1
    chords = [(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
              for i in range(n)]
    d = [math.hypot(*c) for c in chords]
    if any(di < 1e-6 for di in d):
        return pts
    gamma = [0.0] * (n + 1)
    for i in range(1, n):
        a, b = chords[i - 1], chords[i]
        gamma[i] = math.atan2(a[0] * b[1] - a[1] * b[0],
                              a[0] * b[0] + a[1] * b[1])

    A = [0.0] * (n + 1)
    B = [0.0] * (n + 1)
    C = [0.0] * (n + 1)
    D = [0.0] * (n + 1)
    B[0] = 2.0 + curl_start
    C[0] = 2.0 * curl_start + 1.0
    D[0] = -C[0] * gamma[1]
    for i in range(1, n):
        A[i] = 1.0 / d[i - 1]
        B[i] = (2.0 * d[i - 1] + 2.0 * d[i]) / (d[i - 1] * d[i])
        C[i] = 1.0 / d[i]
        D[i] = -(2.0 * gamma[i] * d[i] + gamma[i + 1] * d[i - 1]) / (
            d[i - 1] * d[i])
    A[n] = 2.0 * curl_end + 1.0
    B[n] = 2.0 + curl_end

    # Thomas algorithm for the tridiagonal tangent-angle system.
    cp = [0.0] * (n + 1)
    dp = [0.0] * (n + 1)
    cp[0] = C[0] / B[0]
    dp[0] = D[0] / B[0]
    for i in range(1, n + 1):
        den = B[i] - cp[i - 1] * A[i]
        cp[i] = C[i] / den
        dp[i] = (D[i] - dp[i - 1] * A[i]) / den
    alpha = [0.0] * (n + 1)
    alpha[n] = dp[n]
    for i in range(n - 1, -1, -1):
        alpha[i] = dp[i] - cp[i] * alpha[i + 1]

    beta = [0.0] * n
    for i in range(n - 1):
        beta[i] = -gamma[i + 1] - alpha[i + 1]
    beta[n - 1] = -alpha[n]

    def rho(a, b):
        c = 2.0 / 3.0
        return 2.0 / (1.0 + c * math.cos(b) + (1.0 - c) * math.cos(a))

    out = [pts[0]]
    for i in range(n):
        ux, uy = chords[i][0] / d[i], chords[i][1] / d[i]
        ca, sa = math.cos(alpha[i]), math.sin(alpha[i])
        la = rho(alpha[i], beta[i]) * d[i] / 3.0
        c0 = (pts[i][0] + (ux * ca - uy * sa) * la,
              pts[i][1] + (ux * sa + uy * ca) * la)
        cb, sb = math.cos(-beta[i]), math.sin(-beta[i])
        lb = rho(beta[i], alpha[i]) * d[i] / 3.0
        c1 = (pts[i + 1][0] - (ux * cb - uy * sb) * lb,
              pts[i + 1][1] - (ux * sb + uy * cb) * lb)
        p0, p3 = pts[i], pts[i + 1]
        for j in range(1, samples + 1):
            t = j / samples
            mt = 1.0 - t
            out.append((
                mt ** 3 * p0[0] + 3 * mt * mt * t * c0[0]
                + 3 * mt * t * t * c1[0] + t ** 3 * p3[0],
                mt ** 3 * p0[1] + 3 * mt * mt * t * c0[1]
                + 3 * mt * t * t * c1[1] + t ** 3 * p3[1],
            ))
    return out


def _hobby_path(start, mids, end):
    """Hobby connector: Hobby spline through start, stored middles and end."""
    return _hobby_spline([start, *mids, end])


def _arrowhead(draw: ImageDraw.ImageDraw, tip, prev, line: LineItem,
               scale: float, style: int):
    if style <= 0:
        return
    dx, dy = tip[0] - prev[0], tip[1] - prev[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    ux, uy = dx / length, dy / length
    size = 16.0 * scale
    half = math.radians(27.0)
    a1 = math.atan2(uy, ux) + half
    a2 = math.atan2(uy, ux) - half
    p1 = (tip[0] - size * math.cos(a1), tip[1] - size * math.sin(a1))
    p2 = (tip[0] - size * math.cos(a2), tip[1] - size * math.sin(a2))
    wd = max(1, round(line.width * scale))
    if style == 1:
        draw.line([tip, p1], fill=line.color, width=wd)
        draw.line([tip, p2], fill=line.color, width=wd)
    else:
        draw.polygon([tip, p1, p2], fill=line.color)


def _draw_line_item(draw: ImageDraw.ImageDraw, line: LineItem, page: Page,
                    scale: float, supersample: int):
    sc = scale * supersample
    start = _resolve_endpoint(line.start, page)
    end = _resolve_endpoint(line.end, page)
    if start is None or end is None:
        raise ValueError("line endpoint anchor not found")
    if line.geometry == "hobby":
        pts = _hobby_path(start, line.middle_points, end)
    else:
        mids = list(line.middle_points)
        pts = [start]
        if mids:
            for m in mids:
                if line.start_direction == 1:
                    pts.append((pts[-1][0], m[1]))
                else:
                    pts.append((m[0], pts[-1][1]))
                pts.append(m)
            if line.start_direction == 1:
                pts.append((end[0], pts[-1][1]))
            else:
                pts.append((pts[-1][0], end[1]))
        else:
            if line.start_direction == 1:
                pts.append((start[0], end[1]))
            else:
                pts.append((end[0], start[1]))
        pts.append(end)
        pts = _round_corners(pts)
    scaled = [(p[0] * sc, p[1] * sc) for p in pts]
    wd = max(1, round(line.width * sc))
    draw.line(scaled, fill=line.color, width=wd, joint="curve")
    if len(scaled) >= 2:
        _arrowhead(draw, scaled[-1], scaled[-2], line, sc, line.end_arrow)
        _arrowhead(draw, scaled[0], scaled[1], line, sc, line.start_arrow)


def _draw_shape_item(draw: ImageDraw.ImageDraw, shape: ShapeItem,
                     scale: float, supersample: int):
    sc = scale * supersample
    box = [shape.x * sc, shape.y * sc,
           (shape.x + shape.width) * sc, (shape.y + shape.height) * sc]
    if shape.geometry == "ellipse":
        if shape.fill_color[3] > 0:
            draw.ellipse(box, fill=shape.fill_color)
        if shape.stroke_width > 0:
            draw.ellipse(box, outline=shape.stroke_color,
                         width=max(1, round(shape.stroke_width * sc)))
    elif shape.geometry == "roundedRectangle":
        if shape.fill_color[3] > 0:
            draw.rounded_rectangle(box, radius=8 * sc, fill=shape.fill_color)
        if shape.stroke_width > 0:
            draw.rounded_rectangle(box, radius=8 * sc, outline=shape.stroke_color,
                                   width=max(1, round(shape.stroke_width * sc)))
    else:
        raise ValueError("shape geometry not rendered: %s" % shape.geometry)


def _draw_sticky_note(draw: ImageDraw.ImageDraw, sticky: StickyNoteItem,
                      scale: float, supersample: int):
    sc = scale * supersample
    x, y = sticky.x * sc, sticky.y * sc
    w, h = sticky.width * sc, sticky.height * sc
    draw.rectangle([x, y, x + w, y + h], fill=sticky.color)
    pad = 10.0 * sc
    body_color = (49, 42, 2, 255)
    font = _font(max(6, round(24 * sc)), "Helvetica")
    lines = _wrap(sticky.text, font, max(8.0, w - 2 * pad)) if sticky.text else []
    line_h = max(6.0, 24 * sc * 1.22)
    for i, text in enumerate(lines):
        draw.text((x + pad, y + pad + i * line_h), text, font=font, fill=body_color)
    if sticky.show_author and sticky.author_name:
        afont = _font(max(6, round(12.0 * sc)), "Helvetica")
        ay = y + h - pad - 15 * sc
        draw.text((x + pad, ay), sticky.author_name, font=afont,
                  fill=(49, 42, 2, 255))


def _pattern_bytes(attachment_id: str, attachment_loader=None, zf=None):
    if not attachment_id:
        return None
    if attachment_loader is not None:
        try:
            return attachment_loader(attachment_id)
        except Exception:
            pass
    if zf is not None:
        for member in ("attachments/" + attachment_id, attachment_id):
            try:
                return zf.read(member)
            except KeyError:
                continue
    return None


def _draw_tape(layer: Image.Image, stroke: Stroke, scale: float, supersample: int,
               attachment_loader=None, zf=None) -> bool:
    """Draw a pattern-tape stroke; returns False when its tile is unavailable."""
    tape = stroke.tape or {}
    thickness = float(tape.get("thickness") or stroke.thickness or 18.0)
    size = layer.size                    # already supersampled
    sc = scale * supersample
    mask = Image.new("L", size, 0)
    md = ImageDraw.Draw(mask)
    tmp = Stroke("tape", "pencil", stroke.color, stroke.tape_commands,
                 thickness=thickness)
    for path in _paths(tmp):
        if len(path) < 2:
            continue
        pts = [(p[0] * sc, p[1] * sc) for p in path]
        wd = max(1, round(2.0 * thickness * sc))
        md.line(pts, fill=255, width=wd, joint="curve")
        rr = wd / 2.0
        for x, y in pts[1:-1]:
            md.ellipse([x - rr, y - rr, x + rr, y + rr], fill=255)
    bbox = mask.getbbox()
    if bbox is None:
        return True
    data = _pattern_bytes(tape.get("attachment_id", ""), attachment_loader, zf)
    if data is None:
        fg = Image.new("RGBA", size, tuple(stroke.color[:3]) + (0,))
        fg.putalpha(mask)
        layer.alpha_composite(fg)
        return False
    tile = Image.open(io.BytesIO(data)).convert("RGBA")
    side = max(1, round(2.0 * thickness * float(tape.get("scale") or 1.0) * sc))
    tile = tile.resize((side, side), Image.LANCZOS)
    ax, ay = tape.get("rotation_anchor") or (0.0, 0.0)
    ax, ay = float(ax) * sc, float(ay) * sc
    bx, by, ex, ey = bbox
    pat = Image.new("RGBA", (ex - bx, ey - by), (0, 0, 0, 0))
    ox = (bx - ax) % side
    oy = (by - ay) % side
    for yy in range(-side, pat.height + side, side):
        for xx in range(-side, pat.width + side, side):
            pat.alpha_composite(tile, (xx + int(ox), yy + int(oy)))
    # Thin platform edge/shadow visible around the green fill (not in the data).
    edge = Image.new("L", size, 0)
    ed = ImageDraw.Draw(edge)
    for path in _paths(tmp):
        if len(path) < 2:
            continue
        pts = [(p[0] * sc, p[1] * sc) for p in path]
        ed.line(pts, fill=255, width=max(1, round(2.0 * thickness * sc + 2.0 * sc)),
                joint="curve")
    edge = ImageChops.subtract(edge, mask)
    edge_rgba = Image.new("RGBA", size, (198, 198, 188, 0))
    edge_rgba.putalpha(edge.point(lambda v: (v * 150 + 127) // 255))
    layer.alpha_composite(edge_rgba)
    base_fill = Image.new("RGBA", pat.size, tuple(stroke.color[:3]) + (255,))
    pat = Image.alpha_composite(base_fill, pat)
    pat.putalpha(ImageChops.multiply(pat.getchannel("A"), mask.crop(bbox)))
    layer.alpha_composite(pat, (bx, by))
    return True


def _draw_grain_stroke(draw: ImageDraw.ImageDraw, stroke: Stroke,
                       scale: float, supersample: int):
    """Procedural pencil grain for type-5 brush strokes (P4 thick test)."""
    sc = scale * supersample
    thickness = float(stroke.thickness or 1.0)
    color = stroke.color
    seed = sum((i + 1) * ord(ch) for i, ch in enumerate(stroke.id or "")) + stroke.record
    rng = random.Random(seed)
    paths = _paths(stroke)
    for path in paths:
        if len(path) < 2:
            continue
        draw.line([(p[0] * sc, p[1] * sc) for p in path],
                  fill=tuple(color[:3]) + (245,),
                  width=max(1, round(thickness * 0.60 * sc)), joint="curve")
    step = max(0.7, thickness * 0.18)
    for path in paths:
        if len(path) < 2:
            continue
        for a, b in zip(path, path[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            seg = math.hypot(dx, dy)
            if seg < 1e-6:
                continue
            ux, uy = dx / seg, dy / seg
            nx, ny = -uy, ux
            n = max(1, int(seg / step))
            for k in range(n):
                t = (k + 0.5) / n
                x = a[0] + dx * t
                y = a[1] + dy * t
                for _ in range(10):
                    off = rng.gauss(0.0, thickness * 0.46)
                    along = rng.uniform(-0.5, 0.5) * step
                    px = (x + nx * off + ux * along) * sc
                    py = (y + ny * off + uy * along) * sc
                    r = max(0.28, rng.uniform(0.03, 0.10) * thickness) * sc
                    alpha = rng.randint(100, 220)
                    draw.ellipse([px - r, py - r, px + r, py + r],
                                 fill=tuple(color[:3]) + (alpha,))
            if rng.random() < 0.18:
                # Sparse light void in the core, like the iPad grain.
                x = (a[0] + dx * 0.5) * sc
                y = (a[1] + dy * 0.5) * sc
                r = max(0.5, thickness * 0.16 * sc)
                draw.ellipse([x - r, y - r, x + r, y + r],
                             fill=(247, 246, 233, 90))


def _draw_image(layer: Image.Image, z: zipfile.ZipFile | None, item, scale: float,
                supersample: int, attachment_loader=None):
    try:
        if item.attachment and z is not None:
            data = z.read(item.attachment)
        elif getattr(item, "attachment_id", "") and attachment_loader is not None:
            data = attachment_loader(item.attachment_id)
        else:
            raise FileNotFoundError("image attachment unavailable")
        try:
            im = Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception:
            doc = pdfium.PdfDocument(io.BytesIO(data))
            im = doc[0].render(scale=2).to_pil().convert("RGBA")
    except Exception:
        if getattr(item, "kind", "") == "math":
            # Clearly marked placeholder; math items in the corpus are deleted.
            sc = scale * supersample
            x, y = item.x * sc, item.y * sc
            w, h = max(8, item.width * sc), max(8, item.height * sc)
            md = ImageDraw.Draw(layer)
            md.rectangle([x, y, x + w, y + h], outline=(180, 60, 60, 180), width=max(1, int(2 * sc)))
            label = (item.data_string or "math")[:80]
            md.text((x + 4 * sc, y + 4 * sc), label, fill=(120, 40, 40, 220))
            return
        raise
    s = scale * supersample
    if item.rotation and item.actual_width and item.actual_height:
        # RotatedRect: actualRect carries the true size and centre; rect is the
        # axis-aligned bounding box and must not be used as the pasted size.
        w = max(1, round(item.actual_width * s))
        h = max(1, round(item.actual_height * s))
        im = im.resize((w, h), Image.LANCZOS)
        im = im.rotate(-math.degrees(item.rotation), expand=True, resample=Image.BICUBIC)
        cx, cy = item.center_x * s, item.center_y * s
        layer.paste(im, (round(cx - im.width / 2.0), round(cy - im.height / 2.0)), im)
    else:
        w = max(1, round(item.width * s))
        h = max(1, round(item.height * s))
        im = im.resize((w, h), Image.LANCZOS)
        layer.paste(im, (round(item.x * s), round(item.y * s)), im)


def _draw_fill(draw: ImageDraw.ImageDraw, fill: FillShapeItem, scale: float,
               supersample: int):
    s = scale * supersample
    color = fill.color
    if color[3] == 0:
        return
    for sh in fill.shapes:
        t = sh.get("type")
        if t == "ellipse":
            cx, cy, rx, ry = sh["cx"] * s, sh["cy"] * s, sh["rx"] * s, sh["ry"] * s
            rot = sh.get("rotation", 0.0)
            pts = []
            for i in range(48):
                a = 2.0 * math.pi * i / 48
                x, y = rx * math.cos(a), ry * math.sin(a)
                pts.append((cx + x * math.cos(rot) - y * math.sin(rot),
                            cy + x * math.sin(rot) + y * math.cos(rot)))
            draw.polygon(pts, fill=color)
        elif t == "rect":
            draw.rectangle([sh["x"] * s, sh["y"] * s,
                            (sh["x"] + sh["w"]) * s, (sh["y"] + sh["h"]) * s],
                           fill=color)
        elif t == "polygon":
            if len(sh["points"]) >= 3:
                draw.polygon([(x * s, y * s) for x, y in sh["points"]], fill=color)
        elif t == "quad":
            x0, y0 = sh["p0"]; x1, y1 = sh["p1"]; x2, y2 = sh["p2"]
            tx, ty = sh.get("tx", 0.0), sh.get("ty", 0.0)
            pts = []
            for i in range(25):
                u = i / 24.0; mu = 1.0 - u
                pts.append(((mu * mu * x0 + 2 * mu * u * x1 + u * u * x2 + tx) * s,
                            (mu * mu * y0 + 2 * mu * u * y1 + u * u * y2 + ty) * s))
            draw.polygon(pts, fill=color)


FONT_FAMILIES = {
    "courier": ("Courier New.ttf", "Courier New Bold.ttf",
                "Courier New Italic.ttf", "Courier New Bold Italic.ttf"),
    "times": ("Times New Roman.ttf", "Times New Roman Bold.ttf",
              "Times New Roman Italic.ttf", "Times New Roman Bold Italic.ttf"),
    "georgia": ("Georgia.ttf", "Georgia Bold.ttf", "Georgia Italic.ttf",
                "Georgia Bold Italic.ttf"),
}


def _font(size: int, family: str = "", bold: bool = False, italic: bool = False):
    fam = (family or "").lower()
    key = "courier" if "courier" in fam else "times" if "times" in fam else \
        "georgia" if "georgia" in fam else ""
    candidates = []
    if bold or italic:
        candidates += ["/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf" if bold and italic else
                       "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else
                       "/System/Library/Fonts/Supplemental/Arial Italic.ttf"]
    if key:
        names = FONT_FAMILIES[key]
        candidates.append("/System/Library/Fonts/Supplemental/" + names[(1 if bold else 0) + (2 if italic else 0)])
    candidates += list(FONT_CANDIDATES)
    for path in candidates:
        try:
            return ImageFont.truetype(path, max(6, size))
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(text: str, font, max_width: float) -> list[str]:
    words = text.split(" ")
    lines = []
    cur = ""
    for word in words:
        # Long unbroken tokens (UUID headings, URLs, code identifiers) must be
        # split by character instead of overflowing the page.
        if font.getlength(word) > max_width:
            if cur:
                lines.append(cur)
                cur = ""
            chunk = ""
            for ch in word:
                if chunk and font.getlength(chunk + ch) > max_width:
                    lines.append(chunk)
                    chunk = ch
                else:
                    chunk += ch
            cur = chunk
            continue
        trial = (cur + " " + word).strip()
        if cur and font.getlength(trial) > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


def _draw_full_page_text(draw: ImageDraw.ImageDraw, box: TextBox, page: Page,
                         s: float):
    """Goodnotes text-document layout: document id heading, section, body/list."""
    cw = page.canvas_width or 834.24
    ch = page.canvas_height or 1078.83
    x = cw * (44.0 / 834.24) * s
    area_y = ch * (58.6667 / 1078.82495) * s
    max_w = max(8.0, cw * (746.24005 / 834.24) * s)
    unit = ch / 1078.82495
    body_fs = 20.0 * unit
    title_fs = 38.0 * unit
    heading_fs = 30.0 * unit
    cy = area_y + 110.0 * unit * s

    def draw_wrapped(text, font, fs, color=(0, 0, 0, 255), indent=0, width=None):
        nonlocal cy
        for hard in str(text).split("\n"):
            for line in _wrap(hard, font, (width or max_w) - indent):
                draw.text((x + indent, cy), line, font=font, fill=color)
                cy += fs * 1.42

    if page.document_id:
        title_font = _font(max(8, round(title_fs * s)), "", bold=True)
        draw_wrapped(page.document_id, title_font, title_fs * s, width=max_w * 0.68)
        cy += 36.0 * unit * s

    runs = list(box.runs or [])
    if not runs:
        runs = [str(p.get("text") or "") for p in box.paragraphs
                if isinstance(p, dict) and str(p.get("text") or "").strip()]
    # The first Yjs child is the document name (the PDF uses the id heading
    # instead); do not print the duplicate title.
    if page.document_id and len(runs) > 1:
        runs = runs[1:]
    if not runs:
        return

    # A short first run after the title is a section heading (e.g. T6a.1).
    heading = ""
    if len(runs) > 1 and len(runs[0].strip()) <= 60 and "\n" not in runs[0]:
        heading = runs.pop(0)
    if heading:
        hfont = _font(max(8, round(heading_fs * s)), "", bold=True)
        draw_wrapped(heading, hfont, heading_fs * s)
        cy += 12.0 * unit * s

    body_font = _font(max(8, round(body_fs * s)))
    # Short runs without code punctuation form a numbered list (the common
    # T6a.1/T7.x exercise layout).  Prose runs remain ordinary paragraphs.
    is_list = (len(runs) >= 2 and all(len(r) <= 80 and "\n" not in r for r in runs)
               and not any(any(ch in r for ch in ";{}") for r in runs))
    for i, run in enumerate(runs):
        if (len(run) <= 24 and re.match(r"^[A-Z]\d", run.strip())):
            cy += 16.0 * unit * s
            hfont = _font(max(8, round(heading_fs * s)), "", bold=True)
            draw_wrapped(run, hfont, heading_fs * s, box.color)
            cy += 10.0 * unit * s
            continue
        text = (("%d. " % (i + 1)) if is_list else "") + run
        draw_wrapped(text, body_font, body_fs * s, box.color)


def _draw_text(draw: ImageDraw.ImageDraw, box: TextBox, scale: float,
               supersample: int, page: Page | None = None):
    s = scale * supersample
    if box.full_page and page is not None:
        _draw_full_page_text(draw, box, page, s)
        return
    x, y, width, height = box.x, box.y, box.width, box.height
    font_size = box.font_size
    x *= s
    y *= s
    max_w = max(8.0, width * s)
    if box.kind == "stickyNote" and box.fill_color[3] > 0:
        pad = 4.0 * s
        draw.rounded_rectangle([x - pad, y - pad, x + max_w + pad,
                                y + max(8.0, height * s) + pad],
                               radius=6 * s, fill=box.fill_color)
    paragraphs = box.paragraphs
    if not paragraphs:
        paragraphs = [{"text": line, "inline": {}, "block": {}}
                      for line in (box.lines or [""])]
    # Preserve explicit blank paragraphs (important for nexus documents).
    lines: list[tuple[str, dict, dict, tuple]] = []
    for para in paragraphs:
        if not isinstance(para, dict):
            continue
        inline = para.get("inline") or {}
        block = para.get("block") or {}
        text = str(para.get("text") or "")
        base_color = box.color
        c = inline.get("color") if isinstance(inline, dict) else None
        if isinstance(c, dict):
            vals = [c.get(k, 0.0) for k in ("r", "g", "b", "a")]
            base_color = tuple(max(0, min(255, round(float(v) * 255.0))) for v in vals)
        fam = (inline.get("fontFamily") if isinstance(inline, dict) else "") or box.font_family
        fs = inline.get("fontSize") if isinstance(inline, dict) else None
        try:
            fs = float(fs)
        except (TypeError, ValueError):
            fs = 0.0
        if not (1.0 < fs < 300.0):
            fs = font_size
        bold = box.bold or (isinstance(inline.get("fontWeight"), (int, float)) and
                            500 <= inline["fontWeight"] < 1000)
        italic = box.italic or bool(inline.get("italic"))
        size = max(6, round(fs * s))
        font = _font(size, fam, bold, italic)
        if text == "":
            lines.append(("", font, block, base_color))
            continue
        for piece in text.split("\n"):
            for wrapped in _wrap(piece, font, max_w):
                lines.append((wrapped, font, block, base_color))
    if not lines:
        return
    line_h = max(6.0, round(font_size * s * 1.43))
    # Use block lineHeight when present (canvas units).
    for _txt, _fnt, block, _col in lines:
        lh = (block.get("lineHeight") or {}).get("lineHeight") if isinstance(block, dict) else None
        if isinstance(lh, (int, float)) and lh > 0:
            line_h = max(6.0, float(lh) * s)
            break
    for i, (text, font, block, color) in enumerate(lines):
        align = block.get("textAlignment", box.alignment) if isinstance(block, dict) else box.alignment
        tx = x
        if align == 1:
            tx = x + max(0.0, (max_w - font.getlength(text)) / 2.0)
        list_style = (block.get("listStyle") or {}).get("type") if isinstance(block, dict) else None
        prefix = ""
        if list_style == 1:
            prefix = "%d. " % (i + 1)
        elif list_style == 0:
            prefix = "• "
        draw.text((tx, y + i * line_h), prefix + text, font=font, fill=color)


def _path_is_straight(path, tolerance: float = 0.5) -> bool:
    """True when every flattened point lies on the start/end line."""
    if len(path) < 3:
        return True
    x0, y0 = path[0][0], path[0][1]
    x1, y1 = path[-1][0], path[-1][1]
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return False
    return max(abs(dy * (x - x0) - dx * (y - y0)) / length
               for x, y, _w in path) <= tolerance


def _highlighter_mask(stroke: Stroke, size: tuple[int, int], scale: float,
                      supersample: int) -> Image.Image:
    """Coverage mask for one translucent highlighter.

    A polyline gives the main body.  Goodnotes' auto-straightened bars are
    collinear and carry ``ConstantWidthStroke`` field 6 = 1
    (``PenStrokeLineStyle_LineCap_round``); they get a disc at each end, radius
    half the stored thickness.  Compact scribbled marks (start-to-end distance
    < 35 canvas units) additionally get a disc at every flattened point,
    because Pillow's even-odd polyline rasteriser leaves thin white slivers
    where their short segments overlap.
    """
    mask = Image.new("L", (size[0] * supersample, size[1] * supersample), 0)
    md = ImageDraw.Draw(mask)
    sc = scale * supersample
    for path in _paths(stroke):
        if len(path) >= 2:
            pts = [(p[0] * sc, p[1] * sc) for p in path]
            wd = max(1, round((stroke.thickness or 12.0) * sc))
            md.line(pts, fill=255, width=wd, joint="curve")
            r = wd / 2.0
            if stroke.line_cap == 1 and _path_is_straight(path):
                for x, y in (pts[0], pts[-1]):
                    md.ellipse([x - r, y - r, x + r, y + r], fill=255)
            compact = math.hypot(path[-1][0] - path[0][0],
                                 path[-1][1] - path[0][1]) < 35.0
            if compact:
                for x, y in pts[1:-1]:
                    md.ellipse([x - r, y - r, x + r, y + r], fill=255)
        elif len(path) == 1 and stroke.line_cap == 1:
            x, y, w = path[0]
            r = max(1.0, (w or stroke.thickness or 12.0) * sc) / 2.0
            md.ellipse([x * sc - r, y * sc - r, x * sc + r, y * sc + r], fill=255)
    if supersample != 1:
        mask = mask.resize(size, Image.LANCZOS)
    return mask


def _apply_highlighter(base: Image.Image, stroke: Stroke, scale: float,
                        supersample: int) -> Image.Image:
    """Multiply a translucent highlighter over the current page, preserving ink.

    Goodnotes uses a per-channel multiply with
    ``factor = 1 - alpha * (1 - rgb)``.  A flat coverage mask is built once
    (so overlapping segments of one stroke do not stack alpha), then the
    whole page is multiplied by one factor image.  Works for RGB and RGBA
    pages; the alpha channel of an RGBA page is preserved.
    """
    size = base.size
    mask = _highlighter_mask(stroke, size, scale, supersample)

    a = stroke.color[3] / 255.0
    factor_rgb = tuple(
        max(0, min(255, round(((1.0 - a) + a * (c / 255.0)) * 255.0)))
        for c in stroke.color[:3]
    )
    factor = Image.composite(
        Image.new("RGB", size, factor_rgb),
        Image.new("RGB", size, (255, 255, 255)),
        mask,
    )

    if base.mode == "RGBA":
        alpha = base.getchannel("A")
        out = ImageChops.multiply(base.convert("RGB"), factor)
        out.putalpha(alpha)
        return out
    return ImageChops.multiply(base.convert("RGB"), factor)


def render_records(records, out_path: str, *, width: float = 1091.0, height: float = 1543.0,
                   template: str = "", paper_width: float = 595.28,
                   paper_height: float = 841.89, supersample: int = 3,
                   attachment_loader=None, document_id: str = "",
                   return_info: bool = False):
    """Render one live page from record bytes or gnstore descriptor/item pairs.

    `records` may be the raw length-delimited page bytes, or an iterable of
    `(descriptor_bytes, item_bytes)` pairs (gnstore.get_page_records(), already
    bottom->top).  The latter is re-framed and decoded with the same code path
    as a backup notes/<uuid> file.  `attachment_loader` resolves live f1/f11
    attachment ids (default is the read-only container resolver); `document_id`
    supplies the nexus document-id heading.
    """
    from .decode import open_records
    from .model import Notebook
    if isinstance(records, (bytes, bytearray)):
        data = bytes(records)
    else:
        def varint(n):
            out = bytearray()
            while True:
                b = n & 0x7F
                n >>= 7
                out.append(b | (0x80 if n else 0))
                if not n:
                    return bytes(out)
        data = b"".join(varint(len(d)) + d + varint(len(i)) + i
                        for d, i in records)
    page = open_records(data, width=width, height=height, template=template,
                        paper_width=paper_width, paper_height=paper_height)
    page.document_id = document_id
    if attachment_loader is None:
        attachment_loader = live_attachments.load
    return render_page(Notebook(path="", title="live"), page, out_path,
                       supersample=supersample, return_info=return_info,
                       attachment_loader=attachment_loader)


def render_page(notebook: Notebook, page: Page, out_path: str, scale: float = 1.0,
                supersample: int = 3, highlighter: bool = True,
                attachment_loader=None, return_info: bool = False):
    started = time.perf_counter()
    warnings = [dict(w) for w in page.unknown_items]
    w = page.canvas_width or DEFAULT_W
    h = page.canvas_height or DEFAULT_H
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    base = _background(notebook, page, size)
    layer = Image.new("RGBA", (size[0] * supersample, size[1] * supersample), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    zf = None
    if page.images and notebook.path:
        try:
            zf = zipfile.ZipFile(notebook.path)
        except OSError:
            zf = None

    def flush():
        nonlocal base, layer, draw
        if layer.getbbox() is None:
            return
        if supersample != 1:
            small = layer.resize(size, Image.LANCZOS)
        else:
            small = layer
        base = Image.alpha_composite(base.convert("RGBA"), small).convert("RGB")
        layer = Image.new("RGBA", (size[0] * supersample, size[1] * supersample), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")

    try:
        for obj in page.objects():
            if isinstance(obj, Stroke):
                if obj.kind == "eraser":
                    continue
                if obj.kind == "unknown":
                    continue
                if obj.signature_kind == "constant_short":
                    # Sticker colour body: source-over alpha, one composite per
                    # stroke so overlapping passes darken.
                    flush()
                    base = _composite_alpha_stroke(base, obj, scale, supersample)
                    continue
                if obj.tape and obj.tape_commands:
                    try:
                        if not _draw_tape(layer, obj, scale, supersample,
                                          attachment_loader, zf):
                            warnings.append({"item": obj.id, "kind": "tape",
                                             "reason": "tape pattern missing"})
                    except Exception as exc:
                        warnings.append({"item": obj.id, "kind": "tape",
                                         "reason": "%s: %s" % (type(exc).__name__, exc)})
                    continue
                if obj.kind == "constant" and obj.color[3] < 255:
                    if not highlighter:
                        continue
                    flush()
                    base = _apply_highlighter(base, obj, scale, supersample)
                    continue
                try:
                    _draw_stroke(draw, obj, scale, supersample)
                except Exception as exc:
                    warnings.append({"item": getattr(obj, "id", ""), "kind": obj.kind,
                                     "reason": "%s: %s" % (type(exc).__name__, exc)})
            elif isinstance(obj, FillShapeItem):
                try:
                    _draw_fill(draw, obj, scale, supersample)
                except Exception as exc:
                    warnings.append({"item": getattr(obj, "id", ""), "kind": "fill",
                                     "reason": "%s: %s" % (type(exc).__name__, exc)})
            elif isinstance(obj, ShapeItem):
                try:
                    _draw_shape_item(draw, obj, scale, supersample)
                except Exception as exc:
                    warnings.append({"item": obj.id, "kind": "shape",
                                     "reason": "%s: %s" % (type(exc).__name__, exc)})
            elif isinstance(obj, LineItem):
                try:
                    _draw_line_item(draw, obj, page, scale, supersample)
                except Exception as exc:
                    warnings.append({"item": obj.id, "kind": "line",
                                     "reason": "%s: %s" % (type(exc).__name__, exc)})
            elif isinstance(obj, StickyNoteItem):
                try:
                    _draw_sticky_note(draw, obj, scale, supersample)
                except Exception as exc:
                    warnings.append({"item": obj.id, "kind": "sticky",
                                     "reason": "%s: %s" % (type(exc).__name__, exc)})
            elif isinstance(obj, TextBox):
                if obj.kind == "stickyNote" and any(s.id == obj.id for s in page.stickies):
                    continue
                try:
                    _draw_text(draw, obj, scale, supersample, page)
                except Exception as exc:
                    warnings.append({"item": getattr(obj, "id", ""), "kind": "text",
                                     "reason": "%s: %s" % (type(exc).__name__, exc)})
            else:
                try:
                    _draw_image(layer, zf, obj, scale, supersample, attachment_loader)
                except Exception as exc:
                    warnings.append({"item": getattr(obj, "id", ""), "kind": "image",
                                     "reason": "%s: %s" % (type(exc).__name__, exc)})
        flush()
    finally:
        if zf is not None:
            zf.close()
    base.save(out_path)
    result = RenderResult(out_path, warnings, {
        "page": page.index + 1,
        "canvas": [round(w, 2), round(h, 2)],
        "size": [size[0], size[1]],
        "objects": len(page.objects()),
        "strokes": sum(1 for s in page.strokes if s.kind in ("pencil", "constant", "variable")),
        "texts": len(page.texts),
        "images": len(page.images),
        "fills": len(page.fills),
        "lines": len(page.lines), "shapes": len(page.shapes),
        "stickies": len(page.stickies),
        "tape": sum(1 for s in page.strokes if s.tape),
        "deleted_items": page.deleted_items,
        "supersample": supersample,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 1),
    })
    return result if return_info else out_path


def render_page_result(notebook: Notebook, page: Page, out_path: str, **kwargs) -> RenderResult:
    kwargs["return_info"] = True
    return render_page(notebook, page, out_path, **kwargs)


def render_records_result(records, out_path: str, **kwargs) -> RenderResult:
    kwargs["return_info"] = True
    return render_records(records, out_path, **kwargs)
