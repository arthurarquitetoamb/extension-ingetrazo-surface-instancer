# SPDX-License-Identifier: GPL-3.0-or-later
"""Surface Instancer — one copy of a group on every subface of a surface.

Select the group to repeat (the child: a brick, a panel, a bracket) **and**
the faces that carry it (the parent: the surface of a wall, a dome, a
terrace — one face or a mesh of subfaces), then run
Extensions ▸ Surface Instancer ▸ Copy group onto selected faces. For each
subface one copy is placed, oriented by that face: the child's local Z
follows the face normal (or stays vertical, if you prefer) and its local X
follows the surface's horizontal direction, so bricks on a curved wall come
out tangent to it instead of all facing the same way.

How the copies are made
-----------------------
Every copy is a COMPONENT INSTANCE over ONE prototype mesh: the definition
is built once and shared, so a thousand bricks cost a thousand 4×4 matrices,
not a thousand meshes — and editing the definition reaches every copy, while
moving one copy never moves another.

* The source's own placement is not carried over (its rotation about the
  world axes and its position): each copy is oriented by its face. Use
  **Rotation about the normal** for the rest, and **Scale** for size.
* A component instance (or a copied group) is reused as it is: the new
  copies share its definition with the original and with each other.
* A classic group is copied once into local coordinates and every instance
  shares that copy; its own axes are honoured, so a brick modelled rotated
  comes out axis-aligned.
* The subfaces are treated as ONE side of the surface: a patch whose winding
  disagrees with its neighbours is turned to match them, so a copy never
  flips 180° from one patch to the next. The copies' Y axis is the surface's
  own "up", projected on each face and taken from one reference for the
  whole run — never from that face's longest edge.

Options
-------
* **Alignment** — *Lay on the face*: the copy's Z lies along the face normal
  (a brick laid against a wall). *Stand upright*: the copy's Z stays
  vertical and only its heading follows the surface (a post on a floor, a
  panel in front of a wall).
* **Y direction** — where the copy's Y points, projected on each face:
  *Surface up* (world Z on the face — the direction a brick course climbs),
  *World X*, *World Y*, or the *longest edge of the first face* (for courses
  that must follow a particular edge). One reference serves the whole run.
* **Anchor** — *Centre of the copy*: the copy's bounding-box centre sits on
  the face point. *Side that touches the surface*: the copy's own side
  nearest the surface sits exactly on it, so bricks start at the wall and
  grow outwards.
* **Offset along the normal** — pushes every copy in or out of the surface
  (negative = into it): a deliberate gap, a mortar joint, a sunken panel.
* **Size** — *Keep the group's real size*: every copy keeps the size it was
  modelled with, and **Scale** multiplies it. *Fit the subface*: each copy is
  stretched to the width and height of its face — the face's extent along
  the copy's own X and Y — while its depth stays as modelled, so bricks fill
  each patch of a wall without becoming paper-thin. On a non-rectangular
  patch the face's bounding box is used; the fit is measured before
  **Rotation** turns the copy, so turning it keeps the fitted size; and
  **Scale** still multiplies the fitted size.
* **Rotation about the normal** and **Scale** — a fixed turn and size for
  every copy.
* **Colour** — *Keep the group's own colour* (the source's paint goes to
  every copy), *One colour for every copy*, *Gradient across the copies*
  (the first copy takes the first colour and the last the second, the others
  stepping between them in the order the surface gives), or *Random colour
  per copy*. The colour is the instance's own paint, so the prototype mesh
  stays shared: it shows on the faces that wear the default material, and a
  face with its own paint keeps it.
* **Whole curved surface** — a face that is one patch of a curved surface
  brings the patches it is soft-joined to (Mesh.surface_of), so selecting a
  single patch of a curved wall still fills the wall.
* **Collect into one group** — the copies are nested in one container
  ("Brick instances") instead of landing loose in the model, so the wall
  moves as one object while every brick stays an instance.

Everything is one undo step, and a run that fails changes nothing.

Install
-------
Copy this file into the plugins folder — Extensions ▸ Open plugins folder
(``%APPDATA%\\ingetrazo\\plugins\\`` on Windows,
``~/.local/share/ingetrazo/plugins/`` on Linux) — and restart IngeTrazo.
It appears under Extensions ▸ Surface Instancer, and in the viewport's
right-click menu whenever a group and faces are selected together.

Notes
-----
* Run it at the top level (close the group you are editing first): the faces
  of the surface must be loose faces of the model.
* One group, many faces: select the group and the faces in one selection
  (Ctrl-click), exactly like assigning a parent surface and a child object.
* Degenerate (zero-area) faces are skipped rather than crashed on.
"""
from __future__ import annotations

