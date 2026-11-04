"""Small data model for a decoded Goodnotes notebook."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Stroke:
    id: str
    kind: str                       # 'pencil', 'constant', 'variable', 'eraser', 'unknown'
    color: tuple[int, int, int, int]
    commands: list[dict[str, Any]]
    thickness: float | None = None
    record: int = 0
    signature: str = ""
    z: tuple[int, int, int] = (0, 0, 0)
    outline: dict[str, list] | None = None
    line_cap: int = 0                 # ConstantWidthStroke field 6: 0=butt, 1=round, 2=square
    brush_type: int = 0               # PenStrokeNotesItemV2.type (0=constant,1=variable,...,5=pencil)
    signature_kind: str = ""          # e.g. 'constant_short' (sticker body; source-over)
    tape: dict[str, Any] | None = None       # TapeStyle_PatternTape decoded fields
    tape_commands: list[dict[str, Any]] = field(default_factory=list)  # cmd4/5 centreline
    grain: dict[str, Any] | None = None      # type=5 per-vertex brush parameters

    def points(self) -> list[tuple[float, float, float]]:
        """All path points as (x, y, width). Quad control points included."""
        out: list[tuple[float, float, float]] = []
        for c in self.commands:
            for p in c.get("points", []):
                out.append((p[0], p[1], p[2] if len(p) > 2 else (self.thickness or 1.0)))
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "color": list(self.color),
            "thickness": self.thickness,
            "line_cap": self.line_cap,
            "commands": self.commands,
            **({"brush_type": self.brush_type} if self.brush_type else {}),
            **({"signature_kind": self.signature_kind} if self.signature_kind else {}),
            **({"tape": self.tape} if self.tape else {}),
            **({"grain": self.grain} if self.grain else {}),
        }


@dataclass
class TextBox:
    id: str
    x: float
    y: float
    width: float
    height: float
    lines: list[str]
    color: tuple[int, int, int, int] = (0, 0, 0, 255)
    font_size: float = 24.0
    rotation: float = 0.0
    scale: float = 1.0
    record: int = 0
    z: tuple[int, int, int] = (0, 0, 0)
    kind: str = "text"
    paragraphs: list[dict[str, Any]] = field(default_factory=list)
    font_family: str = ""
    italic: bool = False
    bold: bool = False
    alignment: int = 0
    fill_color: tuple[int, int, int, int] = (0, 0, 0, 0)
    full_page: bool = False
    runs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind,
                "x": round(self.x, 2), "y": round(self.y, 2),
                "width": round(self.width, 2), "height": round(self.height, 2),
                "lines": len(self.lines), "paragraphs": len(self.paragraphs),
                "font_size": self.font_size, "font_family": self.font_family}


@dataclass
class ImageItem:
    id: str
    x: float
    y: float
    width: float
    height: float
    attachment: str | None
    attachment_id: str = ""
    kind: str = "image"
    data_string: str = ""
    rotation: float = 0.0
    record: int = 0
    z: tuple[int, int, int] = (0, 0, 0)
    center_x: float = 0.0
    center_y: float = 0.0
    actual_width: float = 0.0
    actual_height: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "x": round(self.x, 2),
                "y": round(self.y, 2), "width": round(self.width, 2),
                "height": round(self.height, 2), "attachment": self.attachment,
                "attachment_id": self.attachment_id,
                "rotation": round(self.rotation, 4)}


@dataclass
class FillShapeItem:
    id: str
    shapes: list[dict]
    color: tuple[int, int, int, int] = (0, 0, 0, 255)
    record: int = 0
    z: tuple[int, int, int] = (0, 0, 0)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "shapes": len(self.shapes),
                "color": list(self.color)}


@dataclass
class LineItem:
    id: str
    geometry: str                    # 'elbow' | 'hobby'
    start: dict[str, Any]            # {'kind':'point',...} or {'kind':'connection',...}
    end: dict[str, Any]
    middle_points: list[tuple[float, float]]
    width: float
    color: tuple[int, int, int, int]
    dashed: bool = False
    start_arrow: int = 0             # 0 none, 1 line, 2 triangle
    end_arrow: int = 0
    start_direction: int = 0
    rotation: tuple[float, float, float] | None = None
    record: int = 0
    z: tuple[int, int, int] = (0, 0, 0)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": "line", "geometry": self.geometry,
                "start": self.start, "end": self.end,
                "middle_points": [list(p) for p in self.middle_points],
                "width": round(self.width, 3), "color": list(self.color),
                "dashed": self.dashed, "start_arrow": self.start_arrow,
                "end_arrow": self.end_arrow}


@dataclass
class ShapeItem:
    id: str
    kind: int                        # 0 shape, 1 textbox
    geometry: str                    # 'ellipse' | 'roundedRectangle' | 'hobbyPath'
    x: float
    y: float
    width: float
    height: float
    fill_color: tuple[int, int, int, int] = (0, 0, 0, 0)
    stroke_color: tuple[int, int, int, int] = (0, 0, 0, 255)
    stroke_width: float = 0.0
    dashed: bool = False
    rotation: float = 0.0
    scale: float = 1.0
    sizing: str = ""
    has_text: bool = False
    record: int = 0
    z: tuple[int, int, int] = (0, 0, 0)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": "shape", "shape_kind": self.kind,
                "geometry": self.geometry, "x": round(self.x, 2), "y": round(self.y, 2),
                "width": round(self.width, 2), "height": round(self.height, 2),
                "fill_color": list(self.fill_color), "stroke_width": self.stroke_width,
                "stroke_color": list(self.stroke_color), "dashed": self.dashed,
                "sizing": self.sizing, "rotation": self.rotation, "scale": self.scale}


@dataclass
class StickyNoteItem:
    id: str
    x: float
    y: float
    width: float
    height: float
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    text: str = ""
    author_id: str = ""
    author_name: str = ""
    expanded: bool = False
    show_author: bool = False
    record: int = 0
    z: tuple[int, int, int] = (0, 0, 0)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": "sticky", "x": round(self.x, 2), "y": round(self.y, 2),
                "width": round(self.width, 2), "height": round(self.height, 2),
                "color": list(self.color), "text_len": len(self.text),
                "author_len": len(self.author_name), "expanded": self.expanded,
                "show_author": self.show_author}


@dataclass
class Page:
    index: int
    uuid: str
    label: str = ""
    document_id: str = ""
    canvas_width: float = 0.0
    canvas_height: float = 0.0
    attachment: str | None = None       # attachments/<uuid> member
    attachment_page: int = 0
    background_kind: str = "blank"
    template: str = ""                  # dynamic template id/string from the background event
    paper_width: float = 595.28
    paper_height: float = 841.89
    strokes: list[Stroke] = field(default_factory=list)
    texts: list[TextBox] = field(default_factory=list)
    images: list[ImageItem] = field(default_factory=list)
    fills: list[FillShapeItem] = field(default_factory=list)
    lines: list[LineItem] = field(default_factory=list)
    shapes: list[ShapeItem] = field(default_factory=list)
    stickies: list[StickyNoteItem] = field(default_factory=list)
    deleted_items: int = 0
    text_items: int = 0
    unknown_items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def stroke_count(self) -> int:
        return len(self.strokes)

    def objects(self):
        """Images, strokes and text boxes in real draw order (bottom -> top)."""
        return sorted([*self.images, *self.fills, *self.strokes, *self.texts,
                       *self.lines, *self.shapes, *self.stickies],
                      key=lambda o: (getattr(o, "z", (0, 0, 0)), getattr(o, "record", 0)))

    @property
    def template_kind(self) -> str:
        t = self.template.lower()
        if "9fe8f365" in t or "ad55b2d7" in t or "squared" in t:
            return "squared"
        if "238ee861" in t or "248bd8da" in t or "ruled narrow" in t:
            return "ruled_narrow"
        if "aa273c17" in t or "ruled wide" in t:
            return "ruled_wide"
        if "cc365888" in t or "dotted" in t:
            return "dotted"
        return ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.index + 1,
            "index": self.index,
            "uuid": self.uuid,
            "label": self.label,
            "document_id": self.document_id,
            "canvas": [round(self.canvas_width, 2), round(self.canvas_height, 2)],
            "background": {"kind": self.background_kind, "attachment": self.attachment,
                           "attachment_page": self.attachment_page,
                           "template": self.template,
                           "template_kind": self.template_kind},
            "strokes": len(self.strokes),
            "texts": len(self.texts),
            "images": len(self.images),
            "fills": len(self.fills),
            "lines": len(self.lines),
            "shapes": len(self.shapes),
            "stickies": len(self.stickies),
            "deleted_items": self.deleted_items,
            "text_items": self.text_items,
            "unknown_items": self.unknown_items,
        }


@dataclass
class Notebook:
    path: str
    title: str
    pages: list[Page] = field(default_factory=list)
    deleted_pages: int = 0
    document_id: str = ""
    attachments: dict[str, str] = field(default_factory=dict)
    unknown: list[dict[str, Any]] = field(default_factory=list)

    def page(self, selector: int | str) -> Page:
        if isinstance(selector, int):
            return self.pages[selector]
        if selector.isdigit():
            return self.pages[int(selector)]
        for p in self.pages:
            if p.uuid == selector or p.uuid.startswith(selector):
                return p
        raise KeyError("no page %r" % selector)
