"""Deterministic fold model for a dieline.

The module turns a flat ``Dieline`` into a foldable ``FoldModel``:

1. Build a planar arrangement of the CUT + CREASE segments (split at every
   intersection / T-junction).
2. Walk the arrangement faces and keep the *material* faces (inside an odd
   number of cut contours, i.e. the actual paper).  Aperture interiors become
   holes of the surrounding face.
3. For every CREASE edge look up the two material faces it separates and turn
   it into a hinge.
4. Assign fold angles (vertical panel folds 90deg, horizontal flap folds
   180deg) and fold direction (mountain/valley).
5. Build a spanning tree rooted at the largest face with BFS; folds that are
   not part of the tree are reported separately.

Everything is pure-python and deterministic: the same dieline always yields the
same model.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

from packstudio.engines.packaging_engine.models.ir import (
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    Dieline,
)

TOL = 1e-7
VERTEX_TOL = 1e-6


@dataclass
class FoldModel:
    """JSON-serialisable fold model consumed by the 3D preview."""

    available: bool
    status: str
    root: int
    faces: list = field(default_factory=list)
    hinges: list = field(default_factory=list)
    non_tree_folds: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "status": self.status,
            "root": self.root,
            "faces": [
                {
                    "id": f["id"],
                    "vertices": [
                        [round(float(x), 6), round(float(y), 6)] for x, y in f["vertices"]
                    ],
                    "holes": [
                        [[round(float(x), 6), round(float(y), 6)] for x, y in hole]
                        for hole in f.get("holes", [])
                    ],
                    "area_mm2": round(float(f["area_mm2"]), 6),
                }
                for f in self.faces
            ],
            "hinges": [_hinge_dict(h) for h in self.hinges],
            "non_tree_folds": [_hinge_dict(h) for h in self.non_tree_folds],
        }


def _hinge_dict(h: dict) -> dict:
    return {
        "parent": h["parent"],
        "child": h["child"],
        "axis": [[round(float(x), 6), round(float(y), 6)] for x, y in h["axis"]],
        "angle_deg": round(float(h["angle_deg"]), 4),
        "fold_direction": h["fold_direction"],
        "label": h.get("label", ""),
    }


# ---------------------------------------------------------------------------
# Small geometry helpers
# ---------------------------------------------------------------------------

def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _point_on_segment(p, a, b, tol: float = TOL) -> bool:
    if _dist(a, b) <= tol:
        return False
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > tol * _dist(a, b):
        return False
    dot = (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])
    if dot < -tol:
        return False
    return dot <= _dist(a, b) ** 2 + tol


def _project_t(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= 0:
        return 0.0
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2


def _interp(a, b, t):
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _segment_intersection(a, b, c, d):
    """Proper (transverse) intersection point of segments ab and cd."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    denom = rx * sy - ry * sx
    if abs(denom) <= 1e-12:
        return None
    t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / denom
    u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / denom
    if -TOL <= t <= 1 + TOL and -TOL <= u <= 1 + TOL:
        return _interp(a, b, max(0.0, min(1.0, t)))
    return None


def _ring_area(ring) -> float:
    total = 0.0
    m = len(ring)
    for i in range(m):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % m]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _contains(point, ring) -> bool:
    x, y = point
    inside = False
    m = len(ring)
    for i in range(m):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % m]
        if (y1 > y) != (y2 > y):
            xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xint:
                inside = not inside
    return inside


def _centroid(ring):
    m = len(ring)
    cx = sum(p[0] for p in ring) / m
    cy = sum(p[1] for p in ring) / m
    return (cx, cy)


# ---------------------------------------------------------------------------
# Planar arrangement
# ---------------------------------------------------------------------------

def _arrangement(segments):
    """Split segments at every intersection; return (vertices, edges).

    ``edges`` maps an undirected vertex pair ``(i, j)`` (i < j) to the list of
    source segment indices that produced it.
    """
    verts = []

    def vid(p):
        for i, v in enumerate(verts):
            if _dist(p, v) <= VERTEX_TOL:
                return i
        verts.append((float(p[0]), float(p[1])))
        return len(verts) - 1

    n = len(segments)
    splits = []
    for i in range(n):
        s = segments[i]
        a = (s.x1, s.y1)
        b = (s.x2, s.y2)
        ts = {0.0, 1.0}
        for j in range(n):
            if j == i:
                continue
            t = segments[j]
            c = (t.x1, t.y1)
            d = (t.x2, t.y2)
            for p in (c, d):
                if _point_on_segment(p, a, b):
                    tt = _project_t(p, a, b)
                    if -TOL <= tt <= 1 + TOL:
                        ts.add(max(0.0, min(1.0, tt)))
            inter = _segment_intersection(a, b, c, d)
            if inter is not None:
                ts.add(_project_t(inter, a, b))
        ordered = sorted(ts)
        merged = []
        for t in ordered:
            if not merged or t - merged[-1] > 1e-9:
                merged.append(t)
        splits.append(merged)

    edges = {}
    for i in range(n):
        s = segments[i]
        a = (s.x1, s.y1)
        b = (s.x2, s.y2)
        ids = [vid(_interp(a, b, t)) for t in splits[i]]
        for k in range(len(ids) - 1):
            i1, i2 = ids[k], ids[k + 1]
            if i1 == i2:
                continue
            key = (i1, i2) if i1 < i2 else (i2, i1)
            if key not in edges:
                edges[key] = []
            if i not in edges[key]:
                edges[key].append(i)
    return verts, edges