import colorsys
import copy
import logging
import math
import random

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QMatrix4x4, QVector3D
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.group import Group, group_frame, transformed_mesh, world_mesh
from core.history import Command
from core.mesh import Face

log = logging.getLogger("ingetrazo.plugins.surface_instancer")

#: Alignment modes (``opts["align"]``).
ALIGN_NORMAL = "normal"      # the copy's Z follows the face normal
ALIGN_UPRIGHT = "upright"    # the copy's Z stays vertical

#: Anchor modes (``opts["anchor"]``).
ANCHOR_CENTER = "center"     # the copy's bounding-box centre on the face point
ANCHOR_CONTACT = "contact"   # the copy's side nearest the surface, on the point

#: Y reference modes (``opts["y_ref"]``) — where the copy's Y comes from.
YREF_SURFACE = "surface"    # the surface's own up: world Z projected on the face
YREF_X = "x"                # world X, projected on the face
YREF_Y = "y"                # world Y, projected on the face
YREF_EDGE = "edge"          # the longest edge of the first face of the run

#: Colour modes (``opts["color_mode"]``) — the copies' own paint.
COLOR_KEEP = "keep"          # the source's paint, copied as it is
COLOR_SOLID = "solid"        # one colour for every copy
COLOR_GRADIENT = "gradient"  # first copy to last copy, stepping between
COLOR_RANDOM = "random"      # a fresh colour per copy

#: Size modes (``opts["size_mode"]``).
SIZE_KEEP = "keep"           # the group's own size, times Scale
SIZE_FIT = "fit"             # width and height stretched to each subface

#: Defaults for the colour pickers (terracotta → sand, a brick course).
DEFAULT_COLOR = (0.757, 0.400, 0.247)
DEFAULT_COLOR_END = (0.910, 0.788, 0.627)

_UP = QVector3D(0.0, 0.0, 1.0)
_EPS = 1e-9


class InstancerError(Exception):
    """A user-facing refusal (bad selection, empty source group…)."""


# ---------------------------------------------------------------------------
# Vector and frame math (no document access — testable on its own)

def _unit(v):
    """``v`` normalized, or None for a vanishing vector."""
    length = v.length()
    if length < _EPS:
        return None
    return v / length


def _normal_of(face):
    """A unit normal for ``face``, with Face.normal()'s own fallback."""
    n = _unit(face.normal())
    return n if n is not None else QVector3D(0.0, 0.0, 1.0)


def _square_to(z, v):
    """``v`` projected into the plane normal to ``z``, unit — or None when it
    lies along ``z`` and has no direction there."""
    return _unit(v - z * QVector3D.dotProduct(v, z))


def _axis_z(normal, mode):
    """The copy's Z: the face normal, or world up when standing upright.

    A horizontal face has no "upright" of its own, so it keeps its normal.
    """
    if mode == ALIGN_UPRIGHT and abs(normal.z()) < 0.999:
        return _UP
    return normal


def _primary_tangent(normal, mode):
    """The copy's Y before any fallback: the surface's own "up" along it.

    Laid on the face, Y is world up projected on the face — the direction a
    brick course climbs. Standing upright, Y points the way the current code
    always has: into the face (the copy's front looks out of the surface).
    None on a horizontal face, where neither exists.
    """
    z = _axis_z(normal, mode)
    if mode == ALIGN_UPRIGHT:
        return _unit(-(normal - z * QVector3D.dotProduct(normal, z)))
    return _square_to(z, _UP)


def _longest_edge(face):
    """The face's longest side, as a unit direction — the reference the
    ``YREF_EDGE`` option takes from the first face of the run."""
    best = None
    best_len = _EPS
    pts = face.vertices
    count = len(pts)
    for i in range(count):
        d = pts[(i + 1) % count] - pts[i]
        length = d.length()
        if length > best_len:
            best, best_len = d, length
    return _unit(best) if best is not None else None


