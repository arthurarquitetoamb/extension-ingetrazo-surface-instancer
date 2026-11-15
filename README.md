# Surface Instancer

**One copy of a group on every subface of a surface** — bricks on a curved wall,
cobogós on a serpentine screen, panels on a dome — each copy tangent to the
surface and all of them sharing a single prototype mesh.

A plugin for [IngeTrazo](https://github.com/ingelibre/ingetrazo).

<img width="640" height="509" alt="Instancer" src="https://github.com/user-attachments/assets/800b51ae-4d62-4b00-a52a-197db5cf1748" />

## What it does

Select the object to repeat (the *child*: a brick, a panel, a bracket) **and**
the faces that carry it (the *parent*: one face or the subfaces of a surface),
then run **Extensions ▸ Surface Instancer ▸ Copy group onto selected faces…**
Close the group you are editing first: the faces of the surface must be loose
faces of the model.

Select the group and the faces together (Ctrl-click), exactly like assigning a
parent surface and a child object. The same command is in the viewport's
right-click menu whenever both are selected.

**One prototype, many instances.** Every copy is a component instance over one
prototype mesh: the definition is built once and shared, so a thousand bricks
cost a thousand 4×4 matrices, not a thousand meshes. Editing the definition
reaches every copy; moving one copy never moves another.

**One undo step.** The copies (and the container group, if you ask for one)
come in together and leave together.

## Options

| Option | What it does |
| --- | --- |
| **Alignment** | *Lay on the face*: the copy's Z follows the face normal (a brick against a wall). *Stand upright*: Z stays vertical and only the heading follows the surface (a post on a floor, a panel in front of a wall). |
| **Y direction** | Where the copy's Y points, projected on each face: *Surface up* (world Z on the face — the direction a brick course climbs), *World X*, *World Y*, or the *longest edge of the first face*. One reference serves the whole run. |
| **Anchor** | *Centre of the copy*: its bounding-box centre sits on the face point. *Side that touches the surface*: its own side nearest the surface sits exactly on it, so bricks start at the wall and grow outwards. |
| **Offset along the normal** | Pushes every copy in or out of the surface — a deliberate gap, a mortar joint, a sunken panel. |
| **Rotation about the normal** | A fixed turn for every copy, e.g. 90° for a running bond. |
| **Size** | *Keep the group's real size* (Scale multiplies it), or *Fit the subface*: each copy stretches to the width and height of its face, its depth left as modelled, so nothing becomes paper-thin. On a non-rectangular patch the face's bounding box is used; the fit is measured before Rotation turns the copy, so turning it keeps the fitted size. |
| **Scale** | Multiplies the size in either Size mode. |
| **Colour** | *Keep the group's own colour*, *One colour for every copy*, *Gradient across the copies* (first copy takes the first swatch, last takes the second, the rest stepping between them in the order the surface gives), or *Random colour per copy*. The colour is each instance's own paint, so the prototype stays shared. |
| **Whole curved surface** | A face that is one patch of a curved surface brings the patches it is soft-joined to, so selecting a single patch of a curved wall still fills the wall. |
| **Collect into one group** | The copies are nested in one container ("Brick instances") instead of landing loose in the model, so the wall moves as one object while every brick stays an instance. |

## How the copies are oriented

* The source's own placement is not carried over (its rotation about the world
  axes and its position): each copy is oriented by its face. Use **Rotation
  about the normal** for the rest, and **Scale** for size.
* A component instance (or a copied group) is reused as it is: the new copies
  share its definition with the original and with each other.
* A classic group is copied once into local coordinates and every instance
  shares that copy; its own axes are honoured, so a brick modelled rotated
  comes out axis-aligned.
* The subfaces are treated as **one side** of the surface: a patch whose
  winding disagrees with its neighbours is turned to match them, so a copy
  never flips 180° from one patch to the next. This matters on imported or
  hand-drawn surfaces, where patch normals often point both ways.
* The copies' Y is the surface's own up, projected on each face and taken from
  one reference for the whole run — never from that face's longest edge, which
  once made horizontal patches turn 90° against their neighbours.
* Where the chosen reference has no direction on a face (world Z on a floor, X
  on a face that looks along X), the face uses one fallback chosen for the
  whole run — the average of the valid directions, or a world axis — so a
  course never breaks at one patch.

## Paint

Colour modes paint the **instance**, not the shared prototype mesh, so each
copy can differ (gradient, random) without copying any geometry. Following
IngeTrazo's rule, a container's paint shows on the faces that wear the default
material; a face with its own paint keeps it. A texture inherited from the
source is replaced when you ask for a colour.

## Install

1. Copy `surface_instancer.py` into the plugins folder:
   * Windows — `%APPDATA%\ingetrazo\plugins\`
   * Linux — `~/.local/share/ingetrazo/plugins/`
   You can reach it from **Extensions ▸ Open plugins folder**.
2. Restart IngeTrazo. The plugin appears under **Extensions ▸ Surface
   Instancer**, and in the viewport's right-click menu when a group and faces
   are selected together.

Requires IngeTrazo 0.6.x (tested with 0.6.2.1). The plugin uses only the
public core API (`Group`, `Mesh.surface_of`, `core.history.Command`), so it
does not need to be built with the application.

Notes:

* Run it at the top level: inside a group being edited the faces are not the
  model's loose faces, and the run is refused with a message.
* Degenerate (zero-area) faces are skipped rather than crashed on.
* A refused run changes nothing.

## License

GPL-3.0-or-later, matching IngeTrazo.