def _face_walks(verts, edges):
    """Return directed-edge walks, one per face (interior on the left)."""
    adj = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    for a in adj:
        adj[a].sort(
            key=lambda b: math.atan2(verts[b][1] - verts[a][1], verts[b][0] - verts[a][0])
        )
    remaining = set()
    for a, b in edges:
        remaining.add((a, b))
        remaining.add((b, a))
    walks = []
    while remaining:
        start = min(remaining)
        walk = []
        cur = start
        guard = 0
        while cur in remaining and guard < 200000:
            remaining.remove(cur)
            walk.append(cur)
            a, b = cur
            fan = adj[b]
            nxt = fan[(fan.index(a) - 1) % len(fan)]
            cur = (b, nxt)
            guard += 1
        walks.append(walk)
    return walks


def _simple_ring(walk, verts):
    """Drop spike vertices so the walk becomes a simple polygon."""
    ids = [a for a, _ in walk]
    changed = True
    while changed and len(ids) > 2:
        changed = False
        m = len(ids)
        for i in range(m):
            if ids[i - 1] == ids[(i + 1) % m]:
                del ids[i]
                changed = True
                break
    return [list(verts[i]) for i in ids]


def _left_sample(walk, verts):
    a = verts[walk[0][0]]
    b = verts[walk[0][1]]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        return (a[0], a[1])
    offset = min(length * 1e-5, 1e-5)
    return ((a[0] + b[0]) / 2 - dy / length * offset, (a[1] + b[1]) / 2 + dx / length * offset)


# ---------------------------------------------------------------------------
# Fold direction / angle inference
# ---------------------------------------------------------------------------

#: 折叠语义标签：出现在压线 label 中即视为“翻板向后平折”。
#: 粘口/插耳/防尘耳/锁舌都向印刷面背面折入盒内。
_FLAP_TOKENS = ("TUCK", "DUST", "LOCK", "LID", "FLAP", "GLUE")


def _fold_angle(seg, axis) -> float:
    """Flap folds (by label) -> 180deg; panel folds -> 90deg.

    方向只由语义标签决定，与折线水平/竖直无关：屋脊（CREASE_ROOF_*）
    是水平面板折线（90°），而插耳/防尘耳无论朝向都平折（180°）。
    """
    label = (seg.label or "").upper()
    if any(tok in label for tok in _FLAP_TOKENS):
        return 180.0
    return 90.0


def _fold_direction(seg, axis) -> str:
    """Deterministic mountain/valley inference.

    An explicit ``fold_direction`` on the segment wins.  Otherwise creases
    whose label marks a flap (tuck / dust / lock / lid / glue) fold behind
    the printed face — a mountain fold; every other crease is a valley
    fold (the printed side stays convex).  The result is always a concrete
    direction so the preview stays available for standard cartons.
    """
    explicit = getattr(seg, "fold_direction", "auto")
    if explicit in ("mountain", "valley"):
        return explicit
    label = (seg.label or "").upper()
    if any(tok in label for tok in _FLAP_TOKENS):
        return "mountain"
    return "valley"


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_fold_model(dieline: Dieline) -> FoldModel:
    try:
        return _build(dieline)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash
        return FoldModel(False, f"fold model unavailable: {exc}", -1)