def _reference_tangent(normal, mode, reference, edge):
    """The copy's Y from the chosen reference, square to its Z — or None
    when that reference lies along Z and has no direction on this face."""
    z = _axis_z(normal, mode)
    if reference == YREF_X:
        return _square_to(z, QVector3D(1.0, 0.0, 0.0))
    if reference == YREF_Y:
        return _square_to(z, QVector3D(0.0, 1.0, 0.0))
    if reference == YREF_EDGE:
        return _square_to(z, edge) if edge is not None else None
    return _primary_tangent(normal, mode)


def _basis(normal, mode, degrees, reference=YREF_SURFACE, edge=None,
           fallback=None):
    """``(x, y, z)`` unit axes that orient a copy on a face.

    ``z`` follows the surface (the face normal, or world up when upright);
    ``y`` comes from the chosen reference (the surface's up, world X, world
    Y, or the first face's longest edge) and ``x`` completes a right-handed
    frame, ``x × y = z``. Where that reference has no direction on the face,
    ``y`` comes from ``fallback`` — ONE reference chosen for the whole run —
    so consecutive patches agree instead of each taking its own longest edge.
    ``degrees`` turns ``x``/``y`` about ``z``.
    """
    n = _unit(QVector3D(normal)) or QVector3D(0.0, 0.0, 1.0)
    z = _axis_z(n, mode)

    y = _reference_tangent(n, mode, reference, edge)
    if y is None and fallback is not None:
        y = _square_to(z, fallback)
    if y is None:
        for axis in (QVector3D(1.0, 0.0, 0.0), QVector3D(0.0, 1.0, 0.0),
                     QVector3D(0.0, 0.0, 1.0)):
            y = _square_to(z, axis)
            if y is not None:
                break
    if y is None:                                # not reached by a real face
        y = QVector3D(0.0, 1.0, 0.0)

    x = _unit(QVector3D.crossProduct(y, z)) or QVector3D(1.0, 0.0, 0.0)

    if degrees:
        rad = math.radians(float(degrees))
        c, s = math.cos(rad), math.sin(rad)
        turned = _unit(x * c + y * s)
        if turned is not None:
            x = turned
            y = _unit(QVector3D.crossProduct(z, x)) or y

    return x, y, z


def _bbox(points):
    """``(lo, hi)`` tuples of an (N, 3) point sequence."""
    xs = [p.x() for p in points]
    ys = [p.y() for p in points]
    zs = [p.z() for p in points]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def _anchor(lo, hi, mode, local_normal):
    """Where the copy's origin sits, in its own prototype coordinates.

    With ``ANCHOR_CONTACT`` ``local_normal`` — the face normal expressed in
    the copy's frame — picks the side of the bounding box the surface
    touches: the side it points away from. A direction too close to zero to
    choose (a face normal that vanished) falls back to the centre.
    """
    mid = tuple((lo[i] + hi[i]) * 0.5 for i in range(3))
    if mode != ANCHOR_CONTACT:
        return mid
    comps = (local_normal.x(), local_normal.y(), local_normal.z())
    axis = max(range(3), key=lambda i: abs(comps[i]))
    if abs(comps[axis]) < _EPS:
        return mid
    point = list(mid)
    # The surface lies on the side the local normal points AWAY from.
    point[axis] = hi[axis] if comps[axis] < 0.0 else lo[axis]
    return tuple(point)


def _translation(offset):
    m = QMatrix4x4()
    m.translate(QVector3D(*offset))
    return m


def _pose(x, y, z, origin):
    """A matrix whose columns are ``x``, ``y``, ``z`` and whose origin is ``origin``."""
    return QMatrix4x4(
        x.x(), y.x(), z.x(), origin.x(),
        x.y(), y.y(), z.y(), origin.y(),
        x.z(), y.z(), z.z(), origin.z(),
        0.0, 0.0, 0.0, 1.0,
    )


# ---------------------------------------------------------------------------
# Turning the document into prototypes and instances

def _source_mesh(child):
    """The prototype to share, in coordinates of its own.

    A component instance (or a copied group) already keeps a shared
    definition in local coordinates — reuse it, so editing that definition
    later reaches every copy this plugin made. Anything else (a classic
    group, a group that owns nested parts) is copied ONCE into its own axes
    and every instance shares that copy.
    """
    if child.xform is not None and not child.children:
        return child.mesh
    frame = group_frame(child) or QMatrix4x4()
    inverse, ok = frame.inverted()
    return transformed_mesh(world_mesh(child),
                            inverse if ok else QMatrix4x4())


def _target_faces(scene, faces, join_surface):
    """The faces to place copies on, in selection order, without repeats.

    ``join_surface`` brings in the faces soft-joined to each selected one
    (the patches of one curved surface), as IngeTrazo considers them one
    surface. A mesh that cannot answer is no reason to fail the run.
    """
    out: list = []
    seen: set = set()
    for face in faces:
        group = [face]
        if join_surface:
            try:
                joined = list(scene.mesh.surface_of(face))
            except Exception:                    # noqa: BLE001 - be forgiving
                joined = []
            if joined:
                group = joined
                if face not in group:
                    group.append(face)
        for target in group:
            if id(target) not in seen:
                seen.add(id(target))
                out.append(target)
    return out


def _oriented_normals(faces):
    """Unit normals of ``faces``, all pointing to the SAME side of the surface.

    A surface's patches are often drawn (or imported) with mixed winding, and
    a copy placed by each face's own normal is then turned 180° from one patch
    to the next — the copy's Y flipped on "some faces". A patch whose normal
    makes more than 120° with its neighbour's is a winding disagreement, not a
    fold (soft-joined surfaces bend gently), so its normal is turned to match.
    """
    raw = {face: _normal_of(face) for face in faces}
    shared: dict = {}
    for face in faces:
        loop = face.loop
        count = len(loop)
        for i in range(count):
            key = frozenset((id(loop[i]), id(loop[(i + 1) % count])))
            shared.setdefault(key, []).append(face)

    out: dict = {}
    # The largest patch sets the side: it is the least likely to be a sliver.
    for seed in sorted(faces, key=lambda f: f.area(), reverse=True):
        if seed in out:
            continue
        out[seed] = raw[seed]
        stack = [seed]
        while stack:
            face = stack.pop()
            normal = out[face]
            loop = face.loop
            count = len(loop)
            for i in range(count):
                key = frozenset((id(loop[i]), id(loop[(i + 1) % count])))
                for other in shared.get(key, ()):
                    if other is face or other in out:
                        continue
                    n_other = raw[other]
                    if QVector3D.dotProduct(normal, n_other) < -0.5:
                        n_other = -n_other
                    out[other] = n_other
                    stack.append(other)
    return out


def _fallback_tangent(faces, normals, mode, reference, edge):
    """One Y reference for the whole run, for patches where the chosen
    reference has no direction of its own.

    The average of the tangents the faces DO have — a vault's patches all lean
    one way, so their up is nearly the same — keeps a horizontal patch in step
    with its neighbours instead of giving it that face's own longest edge.
    With nothing to average (a floor, or a surface that wraps all the way
    round), a world axis square to the average normal is used.
    """
    acc = QVector3D(0.0, 0.0, 0.0)
    for face in faces:
        tangent = _reference_tangent(normals[face], mode, reference, edge)
        if tangent is not None:
            acc = acc + tangent
    tangent = _unit(acc)
    if tangent is not None:
        return tangent

    average = QVector3D(0.0, 0.0, 0.0)
    for face in faces:
        average = average + normals[face]
    average = _unit(average) or _UP
    for axis in (QVector3D(1.0, 0.0, 0.0), QVector3D(0.0, 1.0, 0.0), _UP):
        candidate = _square_to(average, axis)
        if candidate is not None:
            return candidate
    return QVector3D(1.0, 0.0, 0.0)


def _colour(value, fallback):
    """A ``(r, g, b)`` triple of floats in 0..1, or ``fallback``."""
    try:
        rgb = tuple(float(c) for c in value)[:3]
    except (TypeError, ValueError):
        return fallback
    if len(rgb) != 3:
        return fallback
    return tuple(min(1.0, max(0.0, c)) for c in rgb)