def _build(dieline: Dieline) -> FoldModel:
    cut = [s for s in dieline.segments if s.layer == LAYER_CUT]
    # 粘口折线（GLUE 层）同样是真实折线：粘口面板要参与折叠动画。
    crease = [s for s in dieline.segments if s.layer in (LAYER_CREASE, LAYER_GLUE)]
    if not cut:
        return FoldModel(False, "no CUT segments", -1)

    segments = list(cut) + list(crease)
    verts, edges = _arrangement(segments)
    if not edges:
        return FoldModel(False, "empty planar arrangement", -1)

    walks = _face_walks(verts, edges)

    # --- classify faces ----------------------------------------------------
    owner = {}
    face_edges = defaultdict(set)
    face_records = []
    material_index = 0

    for walk in walks:
        ring = _simple_ring(walk, verts)
        if len(ring) < 3:
            continue
        area = _ring_area(ring)
        if abs(area) <= 1e-9:
            continue
        sample = _left_sample(walk, verts)
        face_records.append(
            {"walk": walk, "ring": ring, "area": area, "sample": sample}
        )

    # Cut rings: closed CUT contours built from the CUT segments alone.  Only
    # positively-oriented (CCW) loops are kept, so a point's containment parity
    # tells whether it is inside the material (odd) or in a hole / exterior
    # (even).
    cut_verts, cut_edges = _arrangement(cut)
    cut_rings = []
    for cut_walk in _face_walks(cut_verts, cut_edges):
        cut_ring = _simple_ring(cut_walk, cut_verts)
        if len(cut_ring) >= 3 and _ring_area(cut_ring) > 1e-9:
            cut_rings.append(cut_ring)

    material = []
    holes = []
    for rec in face_records:
        count = sum(1 for cr in cut_rings if _contains(rec["sample"], cr))
        if count % 2 != 1:
            # Outside every cut contour (the exterior face).
            continue
        if rec["area"] < 0:
            # Negative-area ring = an aperture interior (a hole).
            holes.append(rec)
        else:
            material.append(rec)

    if not material:
        return FoldModel(False, "no material faces detected", -1)

    faces = []
    for rec in material:
        rec["id"] = material_index
        faces.append(
            {
                "id": material_index,
                "vertices": rec["ring"],
                "holes": [],
                "area_mm2": abs(rec["area"]),
                "centroid": _centroid(rec["ring"]),
            }
        )
        material_index += 1

    # Attach aperture interiors as holes of their enclosing material face.
    for rec in holes:
        containers = [f for f in faces if _contains(rec["sample"], f["vertices"])]
        if not containers:
            continue
        host = min(containers, key=lambda f: f["area_mm2"])
        host["holes"].append(rec["ring"])
        host["area_mm2"] = max(0.0, host["area_mm2"] - abs(rec["area"]))

    # Map each directed edge of a material walk to its face id.
    for rec in material:
        fid = rec["id"]
        for a, b in rec["walk"]:
            owner[(a, b)] = fid
            face_edges[fid].add((a, b))

    # --- hinges -----------------------------------------------------------
    raw_hinges = []
    for (a, b), srcs in edges.items():
        crease_src = None
        for si in srcs:
            # GLUE 层折线（GLUE_FOLD）与 CREASE 同为真实折线，均可作铰链。
            if segments[si].layer in (LAYER_CREASE, LAYER_GLUE):
                crease_src = si
                break
        if crease_src is None:
            continue
        fa = owner.get((a, b))
        fb = owner.get((b, a))
        if fa is None or fb is None or fa == fb:
            continue
        seg = segments[crease_src]
        # Parent = larger face; orient axis along the parent's CCW boundary.
        if faces[fa]["area_mm2"] >= faces[fb]["area_mm2"]:
            parent, child = fa, fb
        else:
            parent, child = fb, fa
        if (a, b) in face_edges[parent]:
            axis = (list(verts[a]), list(verts[b]))
        else:
            axis = (list(verts[b]), list(verts[a]))
        raw_hinges.append(
            {
                "parent": parent,
                "child": child,
                "axis": axis,
                "angle_deg": _fold_angle(seg, axis),
                "fold_direction": _fold_direction(seg, axis),
                "label": seg.label or "",
            }
        )

    if not raw_hinges:
        return FoldModel(False, "no crease hinges detected", -1)

    # --- spanning tree (BFS from the largest face) ------------------------
    root = max(faces, key=lambda f: f["area_mm2"])["id"]
    adjacency = defaultdict(list)
    for idx, hinge in enumerate(raw_hinges):
        adjacency[hinge["parent"]].append(idx)
        adjacency[hinge["child"]].append(idx)

    seen = {root}
    queue = deque([root])
    tree_hinges = []
    used = set()
    while queue:
        current = queue.popleft()
        for idx in adjacency.get(current, []):
            hinge = raw_hinges[idx]
            if hinge["parent"] == current:
                oriented = dict(hinge)
                other = hinge["child"]
            else:
                oriented = {
                    **hinge,
                    "parent": hinge["child"],
                    "child": hinge["parent"],
                    "axis": [list(hinge["axis"][1]), list(hinge["axis"][0])],
                }
                other = hinge["parent"]
            if other in seen:
                continue
            seen.add(other)
            used.add(idx)
            tree_hinges.append(oriented)
            queue.append(other)

    non_tree = [h for i, h in enumerate(raw_hinges) if i not in used]

    # Serialise faces (drop the internal centroid helper).
    serialisable_faces = [
        {
            "id": f["id"],
            "vertices": f["vertices"],
            "holes": f["holes"],
            "area_mm2": f["area_mm2"],
        }
        for f in faces
    ]

    status = ""
    if len(seen) != len(faces):
        status = f"{len(faces) - len(seen)} material face(s) not reachable by the hinge tree"

    return FoldModel(
        available=True,
        status=status,
        root=root,
        faces=serialisable_faces,
        hinges=tree_hinges,
        non_tree_folds=non_tree,
    )


def fold_model_to_dict(dieline: Dieline) -> dict:
    """Convenience wrapper returning the JSON-safe fold model."""
    return build_fold_model(dieline).to_dict()