def _face_spans(face, x, y):
    """The face's width and height in the copy's own frame — the room a
    fitted copy may take. A non-rectangular patch gives its bounding box."""
    centre = face.centroid()
    along_x = [QVector3D.dotProduct(v - centre, x) for v in face.vertices]
    along_y = [QVector3D.dotProduct(v - centre, y) for v in face.vertices]
    return max(along_x) - min(along_x), max(along_y) - min(along_y)


def _copy_colour(opts, index, total, rng):
    """The paint of copy ``index`` (0-based), or None to keep the source's.

    *One colour* paints them all alike. *Gradient* gives the run's first copy
    the first colour and its last the second, the others stepping between
    them in the order the surface gives — a course dark at one end and light
    at the other. *Random* takes a fresh hue per copy from ``rng``, so a run
    with ``opts["color_seed"]`` can be repeated exactly.
    """
    mode = opts.get("color_mode", COLOR_KEEP)
    if mode == COLOR_SOLID:
        return _colour(opts.get("color"), DEFAULT_COLOR)
    if mode == COLOR_GRADIENT:
        first = _colour(opts.get("color"), DEFAULT_COLOR)
        last = _colour(opts.get("color_end"), DEFAULT_COLOR_END)
        t = index / (total - 1) if total > 1 else 0.0
        return tuple(round(a + (b - a) * t, 6) for a, b in zip(first, last))
    if mode == COLOR_RANDOM:
        return tuple(round(c, 6) for c in
                     colorsys.hsv_to_rgb(rng.random(), 0.62, 0.95))
    return None


def build_instances(child, faces, opts):
    """One component instance of ``child`` per face — built, not placed.

    Raises :class:`InstancerError` before touching the document, so a
    refused run leaves the model exactly as it was. The caller appends the
    returned groups to ``scene.groups`` inside the undo command.
    """
    proto = _source_mesh(child)
    points = [v.position for v in proto.vertices]
    if not points:
        raise InstancerError(
            "The group to copy has no geometry — select a group with a 3D "
            "object in it.")

    lo, hi = _bbox(points)
    scale = float(opts.get("scale", 1.0))
    if scale <= 0.0:
        raise InstancerError("Scale must be greater than zero.")
    align = opts.get("align", ALIGN_NORMAL)
    anchor_mode = opts.get("anchor", ANCHOR_CENTER)
    rotation = float(opts.get("rotation", 0.0))
    offset = float(opts.get("offset", 0.0))

    usable = [face for face in faces if len(face.vertices) >= 3]
    skipped = len(faces) - len(usable)
    if not usable:
        raise InstancerError(
            "No usable face in the selection — every selected face is "
            "degenerate.")
    normals = _oriented_normals(usable)
    y_ref = opts.get("y_ref", YREF_SURFACE)
    edge = _longest_edge(usable[0]) if y_ref == YREF_EDGE else None
    fallback = _fallback_tangent(usable, normals, align, y_ref, edge)

    color_mode = opts.get("color_mode", COLOR_KEEP)
    rng = (random.Random(opts.get("color_seed"))
           if color_mode == COLOR_RANDOM else None)
    total = len(usable)
    size_mode = opts.get("size_mode", SIZE_KEEP)

    made: list = []
    for index, face in enumerate(usable, 1):
        normal = normals[face]
        x, y, z = _basis(normal, align, rotation, y_ref, edge, fallback)

        sx = sy = sz = scale
        if size_mode == SIZE_FIT:
            # Stretch width and height to the face; the depth is untouched.
            # Measured before Rotation turns the copy, so turning it keeps
            # the fitted size instead of inflating it to a turned bbox.
            flat_x, flat_y, _ = _basis(normal, align, 0.0, y_ref, edge,
                                       fallback)
            width, height = _face_spans(face, flat_x, flat_y)
            if hi[0] - lo[0] > _EPS:
                sx = width / (hi[0] - lo[0]) * scale
            if hi[1] - lo[1] > _EPS:
                sy = height / (hi[1] - lo[1]) * scale

        local_normal = QVector3D(
            QVector3D.dotProduct(x, normal),
            QVector3D.dotProduct(y, normal),
            QVector3D.dotProduct(z, normal),
        )
        pivot = _anchor(lo, hi, anchor_mode, local_normal)
        # Prototype → copy space: the pivot lands on the copy's origin.
        local = _translation((-pivot[0], -pivot[1], -pivot[2]))

        origin = face.centroid() + normal * offset
        pose = _pose(x * sx, y * sy, z * sz, origin)

        inst = Group(proto, name=f"{child.name} {index:03d}")
        inst.xform = pose * local
        inst.component = True
        inst.layer = child.layer
        inst.ifc = dict(child.ifc) if child.ifc else None
        inst.ext = copy.deepcopy(child.ext) if child.ext else None
        paint = dict(child.material) if child.material else {}
        colour = _copy_colour(opts, index - 1, total, rng)
        if colour is not None:
            paint["color"] = colour
            paint.pop("texture", None)      # the colour asked for, not a tint
        inst.material = paint or None
        made.append(inst)

    if skipped:
        log.info("surface_instancer: skipped %d degenerate face(s)", skipped)
    return made


class SurfaceInstancerCommand(Command):
    """One undo step: put the copies in the model, or take them out.

    Only the top-level objects are added (a container carries its nested
    placements); no existing group is touched, so the document's other
    groups and their paint stay exactly as they were.
    """

    def __init__(self, factory) -> None:
        self._factory = factory
        self.created: list = []

    def do(self, scene) -> None:
        if not self.created:
            self.created = list(self._factory() or ())
        scene.groups.extend(self.created)
        scene.selection.clear()
        scene.selection.update(self.created)
        scene.version += 1

    def undo(self, scene) -> None:
        for group in self.created:
            if group in scene.groups:
                scene.groups.remove(group)
            scene.selection.discard(group)
        scene.version += 1


# ---------------------------------------------------------------------------
# Selection

def _selection(scene):
    """``(faces, groups)`` chosen together — the parent surface and the child.

    A selected nested placement answers for its top-level object: scattering
    from a part of an imported component repeats the whole component, which
    is what the user sees and can move.
    """
    faces: list = []
    groups: list = []
    for entity in list(scene.selection):
        if isinstance(entity, Face):
            if entity in scene.mesh.faces and entity not in faces:
                faces.append(entity)
            continue
        if getattr(entity, "mesh", None) is None:
            continue
        owner = getattr(entity, "owner", None) or entity
        if owner in scene.groups and owner not in groups:
            groups.append(owner)
    return faces, groups


# ---------------------------------------------------------------------------
# Options dialog

class OptionsDialog(QDialog):
    """The run's settings: alignment, anchor, offset, turn, size, colour,
    grouping."""

    def __init__(self, parent, child_name: str, face_count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Surface Instancer")
        self.setMinimumWidth(420)

        box = QVBoxLayout(self)
        intro = QLabel(
            "One shared copy of the group for each face of the surface.")
        intro.setWordWrap(True)
        box.addWidget(intro)

        form = QFormLayout()
        form.addRow("Copy", QLabel(child_name))
        form.addRow("Faces", QLabel(str(face_count)))

        self.align = QComboBox()
        self.align.addItem("Lay on the face (Z along the normal)", ALIGN_NORMAL)
        self.align.addItem("Stand upright (Z vertical)", ALIGN_UPRIGHT)
        form.addRow("Alignment", self.align)

        self.y_ref = QComboBox()
        self.y_ref.addItem("Surface up (Z of the world)", YREF_SURFACE)
        self.y_ref.addItem("World X", YREF_X)
        self.y_ref.addItem("World Y", YREF_Y)
        self.y_ref.addItem("Longest edge of the first face", YREF_EDGE)
        self.y_ref.setToolTip(
            "Where the copy's Y points, projected on each face. One reference "
            "is used for the whole run, so the copies keep the same heading.")
        form.addRow("Y direction", self.y_ref)

        self.anchor = QComboBox()
        self.anchor.addItem("Centre of the copy", ANCHOR_CENTER)
        self.anchor.addItem("Side that touches the surface", ANCHOR_CONTACT)
        form.addRow("Anchor", self.anchor)

        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1000.0, 1000.0)
        self.offset.setDecimals(3)
        self.offset.setSingleStep(0.01)
        self.offset.setValue(0.0)
        self.offset.setSuffix(" m")
        self.offset.setToolTip(
            "Pushes every copy along the face normal; negative goes into the "
            "surface.")
        form.addRow("Offset along the normal", self.offset)

        self.rotation = QDoubleSpinBox()
        self.rotation.setRange(-360.0, 360.0)
        self.rotation.setDecimals(1)
        self.rotation.setSingleStep(15.0)
        self.rotation.setValue(0.0)
        self.rotation.setSuffix("°")
        form.addRow("Rotation about the normal", self.rotation)

        self.size_mode = QComboBox()
        self.size_mode.addItem("Keep the group's real size", SIZE_KEEP)
        self.size_mode.addItem("Fit the subface (stretch)", SIZE_FIT)
        self.size_mode.setToolTip(
            "Keep: every copy keeps the size it was modelled with. Fit: each "
            "copy is stretched to the width and height of its face, its depth "
            "left as modelled. Scale multiplies either.")
        form.addRow("Size", self.size_mode)

        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.001, 1000.0)
        self.scale.setDecimals(3)
        self.scale.setSingleStep(0.05)
        self.scale.setValue(1.0)
        self.scale.setToolTip(
            "Multiplies the size: the group's own in Keep, the fitted size "
            "in Fit.")
        form.addRow("Scale", self.scale)

        self.color = QComboBox()
        self.color.addItem("Keep the group's own colour", COLOR_KEEP)
        self.color.addItem("One colour for every copy", COLOR_SOLID)
        self.color.addItem("Gradient across the copies", COLOR_GRADIENT)
        self.color.addItem("Random colour per copy", COLOR_RANDOM)
        self.color.setToolTip(
            "Paints each copy itself, leaving the shared prototype mesh "
            "alone. It shows on the faces that wear the default material; a "
            "face with its own paint keeps it.")
        form.addRow("Colour", self.color)

        self._swatches = [QColor.fromRgbF(*DEFAULT_COLOR),
                          QColor.fromRgbF(*DEFAULT_COLOR_END)]
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        self.swatch_first = QPushButton()
        self.swatch_last = QPushButton()
        for button, which, tip in (
                (self.swatch_first, 0, "First copy — click to choose"),
                (self.swatch_last, 1, "Last copy — click to choose")):
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, k=which: self._pick(k))
            row_layout.addWidget(button)
        form.addRow("Colours", row)

        box.addLayout(form)

        self.join_surface = QCheckBox(
            "A selected face brings its whole curved surface (soft edges)")
        self.join_surface.setChecked(True)
        box.addWidget(self.join_surface)

        self.collect = QCheckBox("Collect the copies into one group")
        self.collect.setChecked(True)
        box.addWidget(self.collect)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

        self.color.currentIndexChanged.connect(self._sync_colours)
        self._sync_colours()

    def _pick(self, which: int) -> None:
        """Ask for the first or the last copy's colour."""
        chosen = QColorDialog.getColor(self._swatches[which], self,
                                       "Colour of the copies")
        if chosen.isValid():
            self._swatches[which] = chosen
            self._sync_colours()

    def _swatch_colour(self, which: int) -> QColor:
        """The swatch as shown: dimmed when the mode does not use it."""
        color = self._swatches[which]
        button = self.swatch_first if which == 0 else self.swatch_last
        if button.isEnabled():
            return color
        return QColor((color.red() + 510) // 3, (color.green() + 510) // 3,
                      (color.blue() + 510) // 3)

    def _sync_colours(self) -> None:
        """Enable the swatches the chosen mode uses and show the colours."""
        mode = self.color.currentData()
        self.swatch_first.setEnabled(mode in (COLOR_SOLID, COLOR_GRADIENT))
        self.swatch_last.setEnabled(mode == COLOR_GRADIENT)
        for which, button in ((0, self.swatch_first),
                              (1, self.swatch_last)):
            shown = self._swatch_colour(which)
            ink = "#101010" if shown.lightness() > 140 else "#f4f4f4"
            button.setText(self._swatches[which].name().upper())
            button.setStyleSheet(
                f"QPushButton {{ background-color: {shown.name()};"
                f" color: {ink}; border: 1px solid #6b6b6b;"
                f" padding: 2px 10px; }}")

    def _rgb(self, which: int) -> tuple:
        color = self._swatches[which]
        return (color.redF(), color.greenF(), color.blueF())

    def options(self) -> dict:
        return {
            "align": self.align.currentData(),
            "y_ref": self.y_ref.currentData(),
            "size_mode": self.size_mode.currentData(),
            "color_mode": self.color.currentData(),
            "color": self._rgb(0),
            "color_end": self._rgb(1),
            "anchor": self.anchor.currentData(),
            "offset": self.offset.value(),
            "rotation": self.rotation.value(),
            "scale": self.scale.value(),
            "join_surface": self.join_surface.isChecked(),
            "collect": self.collect.isChecked(),
        }

    @classmethod
    def ask(cls, parent, child_name: str, face_count: int):
        dialog = cls(parent, child_name, face_count)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            return dialog.options()
        finally:
            dialog.deleteLater()


# ---------------------------------------------------------------------------
# The command

def _warn(viewport, text: str) -> None:
    QMessageBox.warning(viewport.window(), "Surface Instancer", text)


def _flash(viewport, text: str) -> None:
    flash = getattr(viewport, "flash_status", None)
    if callable(flash):
        flash(text, 5000)


def _refuse(viewport) -> bool:
    """True when the run must stop: inside a group, nothing usable selected."""
    scene = viewport.scene
    if getattr(scene, "edit_group", None) is not None:
        _warn(viewport, "Close the group you are editing (Esc) and run "
                        "Surface Instancer again.")
        return True

    faces, groups = _selection(scene)
    if not faces and not groups:
        _warn(viewport, "Select the group to copy AND the faces that form the "
                        "surface (Ctrl-click both), then run Surface "
                        "Instancer.")
        return True
    if not groups:
        _warn(viewport, "Also select the group to copy: select it together "
                        "with the faces (Ctrl-click), then run Surface "
                        "Instancer.")
        return True
    if not faces:
        _warn(viewport, "Also select the faces of the parent surface: select "
                        "the group and the faces together (Ctrl-click), then "
                        "run Surface Instancer.")
        return True
    if len(groups) > 1:
        names = ", ".join(g.name for g in groups[:4])
        _warn(viewport, f"Select ONE group to copy — this selection has "
                        f"{len(groups)} ({names}).")
        return True
    if not child_has_geometry(groups[0]):
        _warn(viewport, "The group to copy has no geometry — it holds no "
                        "faces, edges or nested objects.")
        return True
    return False


def child_has_geometry(child) -> bool:
    """Whether there is anything to copy — checked before the dialog, so the
    refusal comes at once instead of after filling it in."""
    return bool(child.mesh.vertices) or bool(getattr(child, "children", None))


def run(viewport) -> None:
    """Ask for the options and place one copy of the group on each face."""
    if _refuse(viewport):
        return
    scene = viewport.scene
    faces, groups = _selection(scene)
    child = groups[0]

    opts = OptionsDialog.ask(viewport.window(), child.name, len(faces))
    if opts is None:
        return

    targets = _target_faces(scene, faces, opts.get("join_surface", True))

    def factory():
        made = build_instances(child, targets, opts)
        if opts.get("collect", True):
            container = Group(name=f"{child.name} instances")
            container.adopt(made)               # nested placements, shared
            container.component = False          # a group of groups
            container.layer = child.layer
            return [container]
        return made

    command = SurfaceInstancerCommand(factory)
    try:
        viewport.history.execute(command)
    except InstancerError as exc:
        _warn(viewport, str(exc))
        return

    notify = getattr(viewport, "notify_scene_changed", None)
    if callable(notify):
        notify()
    viewport.update()
    total = len(targets)
    _flash(viewport,
           f"Surface Instancer: {total} cop{'y' if total == 1 else 'ies'} of "
           f"“{child.name}” placed on {total} face(s).")


# ---------------------------------------------------------------------------
# Menus

def setup(app) -> None:
    """Extensions ▸ Surface Instancer, plus the viewport's right-click menu."""
    sub = app.add_menu("Surface Instancer")
    if sub is not None:
        sub.addAction("Copy group onto selected faces…",
                      lambda: run(app.viewport))

    def context(menu, selection) -> None:
        faces, groups = _selection(app.viewport.scene)
        if not faces or not groups:
            return
        menu.addSeparator()
        # After the popup closes, like every dialog opened from a menu.
        menu.addAction(
            "Surface Instancer: copy group onto faces…",
            lambda: QTimer.singleShot(0, lambda: run(app.viewport)))

    app.add_context_menu(context)
