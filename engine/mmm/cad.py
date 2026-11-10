"""CAD preset: recognise analytic surfaces and build an editable B-rep."""
# Part of Manifold Mesh Mix (c) 2026 Tuna-The-First and Claude, AGPL-3.0, commit 4cf72ea.
# Modified 2026-11-08: _cusp_crossing looks only at a border holding two circular arcs, all it looks for (_circular_edges),
# and a crossing elsewhere no longer stops it (helical thread borders read back from a file cross themselves, f_long_d8_l12).
# Modified 2026-11-07 (loose edges, step 2): a pooled surface merged with another by _merge_equal keeps its lineage (pooled, the union of the members' pieces), so a face that doesn't build goes back to the pieces instead of freeform cells.
# Modified 2026-11-06 (loose edges, step 2, tc's tori): a pooling join lands only on a kind a member has or a generalisation every member shares, never on a spindle torus (POOL_SPINDLE), cylinders whose axes are within POOL_BEND degrees never on a torus, and a torus or cone of cylinder tiles keeps each tile's radius within its own uncertainty (POOL_SIGMA, _radius_sigma).
# Modified 2026-11-06 (loose edges, step 2): in the pooling's freeform stage, a cell takes a member only when it holds the member's own vertices as closely as the member's surface did (POOL_LEAVE), and never a piece holding a drawn flat; a drawn flat inside a piece is designed_flats' verdict with no floor of the pool's own (_drawn_flats_in).
# Modified 2026-11-05 (loose edges, step 2a): neighbouring pieces that fit one simple surface together are pooled into it
# (_pool_pieces, from _merge_equal): the tiles a smooth area was broken into met along borders with no exact curve.
# Modified 2026-11-08: OCC's wire check (SelfIntersect) runs in meshguard's watched child (self_crossings), so a crash in it
# fails only that face, not the conversion (_self_crossing_regions, _small_neighbours_crossing, _cusp_crossing).
# Modified 2026-11-07: a face read back bigger than written is that face's, and broken, not a stop that leaves every later face
# without its piece (_read_back_pieces grown): allen-key's one bad torus made 33 sound faces count as broken.
# Modified 2026-11-04 (threads): a long border's curve takes more of the chain's points wherever it strays, with no limit on how many, and is measured
# both ways at a cost that grows with the chain, not its square (_fit_long_chain, _ChainRuler); what's left goes through every point (_through_every_point).
# Modified 2026-11-04: a flat piece the mesh shows to be a designed plane stays one (fidelity.designed_flats, _make_freeform), and the flat faces of the last resort may take in a neighbour they break (LAST_ROUNDS).
# Modified 2026-11-04: a big stray freeform surface is cut up and fitted again, not made flat (FLAT_FEW); a void read-back doesn't end the attempt.
# Modified 2026-11-04: a chord's error isn't its face's, and surfaces left out of the fit check are left out on both sides (_chords_let_off).
# Modified 2026-11-04: a seam keeps clear of the loops it joins, along its whole length (_seam_score), and so does one to a pole.
# Modified 2026-11-04: a corner where two exact edges really cross goes to the crossing (_corners_where_edges_cross).
# Modified 2026-11-04: a chord both too far off a round and turning too far along it isn't on that round (face_error, CHORD_TURN_K).
# Modified 2026-11-04: the fit check measures every far point on the true face, both ways (_far_on_own_face).
# Modified 2026-11-03 (planes): a sliver that is a chord cap of a round goes to the round whatever its direction (_chord_cap).
# Modified 2026-11-03 (planes): edges_made takes only crossings the edges really make, and an edge follows the mesh only as far as the crossing.
# Modified 2026-11-03 (planes): _join_level_planes takes the step between its levels.
# Modified 2026-11-03 (planes): an arc must turn ARC_MIN_TURN to be one; a settled tolerance stated far beyond what's measured is measured (_overstated_tolerances).
# Modified 2026-11-03 (planes): an edge on a plane takes its measured distance from it as its tolerance (_plane_edges_measured).
# Modified 2026-11-02 (planes): a
# fold of the mesh inside a plane's region joins it (_absorb_folded_islands).
# Modified 2026-11-02 (planes): _broken_when_saved lines the faces read back up with those written
# when the reader has cut some in pieces (_read_back_pieces), instead of giving up the whole check.
# Modified 2026-11-02 (planes): a plane a designer drew keeps to the triangles on it (Plane.on_design,
# Segmenter._process_seed, face_error), and a mesh mostly of exactly flat facets is not taken for a
# scan (Segmenter.__init__, SCAN_COPLANAR).
# Modified 2026-11-02 (edits): a triangle at a border whose corners lie on the surface next door, and not on its own, goes
# there (Segmenter._triangles_to_their_surfaces), so a border runs where the surfaces really meet.
# Modified 2026-11-02 (edits): before the faces are built, surfaces are settled with each other (regular.py), and borders
# between surfaces touching smoothly are the exact line or circle of contact (_exact_chain_edge, _meet).
# Modified 2026-11-02 (edits): flat strips whose corners lie on a curved surface beside them are that surface
# (Segmenter._chords_to_surfaces), and a chamfer's narrow band takes the axis of the round it runs beside
# (Segmenter._bands_on_axes): strips and tilted cylinders there meet their neighbours nearly tangent, and Fusion can't edit that.
# Modified 2026-11-02 (edits): corners and edges come from the surfaces, not the mesh (solve_corners, _exact_chain_edge):
# each corner is where its surfaces meet and each edge lies on both its faces, as in a designer's file. Fusion can only
# edit a face when its corners and edges really lie on it.
# Modified 2026-11-02: regions pinched only once the slivers have moved are split too (build() in build_cad_shell);
# the cut between two pieces of a plane is a line only when it passes clear of the rest (_plane_cut_edges),
# and a pinched plane is cut the shortest way from one outline to the other (_shortest_cut).
# Modified 2026-11-02: freeform cells too thin for their surface to reach their outline join a neighbour (_slivers_to_neighbours).
# Modified 2026-10-25 for the stl2curves merged engine (mms2c/merged.py): surfaces
# stl2curves found come in "locked" (_locked): borders move, the surface is never refitted,
# simplified, swallowed by a sweep or a fillet chain, and survives merges; faces still
# broken after build_cad_shell's rounds become flat faces of their own triangles.
# Modified 2026-10-26: after its rounds build_cad_shell checks the faces once more written
# as one shell, as the file holds them, and faces broken that way become flat faces of their
# own triangles (the rounds' check writes a loose compound, which read back clean while the
# saved file broke two faces: Rak Uni-USB); a triangle whose direction isn't trusted
# (Segmenter.unsure) must still face within the tilt noise can give it (Segmenter.cos_ok).
# Modified 2026-10-28: convert_body_cad keeps the healed shape only when it is better
# (pipeline.fix_acceptable: valid, volume of the mesh's sign and size), and turns a body
# that comes out inside out the right way round (pipeline.orient_like_mesh).
# Modified 2026-10-28 (b2): a freeform cell _make_freeform fits over weak flat faces that
# can't follow its own triangles (further off than the tolerance, or the mesh's own
# coarseness where that is larger) goes back to those flat faces, which are exact; each
# cell keeps the flat faces it was made from (Region.parts). Toolbox bottom: a wall and its
# 30-degree chamfer became one B-spline bulging 0.42 mm.
# Modified 2026-10-28 (group C): a cone face round its apex is built as a cap with a
# degenerate edge at the apex, as a sphere's cap round its pole was (plan_seams,
# _pole_cap_wire, _generator).
# Modified 2026-10-28 (group C): a cylinder, cone or torus region winding more than one
# turn round its axis is cut into one face a turn at a meridian (split_turns), with its
# seam and the corners of the cut on that meridian (Region.cut, snap_turn_cuts).
# Modified 2026-10-28 (group C): a torus face round its tube but only part way round its
# axis (a bent rod or bore) gets a seam along one of the torus's parallels
# (_plan_tube_seam, _tube_seam_wire), as a face round the axis gets one along a meridian.
# Modified 2026-10-28 (group E): _straighten_borders settles a tie between equally short
# borders by the triangles' shape (_tied_side: normal agreement, curved over flat, larger
# region, position), not by which region came first, which made the choice depend on
# region order. (The mirrored side brace's -1.15% volume has another cause, still open: the
# cut weighs border length only, not how well each surface fits the rows it moves.)
# Modified 2026-10-28 (b13): _process_seed also fits each seed on the known axes near it
# (_axis_hypotheses): the axes of stl2curves' locked surfaces and threads, and of the
# segmenter's own cylinders, cones and tori once they reach AXIS_AROUND_DEG round; only
# the profile is fitted, to the seed's own corners, so a narrow band (a chamfer, a
# countersink) no longer loses to a plane because its neighbourhood took in the faces
# beside it (Rak Front Lid through MMM alone: 204 -> 102 faces; worm_608bearing 358 -> 32).
# Off where the merged engine seeds the segmenter with stl2curves' features (Segmenter.
# known_axes): they hold those axes' surfaces already, and there it cost Solar Panel Bracket
# Large its CAD faces (one smooth area 22 -> 24 regions tipped compete the wrong way).
# Modified 2026-11-03 (demote): a tiny failed region's triangles join a neighbour only when its
# surface holds them within the tolerance (_demote), not the nearest neighbour at any distance.
# Modified 2026-10-29 (slivers): a triangle with no area to speak of goes to the region across
# its longest edge before each build (Segmenter._flat_slivers_to_long_edge): held by a short
# edge it made a zero-width spike in its face's outline, which OpenCASCADE finds crossing.
# Modified 2026-10-29 (brace-fit): _straighten_borders' minimum cut also weighs how well each
# surface fits the triangles it may move (FIT_WEIGHT), not border length only: of equally
# short borders, the one keeping triangles on the surface they fit better wins.
# Modified 2026-10-31 (Tuna's side): a flat face made from a region's own triangles that is
# still broken is built once more from truly coplanar triangles only (FACET_FLAT): grouped
# within FACET_GROUP of its plane, its corners stood as far off that plane as its edges'
# tolerance, and the check failed on the tie (Solar Arm Mount was saved faceted for two).
# Modified 2026-10-31 (Tuna's side): _heal_faces no longer keeps a repair that turns a
# neighbouring face inside out (HEAL_GROWTH): the edge it hands on can leave a periodic
# neighbour with everything but its patch (a whole torus round Bravo's motor shaft joint).
# Modified 2026-11-01 (Tuna's side): _fan_hypothesis gathers a coarse cone's fan of thin flat
# strips and fits one cone (chamfers round rounded corners); an exactly flat seed that isn't a strip
# keeps out of a curved surface its corners miss (SEED_ON_CURVE); a near tie goes to the simpler
# surface only among those the mesh's corners lie about as close to (TIE_FIT); the saved-file check
# also counts a face OpenCascade can't mesh at the preview's fineness, or a small one finer (_unmeshable,
# MESH_FINE) and is rebuilt like one (never as flat facets: Tuna, 2026-11-01).
# Modified 2026-11-01 (Tuna's side, later): _designed_run_hypothesis snaps a coarse export's strips
# to the cylinder or cone their corners lie on, even where a strip's middle stands off it; where
# such a designer's surface is found, surfaces that miss the corners don't compete, and
# _inaccurate_regions leaves it alone (design intent over the triangles: Tuna and Duvdivan).
# Modified 2026-11-01 (Tuna's side, chute): the border between two flat faces is one straight
# edge only while it bends less than LINE_BEND of its length, not just within 5 tolerances: a
# 0.83 mm L-shaped border 0.28 mm off its chord became a line, its corners took 0.62 mm
# tolerances reaching past the wall's other edge 0.2 mm away, and neither side wall could be
# meshed (both were refitted as freeform, 0.37 mm off: the P1S chute's failure).
# Modified 2026-11-01 (Tuna's side, toolbox top): _make_freeform makes a freeform cell of loose flat
# pieces only when they turn through FACET_TURNS directions; a wall and the pieces of its 30 deg
# chamfer (two planes, one crease) had become one B-spline strip 0.35 mm off the mesh.
# Modified 2026-11-02: broad handlers call faults.caught; _unmeshable meshes through meshguard, a face
# the mesher can't finish at the fineness asked for counting as unmeshable.
import collections
import copy
from types import SimpleNamespace

import numpy as np
from . import faults
from . import meshguard

from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.BRepLib import BRepLib
from OCP.Geom import (
    Geom_ConicalSurface,
    Geom_CylindricalSurface,
    Geom_SphericalSurface,
    Geom_ToroidalSurface,
)
from OCP.GeomAbs import GeomAbs_C2
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.ShapeFix import ShapeFix_Edge, ShapeFix_Wire
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Edge, TopoDS_Face, TopoDS_Vertex, TopoDS_Wire
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as _ShapeMap
from OCP.Geom2d import Geom2d_Line
from OCP.gp import gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Dir2d, gp_Pnt2d
from OCP.collections import Array1_gp_Pnt

from .brep import MIN_TOL, BRepAssembler, _pnt, make_compound
from .fitting import CURVED, fit_circle_2d, Cone, Cylinder, Plane, Sphere, Torus, _OnAxis, on_axis, perp_basis, unit
from .meshio import face_normals_areas
from .planar import planar_regions
from .regular import contact, contact_rows, regularise
from .report import NULL
from .topology import RegionGraph

# Surfaces on known axes (Segmenter._axis_hypotheses)
BORDER_SURE = 0.75      # share of a plane's corners that must lie on it for it to give up triangles at its border
SMALL_BESIDE = 0.1      # share of a surface's area: a neighbour this small gives way when their borders cross
FACET_DWARFS = 4.0      # a flat piece this many times the typical one of its cluster isn't a coarse curve's facet
STRAIGHT_DEG = (0.01, 10.0)     # degrees: a cylinder this far from square to a flat face beside it may be tilted by mistake
STRAIGHT_BETTER = 3.0   # times closer a straight piece's corners must lie than the tilted cylinder's
STRAIGHT_SHARE = 0.7    # share of a straight piece's corners that must lie on its circle, the rest within the tolerance
LEVEL_STEP = 0.1        # tolerances: neighbouring parallel planes this close in level are one
FOLD_DEG = 1.0          # degrees: a border turning back on itself within this has folded
CHORD_KINDS = ("plane", "cylinder", "cone", "sphere", "torus")     # surfaces that can't bulge between a triangle's corners
LAST_ROUNDS = 2         # times the last resort's flat faces may take in a neighbour they broke
FLAT_FEW = 24           # triangles: a stray freeform surface of more than this is cut up and fitted again before any flat faces
EVEN_STEPS = 0.5        # the narrowest of a round's strips, past the mesh's ordinary sag, at least this share of the widest
CHORD_TURN_K = 2.0      # a chord past the mesh's ordinary sag must turn no more than this many times its ordinary chord
TRUE_FACE_POINTS = 4000 # far points of a region measured against its true face in the fit check; past this, an even random share stands for the rest
SEAM_SHALLOW = 0.03     # a loop near a seam must stand off it at least this share of how far along the seam it is (about 1.7 degrees)
SEAM_LOOKS = 720        # meridians tried for a seam when no corner will do
SEAM_END = 0.02         # share of a seam's length at each end where the loops it joins don't count against it
CROSS_NEAR = 1e-3       # share of an edge's length from a corner where the look for a crossing starts
CROSS_POINTS = 32       # points along each edge in that look, packed towards the corner
CORNER_ROUNDS = 2       # times the corners are looked at again after some moved to where their edges cross
TIP_HEIGHT = 0.5        # tolerances: a needle this low at the tip of a flat face beside a freeform one goes to it
ON_DESIGN = 0.1         # tolerances: a mesh's corners this close to a surface lie on it (a CAD export's vertices sit on what was drawn)
BAND_TRIS = 400         # triangles: most a narrow band of a chamfer can hold to be looked at for an axis
EXACT_EDGES = True      # corners and edges are worked out from the surfaces (see solve_corners); False: from the mesh's points
CORNER_REACH = 4.0      # tolerances: how far a corner or border point may move to reach its surfaces' meeting point
MEET_RCOND = (0.02, 0.1, 0.35)  # directions the surfaces pin down less than this share of the best are left alone, tried in turn (about 1, 6, 20 degrees)
MEET_CLEAR = np.cos(np.radians(10.0)) ** 2   # two surfaces whose normals' cosine squared is under this meet clearly
EDGE_FIT = 1e-4         # mm: an edge's curve lies this close to both its surfaces, or is refined until it does
POOL_DEG = 15.0         # degrees: two pieces of one surface meet under this at their border, and so do their facets; a drawn crossing
                        # is steeper (71-88% of our edges at 0.5-15 degrees read back loose; the designers' files keep 0.3% there)
POOL_EXACT = 0.001      # mm: a piece whose vertices all lie this close to its surface (or within POOL_NOISE times the mesh's own
                        # scatter) is a drawn face, or one facet, and is never refitted into a neighbour (Tuna's side's rule, at the
                        # judge's own measure of a drawn plane in a mesh; as a share of the tolerance, 1%, it missed allen's flats,
                        # whose mesh is rounded to about 0.001, and 6 went to cylinders)
POOL_NOISE = 4.0        # times the mesh's scatter (fidelity.mesh_noise): a drawn face's vertices lie on it to within that
POOL_EXACT_SHARE = 90   # percentile of a piece's interior vertices that must lie that close: a drawn face's piece carries a ring
                        # of its neighbours' triangles at its border (test13: a drawn cylinder's median 0.0003 mm, one in eight over 0.005)
POOL_INTERIOR = 4       # interior vertices a piece needs for its exactness to be judged on them alone
POOL_LEAVE = 3          # rounds in which the members a cell doesn't hold leave the cluster and the rest are fitted again
POOL_SPINDLE = 1.0      # a joined torus with r at R or more is refused: a spindle can't be saved (stepio._readable_everywhere);
                        # fat ones below that are the limit rule's to judge (designers draw blends up to r/R 0.92, the hopper's
                        # funnel; at 0.9 the bound refused 366 joins on it and 212 on tc that the limit rule refuses anyway:
                        # both files identical at 0.9 and 1.0, 2026-11-07)
                        # and a true spindle can't be saved (stepio._readable_everywhere); tc: cylinder tiles with a few plane
                        # triangles fitted as tori with r 2 to 11 times R
POOL_BEND = 2.0         # degrees: cylinders whose axes are within this are one cylinder or a draft cone, never a bend (torus)
POOL_SIGMA = 3.0        # a torus or cone joined from cylinder tiles keeps each tile's radius (as its tube, or its radius where the
                        # tile is) within this many times the tile's own radius uncertainty (sigma_r of its least-squares fit: a
                        # tile spanning a small arc knows its radius poorly, a wide one well), or within the tolerance
POOL_LIMIT_HOLDS = True     # that rule refuses the join (measured on tc 2026-11-06: of seven cone and torus joins over cylinder
                            # tiles that the other rules passed, every one missed a tile's radius by 3 to 40 times sigma_r, 0.03
                            # to 3 mm, while a 1 mm fillet's bend and a draft's tiles keep theirs within sigma_r)
POOL_FEWER = 3          # a cluster of tiles becomes freeform cells only as this many times fewer faces: two tiles as one cell
                        # (fan-case: two cylinder tiles of 20 and 8 triangles) changed which segmentation won an unsure area
POOL_WORSE = 1.5        # a joined surface may stand off a piece's vertices this many times further than the piece's own did, no
                        # more: a join within the tolerance that is seven times worse than the pieces loses closeness (fan-case)
POOL_PASSES = 4         # most rounds of pairwise joins in one rebuild: each joins what it can, then looks again
POOL_SAMPLE = 7         # border points at which two surfaces' angle is measured
POOL_CREASE = (1.5, 1.0)    # a border whose facets bend more than this share of the pieces' own facets' bending, plus this many
                        # degrees, is a crease between two drawn faces, not a seam of tiles (the freeform union only; Tuna's side's
                        # guard, as a relative test: facets of a coarse curve bend by 10 degrees inside a piece and at its borders alike)
EDGE_TANGENT_DEG = 5.0  # degrees: two faces meeting closer to flat than this have no sharp meeting line to compute
EDGE_LINE_AT = (0.5, 0.25, 0.75)    # shares along a chord where a border is tested for being that straight line
EDGE_START = 12         # points of the mesh's a curved edge starts from; more are added where it needs them
EDGE_POINTS = 200       # most points an edge's curve is laid through
CHAIN_START = 400       # points of a longer border its curve starts from, more being added where it strays: this sets how long the fit takes, not whether it works
FACET_TURNS = 3        # distinct directions a cluster of loose flat pieces must turn through to be a coarse curve's facets
FACET_SAME_DEG = 1.0   # degrees: two flat pieces this close in direction are one plane's (for FACET_TURNS)
LINE_BEND = 0.05       # share of its length a border between two flat faces may stand off its chord to become one line
AXIS_AROUND_DEG = 90.0  # degrees: a fitted cylinder, cone or torus lends its axis once it reaches this far round
AXIS_REACH = 0.05       # share of the model's diagonal, past twice the surface's radius, an axis reaches
AXIS_NEAREST = 4        # the nearest so many axes in reach are tried for each seed
AXIS_TIE = 0.9          # a surface on a known axis wins over a free fit covering up to 1 / this as much
AXIS_SAME_DEG = 0.5     # degrees (and 4 tolerances apart at most): two axes are one

# Flat faces made from a region's own triangles (_flat_facets)
FACET_GROUP = 0.1       # tolerances: triangles share one flat face when this close to its plane ...
FACET_FLAT = 1e-3       # ... and only this close when that face broke (a second try, see build_cad_shell)

# Repairing the last broken faces (_heal_faces)
HEAL_GROWTH = 3.0       # a face this many times the area of its own triangles after a neighbour's repair ...
HEAL_GROWTH_MM2 = 1.0   # ... and this much more (mm^2) was turned inside out by it
HEAL_TRIES = 3          # times the repair is run again without the broken faces next to such a face
MESH_FINE = (0.0015,)   # share of the part's diagonal every saved face must mesh at: the app's preview; see _unmeshable
MESH_FINE_SMALL = 0.0003  # ... and a small face this finer one too: on a face of about 1 mm a failure there is
                          # edges off its surface, shown crumpled (toolbox Body474); held to it, sound big faces
                          # failed too (a P1S drawer's 987 mm2 cylinder: 56 -> 102 faces)
SMALL_FACE = 0.001        # share of the part's diagonal squared below which a face is small
SEED_ON_CURVE = 0.25    # share of the tolerance an exactly flat seed's corners may stand off a curved surface taking it
CUSP_ROUNDS = 6         # cusps on a shell whose exact arcs may be fitted instead, by rebuilding
CUSP_NEAR = 1e-3        # mm: an edge read back and a border are the same when their ends are this close
ARC_MIN_TURN = 0.01     # radians: an arc must turn this far to be built as one
OVERSTATED = 20.0       # tolerances: an edge stated beyond this after settling is measured again
PLANE_EDGE_SAMPLES = 32 # points along an edge at which its distance from a plane it bounds is measured
THIN_FACE = 1.0         # tolerances: a region narrower than this all along is a sliver, not a face
FOLD_TRIS = 64          # triangles at most in a fold of the mesh taken back into the plane round it
ON_PLANE = 0.002        # mm: a triangle whose corners are all this near a designed plane lies on it ...
ON_PLANE_SIZE = 2e-5    # ... or this share of the mesh's size, for a big part's float32 noise
PLANE_SEED_SHARE = 0.75 # an exactly flat seed holding this share of what its plane would grow over at the tolerance is that plane
SCAN_COPLANAR = 0.25    # share of a mesh's area in exactly coplanar groups of triangles from which it can't be a scan
TIE_FIT = 0.1           # share of the tolerance the mesh's corners may stand further off a simpler surface (on top of twice) for it to win a near tie


# ----------------------------------------------------------------------------
# Segmentation
# ----------------------------------------------------------------------------


def _locked(prim):
    """A surface stl2curves found (merged engine): kept exactly as it came."""
    return getattr(prim, "locked", False)


class Region:
    __slots__ = ("prim", "faces", "sense", "weak", "facet", "parts", "cut", "pooled")

    def __init__(self, prim, faces, sense, weak=False):
        self.prim = prim
        self.faces = faces
        self.sense = sense
        self.weak = weak
        self.facet = False
        # a freeform cell made from weak flat faces: those regions (see _make_freeform); a pooled surface: the
        # pieces it was made from, which it goes back to if its face doesn't build (_pool_pieces, _unpool)
        self.parts = None
        self.pooled = False
        # one turn of a surface winding several (split_turns): the direction from
        # its axis of the meridian it was cut at, where its seam goes
        self.cut = None

    @property
    def kind(self):
        return self.prim.kind


def _round_radius(prim, p):
    """The tightest circle of a round through p: its turn about the axis or, on a torus, its own."""
    if prim.kind in ("cylinder", "sphere"):
        return float(prim.r)
    if prim.kind == "cone":
        rel = p - prim.apex
    elif prim.kind == "torus":
        rel = p - prim.c
    else:
        return None
    rho = float(np.linalg.norm(rel - (rel @ prim.d) * prim.d))
    return min(rho, float(prim.r)) if prim.kind == "torus" else rho


class Segmenter:
    def __init__(self, V, F, topo, tol, report=NULL, normal_deg=25.0, smooth_deg=50.0, extent_deg=50.0):
        self.V = V
        self.F = F
        self.topo = topo
        self.tol = tol
        self.report = report
        self.fn, self.fa = face_normals_areas(V, F)
        nf = len(F)
        t = topo.twin.reshape(nf, 3)
        self.nb = np.where(t >= 0, t // 3, -1)
        self.ext = float(np.linalg.norm(V.max(0) - V.min(0)))
        self.cos_n = np.cos(np.radians(normal_deg))
        self.cos_smooth = np.cos(np.radians(smooth_deg))
        self.cos_extent = np.cos(np.radians(extent_deg))
        self.tiny = self.fa < 1e-10 * self.ext ** 2
        # needle triangles: thinner than the tolerance, so their normal says
        # little about the surface they belong to
        T = V[F]
        lmax = np.linalg.norm(T - np.roll(T, -1, axis=1), axis=2).max(1)
        self.needle = 2.0 * self.fa < 0.5 * tol * np.maximum(lmax, 1e-30)
        self.deflection, defl_hi, turn = self._estimate_deflection()
        # surfaces must also follow the mesh between its vertices, within the
        # mesh's own tessellation coarseness
        self.tol_mid = max(3.0 * tol, 5.0 * self.deflection, 2.0 * defl_hi)
        # An exporter limits each chord by its sag, by how far the normal turns along it, or both, so a chord of a
        # real round keeps one of them ordinary for the mesh: a big radius has a big sag and an ordinary turn, a tiny
        # fillet a big turn and an ordinary sag. Past the ordinary sag, a chord must have an ordinary turn. The high
        # sag above comes from very few edges where it stands far above the typical one (on 30 of 449 meshes, over 10
        # times; often 8 to 16 chords turning 31 degrees on meshes whose chords turn 5 to 10), and on xlcd-cover it let
        # a designed octagon pass for a cylinder, 0.45 mm off its sides, at 115 times the typical sag.
        self.tol_sag = max(3.0 * tol, 5.0 * self.deflection)
        self.cos_chord = np.cos(min(CHORD_TURN_K * turn, np.pi)) if turn > 0 else -1.0
        # A scan's noise tilts thin triangles: one 0.03 mm high on a mesh with
        # 0.01 mm of noise points anywhere within about 40 degrees, so judged
        # by its direction it fits no surface, and a scanned flat face fell
        # apart into thousands of pieces (a plate with five holes: 38,830
        # faces). Measured, noise tilts a triangle of height h by about
        # atan(3.4 noise / h); where that exceeds the angle surfaces are
        # matched by, only the triangle's position counts. On such a mesh
        # flat faces are also fitted again as they grow (a small seed's own
        # plane is tilted by the noise). Clean meshes have no noise: none of
        # this applies to them.
        from .fidelity import mesh_noise

        self.noise = mesh_noise(V, F)
        h = 2.0 * self.fa / np.maximum(lmax, 1e-30)
        noisy = h < 3.4 * self.noise / np.tan(np.radians(normal_deg))
        self.scan = self.noise > 0.5 * tol or self.fa[noisy].sum() > 0.005 * max(self.fa.sum(), 1e-30)
        # A scanner's noise leaves no two triangles exactly in one plane (4 scans: at most 7% of
        # their area), and a CAD export's flat faces are nearly all of it (129 parts: at least 46%,
        # most over 80%). Its thin triangles aren't noise: taken for a scan by its needles, Rak
        # N-USB let a 2-triangle V-groove along a wall's foot, facing away from it, into the wall,
        # and the wall's outline folded over itself along 32 mm (2026-11-02). Measured here, on the
        # mesh, as CLAUDE.md asks of a mesh's kind.
        if self.scan:
            grp, _ = planar_regions(V, F, topo, self.fn, self.fa, max(min(tol * 0.05, 2e-6 * self.ext), 1e-9))
            self.scan = float(self.fa[np.bincount(grp)[grp] >= 2].sum()) < SCAN_COPLANAR * float(self.fa.sum())
        # triangles whose direction says nothing: tiny ones, and on a scan
        # the ones thin enough for the noise to tilt
        self.unsure = self.tiny | noisy if self.scan else self.tiny
        # the direction each triangle must agree with a surface's within: a thin
        # one on a scan can be tilted by noise as measured above, so it may
        # disagree by that much more, but not by more (tiny ones by anything).
        # Taken as no limit at all, the thin triangles of a blind hole's floor,
        # every corner on the hole's rim, joined its wall on a CAD mesh taken for
        # a scan by its slivers (Rak Uni-USB: 34 of them, at 90 degrees to it),
        # and the wall's border cut 51 degrees of the rim straight across, 0.17
        # mm inside it
        tilt = np.arctan(3.4 * self.noise / np.maximum(h, 1e-30))
        self.cos_ok = np.where(self.tiny, -1.0, np.where(
            self.unsure, np.cos(np.minimum(np.pi, np.radians(normal_deg) + tilt)), self.cos_n))
        self.refit_planes = self.scan
        self.label = -np.ones(nf, dtype=np.int64)
        self._stamp = np.zeros(nf, dtype=np.int64)
        self._stamp_id = 0
        self.regions = []
        self._axes, self._axes_n = [], -1

    # --- helpers --------------------------------------------------------
    def _estimate_deflection(self):
        """The chordal deflection the mesh was tessellated with (see
        fidelity.chord_stats): (typical, high, the turn of an ordinary chord)."""
        from .fidelity import chord_stats

        twin = self.topo.twin
        h = np.nonzero(twin >= 0)[0]
        t = twin[h]
        m = h < t
        h, t = h[m], t[m]
        L = np.linalg.norm(self.V[self.topo.he_to[h]] - self.V[self.topo.he_from[h]], axis=1)
        return chord_stats(self.fn, self.fa, h // 3, t // 3, L, tiny=self.tiny)

    def _new_stamp(self):
        self._stamp_id += 1
        return self._stamp_id

    def face_error(self, prim, sense, faces):
        """(max vertex distance, cosine to primitive normal) for faces.

        A triangle whose corners lie on the surface can still be a poor match
        if it spans a large piece of it (e.g. a chamfer whose corners happen
        to lie on a sphere). Such triangles get cosine -1 (rejected): the
        surface normals at its corners must agree within ``extent_deg``.
        """
        P = self.V[self.F[faces]]
        flat = P.reshape(-1, 3)
        d = prim.distance(flat).reshape(-1, 3).max(1)
        # (a designed plane takes only what lies on it: see _process_seed)
        near = getattr(prim, "on_design", None)
        if near is not None:
            d = np.where(d <= near, d, np.inf)
        nrm = prim.normal(P.mean(1)) * sense
        cs = (self.fn[faces] * nrm).sum(1)
        if prim.kind != "plane":
            M = np.concatenate([(P[:, 0] + P[:, 1]) / 2, (P[:, 1] + P[:, 2]) / 2, (P[:, 2] + P[:, 0]) / 2])
            dm = prim.distance(M).reshape(3, -1)
            nv = prim.normal(flat).reshape(-1, 3, 3)
            c01 = (nv[:, 0] * nv[:, 1]).sum(1)
            c12 = (nv[:, 1] * nv[:, 2]).sum(1)
            c20 = (nv[:, 2] * nv[:, 0]).sum(1)
            # a mid-edge miss beyond the tessellation coarseness disqualifies, and so does one
            # past the ordinary sag on a chord that also turns further than an ordinary one
            odd = (dm > self.tol_sag) & (np.stack([c01, c12, c20]) < self.cos_chord)
            d = np.where((dm.max(0) <= self.tol_mid) & ~odd.any(0), d, np.inf)
            span_ok = np.minimum(np.minimum(c01, c12), c20) >= self.cos_extent
            cs = np.where(span_ok, cs, -1.0)
        return d, cs

    def midpoint_dev(self, prim, faces):
        """Area weighted mean distance of triangle edge midpoints and
        centroids: how well the surface matches the mesh *between* its
        vertices (a torus can hit both ends of a long straight strip)."""
        T = self.V[self.F[faces]]
        M = np.concatenate([(T[:, 0] + T[:, 1]) / 2, (T[:, 1] + T[:, 2]) / 2, (T[:, 2] + T[:, 0]) / 2, T.mean(1)])
        d = prim.distance(M).reshape(4, -1).max(0)
        w = self.fa[faces]
        return float((d * w).sum() / max(w.sum(), 1e-30))

    def accept(self, prim, sense, faces, tol=None):
        tol = self.tol if tol is None else tol
        d, cs = self.face_error(prim, sense, faces)
        return (d <= tol) & (cs >= self.cos_ok[faces])

    def region_points(self, faces, with_weights=False):
        """Vertices of faces with normals averaged over these faces only.

        With ``with_weights`` also returns per-vertex weights: the mesh area
        each vertex represents (a third of its incident face areas)."""
        Fs = self.F[faces]
        vid, inv = np.unique(Fs.reshape(-1), return_inverse=True)
        inv = inv.reshape(-1, 3)
        N = np.zeros((len(vid), 3))
        w = (self.fn[faces] * self.fa[faces, None])
        for k in range(3):
            np.add.at(N, inv[:, k], w)
        ln = np.linalg.norm(N, axis=1)
        ok = ln > 0
        N[ok] /= ln[ok, None]
        if not with_weights:
            return self.V[vid], N, ok
        W = np.zeros(len(vid))
        a3 = self.fa[faces] / 3.0
        for k in range(3):
            np.add.at(W, inv[:, k], a3)
        W = np.maximum(W, 1e-12 * max(W.max(), 1e-30))
        return self.V[vid], N, ok, W

    def sense_of(self, prim, faces):
        P = self.V[self.F[faces]].mean(1)
        s = (prim.normal(P) * self.fn[faces]).sum(1) @ self.fa[faces]
        return 1.0 if s >= 0 else -1.0

    def grow(self, prim, sense, seed, refit=True):
        stamp = self._new_stamp()
        st = self._stamp
        st[seed] = stamp
        region = [seed]
        frontier = seed
        total = len(seed)
        refit_at = max(2 * total, 24)
        label = self.label
        while frontier.size:
            cand = self.nb[frontier].reshape(-1)
            cand = cand[cand >= 0]
            if not cand.size:
                break
            cand = np.unique(cand)
            cand = cand[(label[cand] < 0) & (st[cand] != stamp)]
            if not cand.size:
                break
            ok = self.accept(prim, sense, cand)
            new = cand[ok]
            st[new] = stamp
            if new.size:
                region.append(new)
            frontier = new
            total += new.size
            if refit and (prim.kind != "plane" or self.refit_planes) and total >= refit_at:
                allf = np.concatenate(region)
                P, N, _, W = self.region_points(allf, True)
                p2 = prim.refit(P, N, W)
                if p2 is not None and not getattr(p2, "degenerate", lambda e: False)(self.ext):
                    okall = self.accept(p2, sense, allf)
                    if okall.mean() > 0.995:
                        prim = p2
                refit_at = 2 * total
        return np.concatenate(region), prim

    def neighbourhood(self, seed, min_verts=30, max_faces=1200, max_layers=8, min_layers=2, min_spread_deg=12.0):
        """Smoothly connected unassigned faces around a seed.

        Grows ring by ring until the patch is big enough to pin down a curved
        surface: at least ``min_layers`` rings, ``min_verts`` vertices and a
        normal spread of ``min_spread_deg`` (two rings of a torus or three
        strips of a cylinder are not enough to tell surface types apart).
        """
        stamp = self._new_stamp()
        st = self._stamp
        st[seed] = stamp
        out = [seed]
        frontier = seed
        total = len(seed)
        layers = 0
        nref = self.fn[seed[np.argmax(self.fa[seed])]]
        min_cos = 1.0
        cos_spread = np.cos(np.radians(min_spread_deg))
        while frontier.size and layers < max_layers and total < max_faces:
            layers += 1
            src = np.repeat(frontier, 3)
            cand = self.nb[frontier].reshape(-1)
            m = cand >= 0
            src, cand = src[m], cand[m]
            m = (self.label[cand] < 0) & (st[cand] != stamp)
            src, cand = src[m], cand[m]
            if not cand.size:
                break
            cs = (self.fn[src] * self.fn[cand]).sum(1)
            m = (cs >= self.cos_smooth) | self.unsure[cand] | self.unsure[src]
            cand = np.unique(cand[m])
            if not cand.size:
                break
            st[cand] = stamp
            out.append(cand)
            frontier = cand
            total += cand.size
            ok = ~self.unsure[cand]
            if ok.any():
                min_cos = min(min_cos, float((self.fn[cand[ok]] @ nref).min()))
            if layers >= min_layers and min_cos <= cos_spread:
                if len(np.unique(self.F[np.concatenate(out)])) >= min_verts:
                    break
        return np.concatenate(out)

    # --- main -----------------------------------------------------------
    def run(self):
        V, F, fn, fa = self.V, self.F, self.fn, self.fa
        tol = self.tol
        fine = min(tol * 0.05, 2e-6 * self.ext)
        sf_lab, sf_planes = planar_regions(V, F, self.topo, fn, fa, max(fine, 1e-9))
        self.sf_lab = sf_lab
        order_sf = np.argsort(sf_lab, kind="stable")
        counts = np.bincount(sf_lab)
        starts = np.r_[0, np.cumsum(counts)]
        sf_area = np.bincount(sf_lab, weights=fa)
        seeds = np.argsort(-sf_area, kind="stable")
        n_sf = len(seeds)
        rep = self.report
        deferred = []
        for k, s in enumerate(seeds):
            if k % 200 == 0:
                rep.progress(0.5 * k / max(1, n_sf))
            faces = order_sf[starts[s]:starts[s + 1]]
            faces = faces[self.label[faces] < 0]
            if not faces.size:
                continue
            if not self._process_seed(faces, allow_defer=True):
                deferred.append(s)
        # Seeds whose neighbourhood was ambiguous get a second chance once the
        # confident surfaces exist; whatever is left becomes small planes.
        for k, s in enumerate(deferred):
            if k % 200 == 0:
                rep.progress(0.5 + 0.1 * k / max(1, len(deferred)))
            faces = order_sf[starts[s]:starts[s + 1]]
            faces = faces[self.label[faces] < 0]
            if faces.size:
                self._process_seed(faces, allow_defer=False)
        self._refine()
        self._absorb_slivers()
        self._straight_pieces()
        self._flats_not_curves()
        self._chords_to_surfaces()
        self._bands_on_axes()
        self._smooth_borders()
        if FIND_SWEEPS and self._find_sweeps() + self._find_fillets():
            # the pieces they leave between them and their neighbours (one
            # or two triangles each) join a surface they fit, and their
            # borders are cleaned like the others' (a tooth left in one made
            # its edge fold back, and the face was invalid)
            self._absorb_slivers()
            self._smooth_borders()
        if STRAIGHTEN and self._straighten_borders():
            # (a border moved past a small region squeezes it to a sliver
            # of a few triangles; they join a surface they fit, like the
            # pieces sweeps and fillets leave)
            self._absorb_slivers()
            self._smooth_borders()
        self._triangles_to_their_surfaces()
        if POOLING and self._pool_pieces():
            # (the joined surfaces settled with their neighbours, and equal ones merged, as after any change)
            self._rebuild_regions()
        self._make_freeform()
        self._fix_apex_cones()
        if getattr(self, "pooled", 0):
            rep.log(f"{self.pooled} neighbouring piece(s) were found to lie on one surface and joined to it")
        if self.scan:
            # on a scan the later steps leave pieces that fit a neighbour
            # (a piece next to another piece had no big neighbour yet)
            for _ in range(3):
                if not self._absorb_slivers():
                    break
        return self.regions

    def _fix_apex_cones(self):
        """A cone whose region runs up to its apex without surrounding it
        (typically a long thin strip fitted by a nearly flat cone) is a
        misfit, and its boundary crosses the apex in parameter space, which
        breaks the face. Such regions become freeform cells instead."""
        regs = self.regions
        bad = []
        for ri, r in enumerate(regs):
            if r.kind != "cone":
                continue
            prim = r.prim
            P = self.V[np.unique(self.F[r.faces])]
            _, h, rad, rho = prim._parts(P)
            along = rho * np.sin(prim.alpha) + h * np.cos(prim.alpha)
            if along.min() > max(3.0 * self.tol, 0.05 * along.max()):
                continue
            # a real cone tip surrounds the apex at every distance from it
            # (near the apex any few points look like they do)
            outer = along > 0.5 * along.max()
            u, v = perp_basis(prim.d)
            ang = np.sort(np.arctan2(rad[outer] @ v, rad[outer] @ u))
            gaps = np.diff(np.r_[ang, ang[0] + 2 * np.pi])
            if len(ang) >= 3 and gaps.max() < np.radians(45.0):
                continue
            bad.append(ri)
        if not bad:
            return
        bad = set(bad)
        keep = [i for i in range(len(regs)) if i not in bad]
        new_regs = [regs[i] for i in keep]
        new_lab = self.label.copy()
        for k, i in enumerate(keep):
            new_lab[regs[i].faces] = k
        for i in sorted(bad):
            for cell in fit_freeform_cells(self, regs[i].faces):
                new_lab[cell.faces] = len(new_regs)
                new_regs.append(cell)
        self.regions = new_regs
        self.label = new_lab

    def _process_seed(self, faces, allow_defer=False):
        fa = self.fa
        seed_area = fa[faces].sum()
        P, N, _, W = self.region_points(faces, True)
        pl = Plane.fit(P, (self.fn[faces] * fa[faces, None]), W=W)
        best = None
        reg_p, pl = self.grow(pl, 1.0, faces, refit=self.refit_planes)
        # An exactly flat seed that is most of what its plane would grow over is a plane the
        # designer drew: its triangles lie on it to the file's rounding, and the rest are others'
        # that happen to lie within the tolerance. Those stay out. A wall on Rak Uni, 1,560 mm2,
        # took in 14 mm2 of a 5 degree slope beside it, 0.025 mm off (fans of triangles 50 mm long,
        # up to a fold-back on the slope's far side): the wall's outline then wound round the fold
        # the wrong way, the face was invalid, and the wall became B-spline patches. SMA-USB's
        # 2,221 mm2 wall took a shallow lune beside a hole and a corner triangle whose vertex
        # doubled its outline back along a line. A seed of a curve's facets is a small share of
        # what it grows over, and a scan's seeds a single triangle, so neither is affected.
        grown_past = reg_p.size > faces.size
        if (grown_past and not self.scan and len(faces) >= 2 and seed_area >= PLANE_SEED_SHARE * fa[reg_p].sum()
                and float(pl.distance(self.V[np.unique(self.F[faces])]).max()) <= max(ON_PLANE, ON_PLANE_SIZE * self.ext)):
            pl.on_design = max(ON_PLANE, ON_PLANE_SIZE * self.ext)
            reg_p, pl = self.grow(pl, 1.0, faces, refit=False)
        area_p = fa[reg_p].sum()
        best = (area_p, 0, pl, 1.0, reg_p)

        nbh = self.neighbourhood(faces)
        nbh_area = fa[nbh].sum()
        hyps = []
        if nbh.size > faces.size and seed_area < 0.5 * nbh_area:
            Pn, Nn, okn, Wn = self.region_points(nbh, True)
            Pn_ok, Nn_ok, Wn_ok = Pn[okn], Nn[okn], Wn[okn]
            for cls in CURVED:
                prim = self._robust_fit(cls, Pn_ok, Nn_ok, Wn_ok)
                if prim is None:
                    continue
                sense = self.sense_of(prim, nbh)
                ok = self.accept(prim, sense, nbh)
                if ok.mean() < 0.3 or not ok[: faces.size].all():
                    continue
                explains_all = False
                if ok.all():
                    dn, _ = self.face_error(prim, sense, nbh)
                    explains_all = bool(dn.max() <= 0.1 * self.tol)
                reg, prim = self.grow(prim, sense, faces)
                # must have grown beyond the seed to be credible
                if reg.size <= faces.size:
                    continue
                p2, s2 = self.simplify(prim, sense, reg)
                if p2 is not prim:
                    if p2.kind == "plane":
                        continue  # the plane hypothesis already covers this
                    prim, sense = p2, s2
                    reg, prim = self.grow(prim, sense, faces)
                    if reg.size <= faces.size:
                        continue
                hyps.append((fa[reg].sum(), prim.rank, prim, sense, reg))
                if explains_all and nbh.size >= 3 * faces.size:
                    break  # a simpler surface already explains everything
        ext_h = self._extrusion_hypothesis(faces)
        if ext_h is not None:
            hyps.append(ext_h)
        fan_h = self._fan_hypothesis(faces)
        if fan_h is not None:
            hyps.append(fan_h)
        run_h = self._designed_run_hypothesis(faces)
        if run_h is not None:
            hyps.append(run_h)
        hyps += self._axis_hypotheses(faces)
        if hyps and len(faces) >= 2 and not self._strip_like(faces):
            # an exactly flat facet of two or more triangles, not a thin strip,
            # is a plane the designer drew unless its own corners lie on the
            # curved surface: a short flat run of a chamfer between two rounded
            # corners was taken into a tilted cylinder (r 13.7 mm, 0.06 mm off)
            # that ate both corners' cone fans (Raspberry Pi case). Thin strips
            # are left alone: they can be chords of a coarse round (a toolbox
            # part's rounded edges, 0.04 mm off their best cylinder, went
            # freeform when this applied to them), and a coarse cylinder's
            # wider facets have their corners on it and pass.
            corners = self.V[np.unique(self.F[faces])]
            hyps = [h for h in hyps if float(h[2].distance(corners).max()) <= SEED_ON_CURVE * self.tol]
        if hyps:
            # Hypotheses that match the mesh much worse between vertices than
            # a competitor of similar size are dropped; then the largest area
            # wins, with simpler surface types preferred within 10%.
            amax = max(h[0] for h in hyps)
            q = [self.midpoint_dev(h[2], h[4]) for h in hyps]
            big = [i for i, h in enumerate(hyps) if h[0] >= 0.6 * amax]
            qmin = min(q[i] for i in big)
            keep = [i for i, h in enumerate(hyps) if getattr(h[2], "designed", False)
                    or not (i in big and q[i] > max(2.5 * qmin, 0.5 * self.tol))]
            hyps = [hyps[i] for i in keep]
            # where a designer's surface was found (the mesh's corners on it), surfaces that
            # miss the corners don't compete with it, however much more they cover: the
            # part is what was designed (Tuna and Duvdivan, 2026-11-01)
            if any(getattr(h[2], "designed", False) for h in hyps):
                hyps = [h for h in hyps if getattr(h[2], "designed", False)
                        or float(h[2].distance(self.V[np.unique(self.F[h[4]])]).max()) <= SEED_ON_CURVE * self.tol]
            hyps.sort(key=lambda h: -h[0])

            # the largest wins, with simpler surface types preferred within 10%
            # of its area; but only among those the mesh's corners lie about as
            # close to as the closest: a CAD export's vertices sit on the
            # surface its designer drew, and a tilted cylinder (corners up to
            # 0.02 mm off) beat the exact cone (0.0000 mm) of a chamfer's
            # rounded corner on the same triangles (Raspberry Pi case's end-wall
            # port frames). On coarse or noisy meshes they stand off alike, and
            # the simpler one still wins.
            tied = [h for h in hyps if h[0] >= 0.9 * hyps[0][0]]
            off = [float(h[2].distance(self.V[np.unique(self.F[h[4]])]).max()) for h in tied]
            close = [h for h, d in zip(tied, off) if d <= 2.0 * min(off) + TIE_FIT * self.tol]
            cur = min(close, key=lambda h: (h[1], -h[0]))
            # a surface on a known axis wins a near tie: its axis is another
            # surface's, found on more of the mesh than a seed's neighbourhood
            known = [h for h in hyps if isinstance(h[2], _OnAxis)]
            if known and not isinstance(cur[2], _OnAxis):
                h = max(known, key=lambda h: h[0])
                if h[0] >= AXIS_TIE * cur[0]:
                    cur = h
            if cur[0] > 1.10 * best[0]:
                best = cur
        _, _, prim, sense, reg = best
        # (a designed plane that kept to its seed isn't a lone facet)
        weak = prim.kind == "plane" and reg.size == faces.size and getattr(prim, "on_design", None) is None
        if weak and allow_defer and nbh.size > faces.size:
            return False
        rid = len(self.regions)
        self.regions.append(Region(prim, reg, sense, weak))
        self.label[reg] = rid
        return True

    def _known_axes(self):
        """[(point, direction, radius)]: the axes surfaces on them have shown so
        far: stl2curves' locked cylinders, cones, tori and threads, and the
        segmenter's own once they reach AXIS_AROUND_DEG round (over a narrow
        arc a fitted axis can be far off). Gathered again only when the
        regions change."""
        if self._axes_n == len(self.regions):
            return self._axes
        out = []
        for r in self.regions:
            p = r.prim
            if p.kind == "cylinder":
                a, d, size = p.a, p.d, p.r
            elif p.kind == "cone":
                a, d, size = p.apex, p.d, None
            elif p.kind == "torus":
                a, d, size = p.c, p.d, p.R + p.r
            elif _locked(p) and getattr(getattr(p, "model", None), "kind", None) == "thread":
                a, d, size = p.model.a, p.model.d, None
            else:
                continue
            a, d = np.asarray(a, float), unit(np.asarray(d, float))
            rel = self.V[np.unique(self.F[r.faces])] - a
            rad = rel - np.outer(rel @ d, d)
            if size is None:
                size = float(np.linalg.norm(rad, axis=1).max())
            if not _locked(p):
                u, v = perp_basis(d)
                ang = np.sort(np.arctan2(rad @ v, rad @ u))
                gap = np.diff(np.r_[ang, ang[0] + 2 * np.pi]).max() if len(ang) > 1 else 2 * np.pi
                if 360.0 - np.degrees(gap) < AXIS_AROUND_DEG:
                    continue
            a = a - (a @ d) * d
            for k, (b, e, s) in enumerate(out):
                if abs(d @ e) > np.cos(np.radians(AXIS_SAME_DEG)) and np.linalg.norm(np.cross(a - b, e)) < 4 * self.tol:
                    out[k] = (b, e, max(s, size))
                    break
            else:
                out.append((a, d, size))
        self._axes, self._axes_n = out, len(self.regions)
        return out

    def _axis_hypotheses(self, faces):
        """Hypotheses like _process_seed's for surfaces about the known axes
        near the seed: a cylinder, cone, torus or sphere with only its profile
        fitted, to the seed's own corners (a chamfer's band: the free fit to
        its neighbourhood took in the faces beside it, and a plane won; and a
        band fitted with its neighbours on the axis took in a flat ring round
        it), accepted on the whole seed, and grown by the usual test with
        refits that keep the axis."""
        if not getattr(self, "known_axes", True):
            return []
        axes = self._known_axes()
        if not axes:
            return []
        c = self.V[self.F[faces]].mean((0, 1))
        near = []
        for a, d, size in axes:
            dist = float(np.linalg.norm(np.cross(c - a, d)))
            if dist <= 2 * size + AXIS_REACH * self.ext:
                near.append((dist, a, d))
        near.sort(key=lambda x: x[0])
        P, _, _, W = self.region_points(faces, True)
        out = []
        for _, a, d in near[:AXIS_NEAREST]:
            for kind in ("cylinder", "cone", "torus", "sphere"):
                p = on_axis(kind, a, d, P, W)
                if p is None or p.degenerate(self.ext):
                    continue
                s = self.sense_of(p, faces)
                if not self.accept(p, s, faces).all():
                    continue
                reg, p = self.grow(p, s, faces)
                if reg.size <= faces.size:
                    continue
                out.append((self.fa[reg].sum(), p.rank, p, s, reg))
        return out

    def _extrusion_hypothesis(self, faces, min_aspect=3.0, min_facets=3):
        """A cylinder for coarse prism-like facets (OpenSCAD cylinders and
        grooves): long, thin flat strips side by side along one direction.
        Their few vertices sit only at the strips' ends, so the usual
        neighbourhood has to grow far (into a groove's sloped ends, say)
        before it holds enough points, and the fit gets spoiled. Here the
        strips are gathered across the edges that run along their length,
        and a cylinder along that direction is fitted to them alone.
        Returns a hypothesis tuple like _process_seed's, or None."""
        V, F, fa, fn = self.V, self.F, self.fa, self.fn
        topo = self.topo
        # the direction of the seed's longest outline edge (the edges inside
        # it, like a quad's diagonal, don't count)
        he = (3 * faces[:, None] + np.arange(3)[None]).reshape(-1)
        tw = topo.twin[he]
        inside = np.zeros(len(F), dtype=bool)
        inside[faces] = True
        outline = he[(tw < 0) | ~inside[np.maximum(tw, 0) // 3]]
        if not outline.size:
            return None
        E = V[topo.he_to[outline]] - V[topo.he_from[outline]]
        L = np.linalg.norm(E, axis=1)
        k = int(np.argmax(L))
        lmax = float(L[k])
        if lmax <= 0:
            return None
        d = E[k] / lmax
        width = float(fa[faces].sum()) / lmax
        if lmax < min_aspect * max(width, 1e-12):
            return None
        sin_par = np.sin(np.radians(1.0))
        sin_perp = np.sin(np.radians(2.0))
        if np.abs(fn[faces] @ d).max() > sin_perp:
            return None
        stamp = self._new_stamp()
        st = self._stamp
        st[faces] = stamp
        strip = [faces]
        frontier = faces
        total = len(faces)
        while frontier.size and total < 4000:
            he = (3 * frontier[:, None] + np.arange(3)[None]).reshape(-1)
            tw = topo.twin[he]
            ok = tw >= 0
            he, tw = he[ok], tw[ok]
            g = tw // 3
            a, b = V[topo.he_from[he]], V[topo.he_to[he]]
            e = b - a
            el = np.maximum(np.linalg.norm(e, axis=1), 1e-30)
            parallel = np.linalg.norm(np.cross(e / el[:, None], d), axis=1) <= sin_par
            coplanar = (fn[he // 3] * fn[g]).sum(1) >= np.cos(np.radians(0.5))
            m = (parallel | coplanar) & (self.label[g] < 0) & (st[g] != stamp) & (np.abs(fn[g] @ d) <= sin_perp)
            new = np.unique(g[m])
            if not new.size:
                break
            st[new] = stamp
            strip.append(new)
            frontier = new
            total += len(new)
        strip = np.concatenate(strip)
        # at least a few distinct facets around the axis
        ang = np.round(np.degrees(np.arctan2(*(fn[strip] @ np.stack(perp_basis(d), 1)).T[::-1])), 0)
        if len(np.unique(ang)) < min_facets:
            return None
        P, N, okn, W = self.region_points(strip, True)
        try:
            prim = Cylinder.fit(P[okn], N[okn], ext=self.ext, W=W[okn], d=d)
        except Exception as fault:
            faults.caught(fault)
            return None
        if prim is None or prim.degenerate(self.ext):
            return None
        sense = self.sense_of(prim, strip)
        if not self.accept(prim, sense, strip).all():
            return None
        reg, prim = self.grow(prim, sense, faces)
        if reg.size <= faces.size:
            return None
        return (fa[reg].sum(), prim.rank, prim, sense, reg)

    def _strip_like(self, faces, min_aspect=3.0):
        """Whether a flat seed is a thin strip: its longest outline edge at least
        min_aspect times its width (area over that length), as the strips of
        _extrusion_hypothesis and _fan_hypothesis are."""
        V, F, fa, topo = self.V, self.F, self.fa, self.topo
        he = (3 * faces[:, None] + np.arange(3)[None]).reshape(-1)
        tw = topo.twin[he]
        inside = np.zeros(len(F), dtype=bool)
        inside[faces] = True
        outline = he[(tw < 0) | ~inside[np.maximum(tw, 0) // 3]]
        if not outline.size:
            return False
        lmax = float(np.linalg.norm(V[topo.he_to[outline]] - V[topo.he_from[outline]], axis=1).max())
        return lmax > 0 and lmax >= min_aspect * float(fa[faces].sum()) / lmax

    def _designed_run_hypothesis(self, faces, max_turn=30.0, max_groups=64):
        """The cylinder or cone a designer drew where a coarse export cut it into flat
        strips: the strips are chained across their long edges both ways from the seed,
        and a window of them grows one strip at a time for as long as every corner of
        the window lies on one surface (within SEED_ON_CURVE of the tolerance). The
        chain stops at a strip of another surface even when it's as thin (toolbox
        Body474: two rounded edges of r 0.495 mm, corners on them within 0.00001 mm,
        with a 0.29 mm flat between them, were gathered together, fitted nothing, and
        went freeform; the freeform edge then crumpled the corner where three rounds
        meet). The surface is kept although a coarse strip's middle stands off it by
        more than the tolerance (a 45-degree facet on a 3 mm chamfer corner: 0.23 mm):
        the designer's surface, not the triangles, is what the part is (Tuna and
        Duvdivan, 2026-11-01). Returns a hypothesis tuple like _process_seed's, or None."""
        sf = getattr(self, "sf_lab", None)
        if sf is None:
            return None
        V, F, fa, fn, topo = self.V, self.F, self.fa, self.fn, self.topo
        coplanar_cos = np.cos(np.radians(0.5))
        turn_cos = np.cos(np.radians(max_turn))
        thr = SEED_ON_CURVE * self.tol

        def group_faces(g):
            return np.nonzero(sf == g)[0]

        def long_neighbours(g):
            # groups across this group's longest outline edges (its rulings)
            fs = group_faces(g)
            if len(fs) > 200:
                return []
            he = (3 * fs[:, None] + np.arange(3)[None]).reshape(-1)
            tw = topo.twin[he]
            ok = tw >= 0
            he, tw = he[ok], tw[ok]
            other = sf[tw // 3] != g
            he, tw = he[other], tw[other]
            if not he.size:
                return []
            L = np.linalg.norm(V[topo.he_to[he]] - V[topo.he_from[he]], axis=1)
            cs = (fn[he // 3] * fn[tw // 3]).sum(1)
            keep = (L >= 0.8 * L.max()) & (cs >= turn_cos) & (cs < coplanar_cos) & (self.label[tw // 3] < 0)
            return list(dict.fromkeys(int(x) for x in sf[tw[keep] // 3]))

        g0 = int(sf[faces[0]])
        nb0 = long_neighbours(g0)
        if not 1 <= len(nb0) <= 2:
            return None
        # the chain both ways from the seed
        chain = [g0]
        for start in nb0:
            prev, cur, side = g0, start, []
            while cur is not None and cur not in chain and cur not in side and len(side) < max_groups:
                side.append(cur)
                nxt = [h for h in long_neighbours(cur) if h != prev and h not in chain and h not in side]
                prev, cur = cur, (nxt[0] if len(nxt) == 1 else None)
            chain = (side[::-1] + chain) if start == nb0[0] else (chain + side)
        k = chain.index(g0)
        if len(chain) < 3:
            return None

        def corners(gs):
            fs = np.concatenate([group_faces(g) for g in gs])
            return fs, V[np.unique(F[fs])]

        def fit(gs):
            fs, Q = corners(gs)
            P, N, okn, W = self.region_points(fs, True)
            best = None
            for cls in (Cylinder, Cone):
                try:
                    prim = cls.fit(P[okn], N[okn], ext=self.ext, W=W[okn])
                except Exception as fault:
                    faults.caught(fault)
                    continue
                if prim is None or prim.degenerate(self.ext):
                    continue
                d = float(prim.distance(Q).max())
                if d <= thr and (best is None or prim.rank < best[0].rank):
                    best = (prim, fs)
            return best

        lo, hi = k, k
        best = None
        # the smallest window of three strips round the seed that fits, then wider
        for a, b in ((k - 1, k + 1), (k, k + 2), (k - 2, k)):
            if a >= 0 and b < len(chain):
                got = fit(chain[a:b + 1])
                if got is not None:
                    lo, hi, best = a, b, got
                    break
        if best is None:
            return None
        grew = True
        while grew:
            grew = False
            for a, b in ((lo - 1, hi), (lo, hi + 1)):
                if a >= 0 and b < len(chain):
                    got = fit(chain[a:b + 1])
                    if got is not None:
                        lo, hi, best, grew = a, b, got, True
                        break
        prim, reg = best
        if len(np.unique(sf[reg])) < 3:
            return None
        if not self._even_strips(prim, [group_faces(g) for g in chain[lo:hi + 1]]):
            return None
        prim.designed = True
        sense = self.sense_of(prim, reg)
        return (fa[reg].sum(), prim.rank, prim, sense, reg)

    def _even_strips(self, prim, strips):
        """Whether flat strips side by side could be a tessellated round. An exporter steps round a surface evenly,
        so strips well past the mesh's ordinary sag have to be about one width: obj_2_02's wall, flats 6.4 mm wide with
        chamfers of 1.2 between them and every corner on one cylinder, is flats and chamfers as drawn, and as a
        cylinder it stood 0.47 mm off them with the volume 2.7% over. (Its two end strips may be cut short by what they run
        into.) A strip's width is its own, across its long direction: angles about the fitted axis say nothing, as a fit with
        its axis in the wrong place can still pass through every corner. Below the ordinary sag it can't matter, and there
        the strips aren't asked."""
        at = prim.a if prim.kind == "cylinder" else prim.apex
        width, sag = [], 0.0
        for fs in strips:
            P = self.V[np.unique(self.F[fs])]
            C = P - P.mean(axis=0)
            across = np.linalg.svd(C, full_matrices=False)[2][1]
            width.append(float(np.ptp(C @ across)))
            Q = P - at
            Q = Q - np.outer(Q @ prim.d, prim.d)
            rho = float(np.linalg.norm(Q, axis=1).max())
            sag = max(sag, width[-1] ** 2 / (8.0 * max(rho, 1e-9)))
        if sag <= self.tol_sag:
            return True
        inner = width[1:-1] if len(width) > 3 else width
        return min(inner) >= EVEN_STEPS * max(inner)

    def _fan_hypothesis(self, faces, min_aspect=3.0, min_facets=3, max_turn=30.0):
        """A cone for coarse fan-like facets (a chamfer round a rounded corner,
        a countersink): long, thin flat strips side by side, each meeting the
        next along its long edge, the edges converging on the cone's apex. As
        with _extrusion_hypothesis, the strips' few vertices sit only at their
        ends, so each strip is exactly flat and wins as a plane of its own, and
        the run of them became one freeform patch (a Raspberry Pi case's port
        frames: 38-degree chamfers, their straight runs planes and their
        rounded corners cone fans, all left as B-splines). Here the strips are
        gathered across the edges that are the longest outline edge of both,
        where the surface turns only a little, and only strips that are thin as
        a whole flat facet (at least min_aspect times longer than wide) join: a
        short flat run of the chamfer between two corners meets both fans along
        its longest side, and let in, it spoiled the cone (0.13 mm off, 42% of
        the triangles accepted; without it each corner fits exactly). A cone is
        then fitted to the strips alone. Returns a hypothesis tuple like
        _process_seed's, or None."""
        sf = getattr(self, "sf_lab", None)
        if sf is None:
            return None
        V, F, fa, fn = self.V, self.F, self.fa, self.fn
        topo = self.topo
        coplanar_cos = np.cos(np.radians(0.5))
        turn_cos = np.cos(np.radians(max_turn))
        garea = self._sf_area if getattr(self, "_sf_area", None) is not None else np.bincount(sf, weights=fa)
        self._sf_area = garea

        def ruling_len(tris):
            # each triangle's longest edge to a neighbour that isn't coplanar
            # with it (a quad's diagonal doesn't count)
            he = (3 * tris[:, None] + np.arange(3)[None])
            tw = topo.twin[he]
            g = np.maximum(tw, 0) // 3
            L = np.linalg.norm(V[topo.he_to[he]] - V[topo.he_from[he]], axis=2)
            flat = (tw >= 0) & ((fn[tris][:, None, :] * fn[g]).sum(2) >= coplanar_cos)
            return np.where(flat, 0.0, L).max(1)

        def thin(tris):
            # the triangle's whole flat facet is a strip along its ruling
            r = ruling_len(tris)
            return r * r >= min_aspect * garea[sf[tris]]

        if not thin(faces[:1])[0]:
            return None
        stamp = self._new_stamp()
        st = self._stamp
        st[faces] = stamp
        strip = [faces]
        frontier = faces
        total = len(faces)
        while frontier.size and total < 4000:
            he = (3 * frontier[:, None] + np.arange(3)[None]).reshape(-1)
            tw = topo.twin[he]
            ok = tw >= 0
            he, tw = he[ok], tw[ok]
            t, g = he // 3, tw // 3
            free = (self.label[g] < 0) & (st[g] != stamp)
            he, t, g = he[free], t[free], g[free]
            if not he.size:
                break
            cs = (fn[t] * fn[g]).sum(1)
            L = np.linalg.norm(V[topo.he_to[he]] - V[topo.he_from[he]], axis=1)
            same = sf[t] == sf[g]
            ruling = (cs >= turn_cos) & (L >= 0.8 * ruling_len(t)) & (L >= 0.8 * ruling_len(g)) & thin(g)
            new = np.unique(g[same | ruling])
            if not new.size:
                break
            st[new] = stamp
            strip.append(new)
            frontier = new
            total += len(new)
        strip = np.concatenate(strip)
        # at least a few distinct facets
        if len(np.unique(sf[strip])) < min_facets:
            return None
        P, N, okn, W = self.region_points(strip, True)
        try:
            prim = Cone.fit(P[okn], N[okn], ext=self.ext, W=W[okn])
        except Exception as fault:
            faults.caught(fault)
            return None
        if prim is None or prim.degenerate(self.ext):
            return None
        sense = self.sense_of(prim, strip)
        if not self.accept(prim, sense, strip).all():
            return None
        reg, prim = self.grow(prim, sense, faces)
        if reg.size <= faces.size:
            return None
        return (fa[reg].sum(), prim.rank, prim, sense, reg)

    SIMPLER = {
        "torus": ("plane", "cylinder", "sphere", "cone"),
        "cone": ("plane", "cylinder"),
        "sphere": ("plane",),
        "cylinder": ("plane",),
    }

    def simplify(self, prim, sense, faces):
        """Replace a surface by a simpler kind when that fits the same faces
        (a torus with a huge ring radius is really a cylinder, etc.)."""
        kinds = self.SIMPLER.get(prim.kind, ())
        if not kinds or _locked(prim):
            return prim, sense
        P, N, ok, W = self.region_points(faces, True)
        if isinstance(prim, _OnAxis):
            # (simpler on the same axis, or flat: a free fit would let the axis go)
            for k in kinds:
                if k == "plane":
                    p2, s2 = Plane.fit(P, (self.fn[faces] * self.fa[faces, None]), W=W), 1.0
                else:
                    p2 = on_axis(k, prim.ka, prim.kd, P, W)
                    if p2 is None or p2.degenerate(self.ext):
                        continue
                    s2 = self.sense_of(p2, faces)
                if self.accept(p2, s2, faces).all():
                    return p2, s2
            return prim, sense
        classes = {"plane": Plane, "cylinder": Cylinder, "sphere": Sphere, "cone": Cone}
        for k in kinds:
            cls = classes[k]
            if cls is Plane:
                p2 = Plane.fit(P, (self.fn[faces] * self.fa[faces, None]), W=W)
            else:
                p2 = self._robust_fit(cls, P[ok], N[ok], W[ok])
            if p2 is None:
                continue
            s2 = 1.0 if cls is Plane else self.sense_of(p2, faces)
            if self.accept(p2, s2, faces).all():
                return p2, s2
        return prim, sense

    def _robust_fit(self, cls, P, N, W=None, iters=2):
        """Weighted fit, then refit on inliers (trimmed least squares) so
        faces of a tangent neighbour (e.g. a fillet next to a hole) don't
        skew the result."""
        try:
            prim = cls.fit(P, N, ext=self.ext, W=W)
        except Exception as fault:
            faults.caught(fault)
            return None
        if prim is None or prim.degenerate(self.ext):
            return None
        for _ in range(iters):
            d = prim.distance(P)
            inl = d <= 2.0 * self.tol
            wsum = W.sum() if W is not None else len(P)
            winl = W[inl].sum() if W is not None else inl.sum()
            if inl.all() or inl.sum() < 8 or winl < 0.3 * wsum:
                break
            try:
                p2 = cls.fit(P[inl], N[inl], ext=self.ext, W=None if W is None else W[inl])
            except Exception as fault:
                faults.caught(fault)
                break
            if p2 is None or p2.degenerate(self.ext):
                break
            prim = p2
        return prim

    # --- refinement -----------------------------------------------------
    def _refine(self, iters=3):
        regs = self.regions
        F = self.F
        for it in range(iters):
            moved = 0
            lab = self.label
            # boundary faces: any neighbour with another label
            nbl = np.where(self.nb >= 0, lab[np.maximum(self.nb, 0)], -1)
            bmask = ((nbl != lab[:, None]) & (nbl >= 0)).any(1)
            bfaces = np.nonzero(bmask)[0]
            if not bfaces.size:
                break
            # error to own primitive
            own = np.full(len(bfaces), np.inf)
            best_lab = lab[bfaces].copy()
            for r in np.unique(lab[bfaces]):
                m = lab[bfaces] == r
                if regs[r].weak:
                    continue  # weak (single facet) regions dissolve readily
                d, cs = self.face_error(regs[r].prim, regs[r].sense, bfaces[m])
                own[m] = np.where(cs >= self.cos_ok[bfaces[m]], d, np.inf)
            best_err = own.copy()
            for j in range(3):
                cl = nbl[bfaces, j]
                for r in np.unique(cl[cl >= 0]):
                    m = (cl == r) & (cl != lab[bfaces])
                    if not m.any():
                        continue
                    reg = regs[r]
                    if reg.weak:
                        continue
                    d, cs = self.face_error(reg.prim, reg.sense, bfaces[m])
                    e = np.where(cs >= self.cos_ok[bfaces[m]], d, np.inf)
                    e = np.where(e <= self.tol, e, np.inf)
                    idx = np.nonzero(m)[0]
                    better = e < best_err[idx] - 0.05 * self.tol
                    best_err[idx[better]] = e[better]
                    best_lab[idx[better]] = r
            ch = best_lab != lab[bfaces]
            # weak regions dissolve readily into neighbours that accept them
            if ch.any():
                self.label[bfaces[ch]] = best_lab[ch]
                moved = int(ch.sum())
            if moved == 0:
                break
        self._rebuild_regions()

    def _chord_cap(self, f, prim):
        """Whether a triangle is what a chord leaves on a round: its corners lie on the surface and it
        stands no further from its longest edge than that edge's own sag on that surface. Such a
        triangle says nothing about direction (it faces along the axis, its three corners on one rim
        circle), and how far its middle is off the surface comes from the mesh's chords, not from any
        surface of its own. A real small face stands further from its long edge than a chord can."""
        P = self.V[self.F[f]]
        if float(prim.distance(P).max()) > ON_DESIGN * self.tol:
            return False
        edges = np.linalg.norm(P - np.roll(P, 1, axis=0), axis=1)
        k = int(np.argmax(edges))
        L = float(edges[k])
        if L <= 0.0:
            return False
        r = _round_radius(prim, (P[k] + P[k - 1]) / 2.0)
        if r is None or r <= 0.5 * L:
            return False
        return 2.0 * float(self.fa[f]) / L <= r - np.sqrt(r * r - 0.25 * L * L)

    def _absorb_slivers(self, max_tris=3, tol_factor=3.0):
        """Tiny regions (slivers where three surfaces meet) join the
        neighbouring surface that fits them best. Genuine tiny faces, such
        as the corner triangle of a chamfered box, fit no neighbour and stay."""
        regs = self.regions
        lab = self.label
        changed = False
        cos_relaxed = np.cos(np.radians(60.0))
        if self.scan:
            # (noise splits a scan into pieces of many small triangles)
            max_tris = max(max_tris, 50)
        areas = np.array([self.fa[r.faces].sum() for r in regs])
        big = areas[np.array([len(r.faces) > max_tris for r in regs], dtype=bool)]
        small_area = 0.02 * (np.median(big) if len(big) else areas.max())
        if self.scan:
            # on a scan most regions are small pieces, so their median says
            # nothing about what is small: hundreds of 1-3 triangle pieces
            # stayed, each punching a hole in the face around it (a scanned
            # plate's top face got 334 holes and was invalid)
            small_area = max(small_area, 1e-4 * float(self.fa.sum()))
        # regions smaller than a face can be (see _too_small_for_face) are
        # slivers too, however many triangles they have: a strip of 18
        # needle triangles (0.01 mm2) on a printed mount stuck into the
        # cylinder beside it, whose outline then ran in and straight back
        # out, crossed itself, and the face was invalid
        tiny = areas <= 25.0 * self.tol ** 2
        # (and any narrower than the tolerance all along: Rak N-USB's ring round a hole, 0.25 mm2,
        # 26 mm round and 0.02 mm wide, exactly flat, was taken in only while the mesh passed for a
        # scan. As a face of its own its two sides fit one curve, the file read back with 0.01 mm2,
        # and Fusion failed every cut through it)
        tiny |= np.array([len(r.faces) > 0 and _too_small_for_face(self, lab, r.faces, width_k=THIN_FACE) for r in regs], dtype=bool)
        few = np.array([len(r.faces) <= max_tris and (r.weak or areas[ri] <= small_area)
                        for ri, r in enumerate(regs)], dtype=bool)
        sliver = [ri for ri, r in enumerate(regs) if len(r.faces) and (few[ri] or tiny[ri])]
        # (those that are only small in area join a surface they lie on
        # within the tolerance: a finely meshed worm gear's small true faces
        # sit a few tolerances off their neighbours, and taking them in cost
        # it accuracy)
        reach = np.where(few, tol_factor, 1.0) * self.tol
        # (a few passes: a triangle inside a strip joins once its neighbour has)
        for _ in range(4):
            moved = False
            for ri in sliver:
                for f in regs[ri].faces:
                    if lab[f] != ri:
                        continue
                    best, best_e = None, np.inf
                    for g in self.nb[f]:
                        if g < 0:
                            continue
                        rj = lab[g]
                        if rj == ri or regs[rj].weak or len(regs[rj].faces) <= max_tris or tiny[rj]:
                            continue
                        d, cs = self.face_error(regs[rj].prim, regs[rj].sense, np.array([f]))
                        # (on a scan a piece this small that sits on a surface
                        # is noise, whichever way its triangles point)
                        if cs[0] < cos_relaxed and not (self.unsure[f] or self.needle[f] or self.scan or (few[ri] and self._chord_cap(f, regs[rj].prim))):
                            continue
                        if d[0] <= reach[ri] and d[0] < best_e:
                            best, best_e = rj, d[0]
                    if best is not None:
                        lab[f] = best
                        moved = changed = True
            if not moved:
                break
        if changed:
            self._rebuild_regions()
        return changed

    def _neighbours(self, r):
        """The regions next to region r, from the current labels."""
        f = np.asarray(self.regions[r].faces, dtype=np.int64)
        nb = self.nb[f].reshape(-1)
        nb = self.label[nb[nb >= 0]]
        return [int(q) for q in np.unique(nb) if q != r and q >= 0]

    def _on_design(self, prim, faces):
        """Whether triangles lie on a surface as a CAD export's do: every corner on it, the
        triangles no further off than the tolerance between corners, and facing its way."""
        faces = np.asarray(faces, dtype=np.int64)
        lim = ON_DESIGN * self.tol
        if float(prim.distance(self.V[np.unique(self.F[faces])]).max()) > lim:
            return False
        if float(self._gap(prim, faces).max()) > self.tol:
            return False
        s = self.sense_of(prim, faces)
        return bool(self.accept(prim, s, faces).all())

    def _triangles_to_their_surfaces(self, passes=8):
        """A border belongs where its two surfaces meet. A plane running on into a chamfer's cone
        also takes the cone's first strip, whose far corners stand off the plane by less than
        the tolerance (main plate: 0.005 mm, the border 0.2 mm past the line of contact), and
        the two then meet at 2 degrees at a place where they don't really meet, which no
        program can work out again. So a triangle whose corners lie on the surface next door,
        as a CAD export's lie on what was drawn, and don't on its own, goes next door."""
        lim = ON_DESIGN * self.tol
        changed = False
        for _ in range(passes):
            lab = self.label
            nbl = np.where(self.nb >= 0, lab[np.maximum(self.nb, 0)], lab[:, None])
            edge = np.nonzero((nbl != lab[:, None]).any(1) & (lab >= 0))[0]
            moved = 0
            sure = {}
            for f in edge:
                own = self.regions[lab[f]]
                if own.kind != "plane" or own.facet:
                    continue
                P = self.V[self.F[f]]
                if float(own.prim.distance(P).max()) <= lim:
                    continue
                # (only a plane most of whose own corners are on it knows what isn't its own:
                # strips of a band each fitted loosely would be shredded, main plate's chamfer into
                # 56 new pieces of 0.01 mm2)
                r = int(lab[f])
                if r not in sure:
                    C = self.V[np.unique(self.F[np.asarray(own.faces, dtype=np.int64)])]
                    sure[r] = float((own.prim.distance(C) <= lim).mean()) >= BORDER_SURE
                if not sure[r]:
                    continue
                for q in np.unique(nbl[f]):
                    if q == lab[f] or q < 0:
                        continue
                    o = self.regions[q]
                    if o.kind not in CURVED_KINDS or o.facet:
                        continue
                    if float(o.prim.distance(P).max()) <= lim and self._on_design(o.prim, np.array([f])):
                        lab[f] = q
                        moved += 1
                        break
            if not moved:
                break
            changed = True
            self._faces_from_labels()
        if changed:
            self._rebuild_regions()
        return changed

    def _faces_from_labels(self):
        """Each region's triangles again, from the labels."""
        order = np.argsort(self.label, kind="stable")
        sl = self.label[order]
        for r, reg in enumerate(self.regions):
            a, b = np.searchsorted(sl, [r, r + 1])
            reg.faces = order[a:b]
        self._drop_empty()

    def _straight_pieces(self):
        """A cylinder a little tilted from the flat face beside it, with corners off it, is straight
        cylinders standing on that face's levels: Rak Uni's standoffs, two of r 3 mm 0.07 mm apart with a
        crescent step between, passed as one of r 3.005 mm, tilted, and the crescent came out
        a face of no shape that OpenCascade's mesher drops. Taken only if each piece's corners lie on
        its own straight cylinder STRAIGHT_BETTER times closer."""
        lim = ON_DESIGN * self.tol
        changed = False
        for r in range(len(self.regions)):
            reg = self.regions[r]
            if reg.kind != "cylinder" or not len(reg.faces) or isinstance(reg.prim, _OnAxis):
                continue
            faces = np.asarray(reg.faces, dtype=np.int64)
            off = float(reg.prim.distance(self.V[np.unique(self.F[faces])]).max())
            if off <= lim:
                continue
            planes = [self.regions[q].prim for q in self._neighbours(r) if self.regions[q].kind == "plane"]
            near = [p for p in planes if STRAIGHT_DEG[0] < np.degrees(np.arccos(min(1.0, abs(float(p.n @ reg.prim.d))))) <= STRAIGHT_DEG[1]]
            if not near:
                continue
            d = near[0].n if float(near[0].n @ reg.prim.d) > 0 else -near[0].n
            levels = sorted({round(float(p.origin @ d), 6) for p in near if abs(float(p.n @ d)) > 1.0 - 1e-6})
            h = self.V[self.F[faces]].mean(1) @ d
            side = np.searchsorted(np.array(levels), h)
            pieces = []
            for s in np.unique(side):
                g = faces[side == s]
                # (a circle through the corners seen along the axis: these corners are the accurate thing)
                P = self.V[np.unique(self.F[g])]
                u, v = perp_basis(d)
                # (the rim where two such cylinders cross holds corners of the other's circle: left out)
                Q = np.c_[P @ u, P @ v]
                fc = None
                for _ in range(4):
                    if len(Q) < 5:
                        fc = None
                        break
                    fc = fit_circle_2d(Q)
                    if fc is None:
                        break
                    e = np.abs(np.linalg.norm(Q - fc[0], axis=1) - fc[1])
                    keep = e <= max(3.0 * float(np.median(e)), lim)
                    if keep.all():
                        break
                    Q = Q[keep]
                cy = None if fc is None else Cylinder(fc[0][0] * u + fc[0][1] * v, d, fc[1])
                if cy is None or cy.degenerate(self.ext) or len(Q) < STRAIGHT_SHARE * len(P) or float(cy.distance(P).max()) > self.tol:
                    pieces = None
                    break
                if _locked(reg.prim):
                    cy.locked = True
                pieces.append((cy, g))
            if not pieces:
                continue
            for cy, g in pieces:
                rid = len(self.regions)
                self.regions.append(Region(cy, g, self.sense_of(cy, g)))
                self.label[g] = rid
            reg.faces = np.zeros(0, dtype=np.int64)
            changed = True
        if changed:
            self._drop_empty()
            self._rebuild_regions()
        return changed

    def _flats_not_curves(self):
        """A curved surface that took in a few exactly flat pieces, with corners off it, is those
        flat pieces: filament guide's r 2.85 mm cylinders held most of a slanted wall and the plane
        beside it, within a loose tolerance of 0.07 mm, 0.2 mm off at their edges. A round has to
        turn through several directions (FACET_TURNS) to be one, so a coarse round's chords stay."""
        changed = False
        for r in range(len(self.regions)):
            reg = self.regions[r]
            if reg.kind not in CURVED_KINDS or _locked(reg.prim) or not len(reg.faces) or isinstance(reg.prim, _OnAxis):
                continue
            faces = np.asarray(reg.faces, dtype=np.int64)
            if float(reg.prim.distance(self.V[np.unique(self.F[faces])]).max()) <= ON_DESIGN * self.tol:
                continue
            groups = {}
            for f in faces:
                groups.setdefault(int(self.sf_lab[f]), []).append(f)
            if len(groups) >= BAND_TRIS:
                continue
            dirs = []
            for g in groups.values():
                n = (self.fn[g] * self.fa[g, None]).sum(0)
                n /= max(float(np.linalg.norm(n)), 1e-30)
                if not any(float(n @ m) >= np.cos(np.radians(FACET_SAME_DEG)) for m in dirs):
                    dirs.append(n)
            if len(dirs) >= FACET_TURNS:
                continue
            for g in groups.values():
                g = np.asarray(g, dtype=np.int64)
                P, _, _, W = self.region_points(g, True)
                n = (self.fn[g] * self.fa[g, None]).sum(0)
                pl = Plane.fit(P, n[None], W=W)
                rid = len(self.regions)
                self.regions.append(Region(pl, g, self.sense_of(pl, g)))
                self.label[g] = rid
            reg.faces = np.zeros(0, dtype=np.int64)
            changed = True
        if changed:
            self._drop_empty()
            self._rebuild_regions()
        return changed

    def _chords_to_surfaces(self):
        """A flat region all of whose corners lie on a curved surface beside it is a chord of
        that surface. Main plate's 12.6 mm wall had three strips, up to 17 mm2, left flat between
        two cylinders: flat seeds are taken biggest first, before the wall is there to claim them,
        and being more than slivers they were never asked again. Those strips met the wall at
        2 to 6 degrees, where neither corners nor edges can be worked out exactly. A scan's
        corners never lie this close, so it isn't touched."""
        changed = False
        for _ in range(16):
            moved = False
            for r, reg in enumerate(self.regions):
                if reg.kind != "plane" or _locked(reg.prim) or reg.facet or not len(reg.faces):
                    continue
                for q in self._neighbours(r):
                    o = self.regions[q]
                    if o.kind not in CURVED_KINDS or not len(o.faces):
                        continue
                    if self._on_design(o.prim, reg.faces):
                        self.label[np.asarray(reg.faces, dtype=np.int64)] = q
                        o.faces = np.r_[np.asarray(o.faces, dtype=np.int64), np.asarray(reg.faces, dtype=np.int64)]
                        reg.faces = np.zeros(0, dtype=np.int64)
                        moved = changed = True
                        break
            if not moved:
                break
        if changed:
            self._drop_empty()
            self._rebuild_regions()
        return changed

    def _drop_empty(self):
        """Removes regions left with no triangles, renumbering the labels."""
        keep = [r for r, reg in enumerate(self.regions) if len(reg.faces)]
        new = -np.ones(len(self.regions), dtype=np.int64)
        new[keep] = np.arange(len(keep))
        m = self.label >= 0
        self.label[m] = new[self.label[m]]
        self.regions = [self.regions[r] for r in keep]

    def _bands_on_axes(self):
        """A chamfer's narrow band round a rounded corner is a cone on the corner's axis, but
        its few triangles fit other things as well: a tilted cylinder (filament guide: r 2.18 and
        2.51 mm, axes 30 degrees off, where the designer has cones of 30 degrees on the fillets'
        axes) or flat strips (main plate). Each small region beside a cylinder, cone or torus is
        tried on that surface's axis, and taken when its corners lie on that surface and don't lie
        on its own as well. Flat strips must turn through several directions to count, as one
        strip can be a flat land."""
        changed = False
        for r in range(len(self.regions)):
            reg = self.regions[r]
            if _locked(reg.prim) or reg.facet or reg.kind not in ("plane", "cylinder", "cone") or not len(reg.faces):
                continue
            if len(reg.faces) > BAND_TRIS or isinstance(reg.prim, _OnAxis):
                continue
            for q in self._neighbours(r):
                o = self.regions[q]
                if o.kind not in ("cylinder", "cone", "torus") or not len(o.faces):
                    continue
                a, d = _axis_of(o.prim)
                if reg.kind != "plane":
                    e = _axis_of(reg.prim)[1]
                    if abs(float(e @ d)) > np.cos(np.radians(AXIS_SAME_DEG)):
                        continue        # (on that axis already)
                if self._take_band(r, a, d):
                    changed = True
                    break
        if changed:
            self._drop_empty()
            self._rebuild_regions()
        return changed

    def _take_band(self, r, a, d):
        """Region r, with the flat strips running on from it, as a cylinder or cone on the axis
        (a, d), if they lie on it. Returns whether it was taken."""
        reg = self.regions[r]
        faces = np.asarray(reg.faces, dtype=np.int64)
        for kind in ("cone", "cylinder"):
            members = [r]
            cur = faces
            p = None
            grew = True
            while grew:
                grew = False
                P, _, _, W = self.region_points(cur, True)
                p = on_axis(kind, a, d, P, W)
                if p is None or p.degenerate(self.ext) or not self._on_design(p, cur):
                    p = None
                    break
                for m in list(members):
                    for q in self._neighbours(m):
                        if q in members:
                            continue
                        o = self.regions[q]
                        if o.kind != "plane" or _locked(o.prim) or o.facet or len(o.faces) > BAND_TRIS or not len(o.faces):
                            continue
                        f2 = np.asarray(o.faces, dtype=np.int64)
                        if self._on_design(p, f2):
                            members.append(q)
                            cur = np.r_[cur, f2]
                            grew = True
            if p is None:
                continue
            if kind == "cone" and abs(np.pi / 2 - p.alpha) < np.radians(1.0):
                continue        # (a flat ring, nearly: a plane's)
            if reg.kind == "plane":
                dirs = np.round(np.degrees(np.arctan2(*(self.fn[cur] @ np.stack(perp_basis(d), 1)).T[::-1])) / FACET_SAME_DEG)
                if len(np.unique(dirs)) < FACET_TURNS:
                    continue
            elif float(reg.prim.distance(self.V[np.unique(self.F[faces])]).max()) <= ON_DESIGN * self.tol and reg.prim.kind == p.kind:
                continue        # (its own surface has them too: nothing says it's wrong)
            for m in members[1:]:
                self.regions[m].faces = np.zeros(0, dtype=np.int64)
            self.label[cur] = r
            reg.prim, reg.faces, reg.sense, reg.weak = p, cur, self.sense_of(p, cur), False
            return True
        return False

    def _gap(self, prim, faces):
        """How far triangles stray from a surface: the largest distance of
        their corners, edge midpoints and centre."""
        if len(faces) == 0:
            return np.zeros(0)
        T = self.V[self.F[faces]]
        P = np.concatenate([T, (T + np.roll(T, -1, axis=1)) / 2, T.mean(1, keepdims=True)], axis=1)
        return prim.distance(P.reshape(-1, 3)).reshape(len(faces), -1).max(1)

    def _smooth_borders(self, passes=6):
        """Triangles that stick out of their surface into a neighbour's (a
        "tooth": two or all three of its neighbours belong to one other
        surface) join that surface when they fit it. A CAD export's long thin
        triangles otherwise leave borders that run out and straight back,
        and the edge curves drawn along them fold over themselves, which
        makes the faces invalid. Returns whether anything changed."""
        regs, lab, nb = self.regions, self.label, self.nb
        # (swept surfaces and fillets too, which have a motion attribute:
        # they are exact, like these)
        analytic = np.array([not r.weak and (r.kind in ("plane", "cylinder", "cone", "sphere", "torus")
                                             or hasattr(r.prim, "motion"))
                             for r in regs], dtype=bool)
        changed = False
        for _ in range(passes):
            nl = np.where(nb >= 0, lab[np.maximum(nb, 0)], -1)
            other = (nl >= 0) & (nl != lab[:, None])
            cand = np.nonzero(other.sum(1) >= 2)[0]
            if not len(cand):
                break
            # the label that two or three of the neighbours share
            a, b_, c = nl[cand, 0], nl[cand, 1], nl[cand, 2]
            maj = np.where((a == b_) | (a == c), a, np.where(b_ == c, b_, -1))
            ok = (maj >= 0) & (maj != lab[cand])
            cand, maj = cand[ok], maj[ok]
            ok = analytic[maj]
            cand, maj = cand[ok], maj[ok]
            # a thin triangle along a border can fit both surfaces (it lies
            # on the line where a fillet meets a wall): it goes to the one it
            # follows more closely, so moving it can't be undone next pass
            gap_new = np.full(len(cand), np.inf)
            gap_own = np.full(len(cand), np.inf)
            for rj in np.unique(maj):
                k = np.nonzero(maj == rj)[0]
                fit = self.accept(regs[rj].prim, regs[rj].sense, cand[k])
                gap_new[k[fit]] = self._gap(regs[rj].prim, cand[k[fit]])
            for ri in np.unique(lab[cand]):
                k = np.nonzero(lab[cand] == ri)[0]
                gap_own[k] = self._gap(regs[ri].prim, cand[k])
            move = gap_new < gap_own
            if not move.any():
                break
            lab[cand[move]] = maj[move]
            changed = True
        if changed:
            self._rebuild_regions()
        return changed

    def _straighten_borders(self, rings=8):
        """Where two exact surfaces meet smoothly (a fillet running into a
        wall), the triangles along their border fit both, and which one
        each joined was close to chance: the border zigzagged along the
        mesh, and the faces came out with saw-tooth edges (on a printed
        mount, 82 of 479 edges). Triangles within ``rings`` of such a
        border that fit both surfaces may change sides, and they take the
        sides that make the border shortest (a minimum cut). Triangles that
        fit only one surface stay where they are, so no face moves further
        from the mesh. Returns whether anything changed."""
        from scipy import sparse
        from scipy.sparse.csgraph import maximum_flow

        regs, lab, nb = self.regions, self.label, self.nb
        topo = self.topo
        analytic = np.array([not r.weak and (r.kind in ("plane", "cylinder", "cone", "sphere", "torus")
                                             or hasattr(r.prim, "motion"))
                             for r in regs], dtype=bool)
        L = np.linalg.norm(self.V[topo.he_to] - self.V[topo.he_from], axis=1).reshape(-1, 3)
        unit = max(float(np.median(L)), 1e-12)
        nl = np.where(nb >= 0, lab[np.maximum(nb, 0)], -1)
        cross = (nl >= 0) & (nl != lab[:, None])
        f_, k_ = np.nonzero(cross)
        a_, b_ = lab[f_], nl[f_, k_]
        keep = (a_ < b_) & analytic[a_] & analytic[b_]
        pairs = np.unique(np.stack([a_[keep], b_[keep]], 1), axis=0) if keep.any() else np.zeros((0, 2), int)
        changed = False
        self._pieces_before = {}
        for r in np.unique(pairs):
            self._pieces_before[int(r)] = self._pieces(lab == r)
        for a, b in pairs:
            # the triangles of either surface near their border
            on = (lab == a) | (lab == b)
            front = np.unique(np.r_[f_[(a_ == a) & (b_ == b)], f_[(a_ == b) & (b_ == a)]])
            front = front[on[front]]
            seen = np.zeros(len(lab), dtype=bool)
            seen[front] = True
            for _ in range(rings):
                n = nb[front].reshape(-1)
                n = np.unique(n[n >= 0])
                n = n[on[n] & ~seen[n]]
                if not len(n):
                    break
                seen[n] = True
                front = n
            near = np.nonzero(seen)[0]
            free = near[self.accept(regs[a].prim, regs[a].sense, near)
                        & self.accept(regs[b].prim, regs[b].sense, near)]
            if len(free) < 2:
                continue
            # graph: the free triangles, plus a source (side a) and a sink
            # (side b) standing for the triangles that stay
            idx = np.full(len(lab), -1)
            idx[free] = np.arange(len(free))
            src, snk = len(free), len(free) + 1
            rows, cols, caps = [], [], []
            # how well each surface fits each free triangle: moving one to the surface it
            # fits worse costs FIT_WEIGHT x (distance / tolerance)^2 (a typical edge of
            # border weighs 100), so the border keeps triangles on their better surface
            # (both P1S braces: rows went to the surface they fit worse, -1.2% and -1.8%
            # volume, while the mirror image came out right)
            da = self.face_error(regs[a].prim, regs[a].sense, free)[0] / max(self.tol, 1e-12)
            db = self.face_error(regs[b].prim, regs[b].sense, free)[0] / max(self.tol, 1e-12)
            on_a = np.round(FIT_WEIGHT * np.square(np.clip(db, 0, 1))).astype(np.int64)   # (cut if it goes to b)
            on_b = np.round(FIT_WEIGHT * np.square(np.clip(da, 0, 1))).astype(np.int64)   # (cut if it goes to a)
            ia = np.nonzero(on_a > 0)[0]
            ib = np.nonzero(on_b > 0)[0]
            rows += [np.full(len(ia), src), ib]
            cols += [ia, np.full(len(ib), snk)]
            caps += [on_a[ia], on_b[ib]]
            for k in range(3):
                o = nb[free, k]
                w = np.maximum(1, np.round(100 * L[free, k] / unit)).astype(np.int64)
                ok = o >= 0
                inner = ok & (idx[np.maximum(o, 0)] >= 0)
                rows.append(idx[free[inner]])
                cols.append(idx[o[inner]])
                caps.append(w[inner])
                for side, node in ((a, src), (b, snk)):
                    fixed = ok & ~inner & (lab[np.maximum(o, 0)] == side)
                    i = idx[free[fixed]]
                    if node == src:
                        rows.append(np.full(len(i), src))
                        cols.append(i)
                    else:
                        rows.append(i)
                        cols.append(np.full(len(i), snk))
                    caps.append(w[fixed])
            rows, cols, caps = np.concatenate(rows), np.concatenate(cols), np.concatenate(caps)
            if not ((rows == src).any() and (cols == snk).any()):
                continue  # one surface is all band: nothing holds it in place
            n = len(free) + 2
            G = sparse.coo_matrix((caps.astype(np.int32), (rows, cols)), shape=(n, n)).tocsr()
            G.sum_duplicates()
            flow = maximum_flow(G, src, snk).flow
            # what the source still reaches through unsaturated links is
            # side a; the rest is side b
            R = (G - flow).tocsr()
            R.data[R.data < 0] = 0
            R.eliminate_zeros()
            # (a reverse link carries back what flowed forward)
            back = flow.T.tocsr()
            back.data[back.data < 0] = 0
            R = (R + back).tocsr()
            R.eliminate_zeros()
            reach = np.zeros(n, dtype=bool)
            reach[src] = True
            front_ = np.array([src])
            while len(front_):
                nxt = np.unique(R[front_].indices)
                nxt = nxt[~reach[nxt]]
                reach[nxt] = True
                front_ = nxt
            # Several borders can be equally short (two parallel rows along a
            # fillet): what the source reaches is the shortest border nearest
            # side a, which side is a only by region number, so the same part
            # mirrored got another border. Every node that can't reach the sink
            # is the one nearest side b; the triangles between the two (tied)
            # go together to the side _tied_side picks from their shape.
            RT = R.T.tocsr()
            to_snk = np.zeros(n, dtype=bool)
            to_snk[snk] = True
            front_ = np.array([snk])
            while len(front_):
                nxt = np.unique(RT[front_].indices)
                nxt = nxt[~to_snk[nxt]]
                to_snk[nxt] = True
                front_ = nxt
            tied = np.nonzero(~reach[:len(free)] & ~to_snk[:len(free)])[0]
            if len(tied) and self._tied_side(free[tied], a, b) == a:
                reach[tied] = True
            new = np.where(reach[:len(free)], a, b)
            if not (new != lab[free]).any():
                continue
            old = lab[free].copy()
            lab[free] = new
            # a cut that leaves either surface in pieces (a thin one cut
            # through) is undone: the pieces made faces that failed
            if any(self._pieces(lab == r) > self._pieces_before.get(r, 1) for r in (a, b)):
                lab[free] = old
                continue
            # a cut that squeezes either surface into a strip is undone too:
            # on a wheel rim one 3.7 tolerances wide became 1.3, and the
            # strip's face got triangles at the origin in a slicer's mesher
            after = {r: self._width(lab == r) for r in (a, b)}
            lab[free] = old
            before = {r: self._width(lab == r) for r in (a, b)}
            if any(after[r] < 2 * self.tol and after[r] < before[r] for r in (a, b)):
                continue
            lab[free] = new
            changed = True
        if changed:
            # (no refits or merges: the surfaces haven't changed)
            self._rebuild_regions(refit=False, merge=False)
        return changed

    # cosine: tied triangles' mean agreement with two surfaces' normals closer than this
    # is a tie (a fillet row 11 degrees off a wall differs by 0.02)
    TIE_COS = 1e-4
    # share of the larger region's area: regions closer in area than this are a tie
    TIE_AREA = 1e-6

    def _tied_side(self, faces, a, b):
        """Which of regions a and b takes triangles that fit both, when giving
        them to either leaves an equally short border; from the shape alone, so
        the answer doesn't depend on the order of triangles or regions: the
        surface their directions agree with better (area weighted); then a curved
        surface over a flat one (the border then runs along the tangent line,
        which lies on both); then the larger region; then the region whose
        area-weighted centre comes first by x, then y, then z."""
        regs = self.regions
        w = self.fa[faces]
        cen = self.V[self.F[faces]].mean(1)
        agree = []
        for r in (a, b):
            nrm = regs[r].prim.normal(cen) * regs[r].sense
            agree.append(float(((self.fn[faces] * nrm).sum(1) * w).sum() / max(w.sum(), 1e-300)))
        if abs(agree[0] - agree[1]) > self.TIE_COS:
            return a if agree[0] > agree[1] else b
        flat = [regs[r].kind == "plane" for r in (a, b)]
        if flat[0] != flat[1]:
            return b if flat[0] else a
        area = [float(self.fa[regs[r].faces].sum()) for r in (a, b)]
        if abs(area[0] - area[1]) > self.TIE_AREA * max(area):
            return a if area[0] > area[1] else b
        c = []
        for r in (a, b):
            fr = regs[r].faces
            wr = self.fa[fr]
            c.append(tuple(np.round((self.V[self.F[fr]].mean(1) * wr[:, None]).sum(0) / max(wr.sum(), 1e-300), 9)))
        return a if c[0] <= c[1] else b

    def _width(self, mask):
        """Mean width of a set of triangles: twice their area over the
        length of their outline."""
        faces = np.nonzero(mask)[0]
        if not len(faces):
            return np.inf
        he = (3 * faces[:, None] + np.arange(3)[None]).reshape(-1)
        twin = self.topo.twin[he]
        outer = (twin < 0) | ~mask[np.maximum(twin, 0) // 3]
        a = self.V[self.topo.he_from[he[outer]]]
        b = self.V[self.topo.he_to[he[outer]]]
        perim = float(np.linalg.norm(b - a, axis=1).sum())
        return 2.0 * float(self.fa[faces].sum()) / perim if perim > 0 else np.inf

    def _pieces(self, mask):
        """How many separate pieces the triangles in ``mask`` make."""
        from scipy import sparse
        from scipy.sparse.csgraph import connected_components

        f = np.nonzero(mask)[0]
        if not len(f):
            return 0
        idx = np.full(len(mask), -1)
        idx[f] = np.arange(len(f))
        n = self.nb[f]
        ok = (n >= 0) & mask[np.maximum(n, 0)]
        r = np.repeat(np.arange(len(f)), 3)[ok.reshape(-1)]
        c = idx[n[ok]]
        G = sparse.coo_matrix((np.ones(len(r)), (r, c)), shape=(len(f), len(f)))
        return connected_components(G, directed=False)[0]

    def _flat_slivers_to_long_edge(self):
        """Give each triangle with no area to speak of (its corners in a line) to the
        region across its longest edge: it has no surface of its own, and held by a
        short edge it makes a spike of zero width in its face's outline, whose two
        sides OpenCASCADE finds crossing (Solar Panel Bracket Large: two such spikes
        broke a plane and a freeform face, and the whole body was saved faceted).
        Returns True if anything changed."""
        lab, nb, topo = self.label, self.nb, self.topo
        L = np.linalg.norm(self.V[topo.he_to] - self.V[topo.he_from], axis=1).reshape(-1, 3)
        k = L.argmax(1)
        rows = np.arange(len(lab))
        h = 2.0 * self.fa / np.maximum(L[rows, k], 1e-300)
        o = nb[rows, k]
        changed = False
        # (a fan of them along one long edge passes the region on one by one)
        for _ in range(3):
            low = h < SLIVER_HEIGHT * self.tol
            # (a needle at the very tip of a flat face beside a freeform one goes at a greater height: it
            # carries the flat face's corner past where its surfaces meet. Rak N: one 0.01 mm high and
            # 0.96 long put a corner 0.5 mm along a cone's arc, the edges beside it crossed there, and
            # the mesher dropped the face, leaving 815 of the part's 846 open edges)
            nbl = np.where(nb >= 0, lab[np.maximum(nb, 0)], lab[:, None])
            tip = (nbl != lab[:, None]).sum(1) == 2
            for f in np.nonzero(tip & ~low & (h < TIP_HEIGHT * self.tol) & (o >= 0))[0]:
                q = lab[o[f]]
                if q != lab[f] and q >= 0 and self.regions[lab[f]].kind == "plane" and self.regions[q].kind == "freeform":
                    low[f] = True
            move = np.nonzero(low & (o >= 0) & (lab[np.maximum(o, 0)] != lab))[0]
            if not len(move):
                break
            lab[move] = lab[o[move]]
            changed = True
        if changed:
            # (the surfaces stay as they are and no regions merge)
            self._rebuild_regions(refit=False, merge=False)
        return changed

    def _absorb_isolated_fragments(self):
        """Absorb triangles that are completely surrounded by other regions.

        A triangle with no same-region neighbour creates a disconnected face
        component that OpenCASCADE cannot represent as a single valid face.
        This arises when split_pinched_regions re-labels some triangles and
        leaves tiny fragments behind.  Each such triangle is merged into
        whichever neighbour has the lowest gap to its surface; ties are broken
        by region size (larger region = more stable boundary).

        Does not run the majority pass or touch any other region boundaries.
        Returns True if anything changed.
        """
        regs, lab, nb = self.regions, self.label, self.nb
        nl = np.where(nb >= 0, lab[np.maximum(nb, 0)], -1)
        other = (nl >= 0) & (nl != lab[:, None])
        isolated = np.nonzero(other.all(1) & (nl >= 0).any(1))[0]
        # (the one triangle a pinched plane's cut took off: see _shortest_cut)
        cusps = getattr(self, "cusps", None)
        if cusps:
            isolated = isolated[~np.isin(isolated, list(cusps))]
        if not len(isolated):
            return False
        best_dst = np.full(len(isolated), -1, dtype=np.int64)
        gap_best = np.full(len(isolated), np.inf)
        sizes = np.array([len(r.faces) for r in regs])
        for edge in range(3):
            dst = nl[isolated, edge]
            valid = dst >= 0
            for rj in np.unique(dst[valid]):
                k = np.nonzero(valid & (dst == rj))[0]
                g = self._gap(regs[rj].prim, isolated[k])
                # (a designed plane takes only what lies on it: on SMA-USB a lone triangle
                # 2 mm2, with a corner 0.013 mm off, joined a wall and doubled its outline back)
                near = getattr(regs[rj].prim, "on_design", None)
                if near is not None:
                    g = np.where(g <= near, g, 1e30)
                for ki, idx in enumerate(k):
                    better = g[ki] < gap_best[idx] - 1e-10
                    same_gap = abs(g[ki] - gap_best[idx]) < 1e-10
                    bigger = best_dst[idx] < 0 or sizes[rj] > sizes[best_dst[idx]]
                    if better or (same_gap and bigger):
                        gap_best[idx] = g[ki]
                        best_dst[idx] = rj
        # Only absorb if the destination surface actually fits the fragment
        # within tolerance.  A fragment with gap > tol to all neighbours is
        # genuinely ambiguous — leave it for the invalid-face demotion path
        # rather than forcing it into a region whose surface it doesn't match.
        move = (best_dst >= 0) & (gap_best <= self.tol)
        if not move.any():
            return False
        lab[isolated[move]] = best_dst[move]
        # (the surfaces stay as they are and no regions merge: two
        # neighbouring cylinders of a worm gear merged into one that
        # followed neither closely, between the build's rounds)
        self._rebuild_regions(refit=False, merge=False)
        return True

    def _absorb_folded_islands(self):
        """A small region wholly inside a plane's region, within the tolerance of that plane and
        facing away from it, joins it. Such a fold of the mesh can't be a hole in a flat face:
        seen along the plane the triangles round it lie over it, so the hole's outline winds the
        wrong way, and the whole face is invalid for it (test14: one triangle of 0.5 mm2 cost
        a 1,726 mm2 wall; before designed planes Rak's walls lost out to a 0.1 mm2 fold the same
        way). Returns whether anything changed."""
        regs, lab, nb = self.regions, self.label, self.nb
        nl = np.where(nb >= 0, lab[np.maximum(nb, 0)], -1)
        changed = False
        for ri, reg in enumerate(regs):
            faces = np.asarray(reg.faces)
            if not len(faces) or len(faces) > FOLD_TRIS:
                continue
            round_it = nl[faces]
            out = np.unique(round_it[(round_it != ri)])
            if len(out) != 1 or out[0] < 0:
                continue
            host = regs[int(out[0])]
            if host.kind != "plane" or host.facet or getattr(host.prim, "on_design", None) is not None:
                continue
            facing = host.prim.normal(np.zeros((1, 3)))[0] * host.sense
            if float((self.fn[faces] * self.fa[faces, None] @ facing).sum()) > 0:
                continue
            if self._gap(host.prim, faces).max() <= self.tol:
                lab[faces] = int(out[0])
                changed = True
        if changed:
            self._rebuild_regions(refit=False, merge=False)
        return changed

    def _absorb_self_crossing_strips(self, sources, passes=4):
        """Try to remove border triangles from faces whose *built* wire crosses.

        This is deliberately not part of normal segmentation.  It is a
        second, topology-checked attempt after OpenCASCADE has proved that a
        face's wire crosses itself.  A candidate triangle must share one edge
        with a neighbouring analytic surface (so at least one of that edge's
        two vertices already participates in a face of the neighbour), and the
        whole triangle must fit the neighbour within tolerance and better than
        its current surface.  The caller builds the result again and keeps it
        only if the reported self-crossing actually decreases without adding
        invalid faces.  The purely geometric criterion (shared-edge anchor +
        gap improvement) is model-agnostic: it fires wherever a region boundary
        produces a crossing wire, regardless of the model.
        """
        regs, lab, nb, F = self.regions, self.label, self.nb, self.F
        sources = set(sources)
        if not sources:
            return False
        nr = len(regs)
        target = np.array([not r.weak and (r.kind in ("plane", "cylinder", "cone", "sphere", "torus")
                                           or hasattr(r.prim, "motion")) for r in regs], dtype=bool)
        changed = False
        for _ in range(passes):
            nl = np.where(nb >= 0, lab[np.maximum(nb, 0)], -1)
            touch = np.unique(F.reshape(-1).astype(np.int64) * nr + np.repeat(lab, 3))
            cand = np.nonzero(np.isin(lab, tuple(sources)) & (nl != lab[:, None]).any(1))[0]
            best = np.full(len(cand), -1)
            for edge in range(3):
                dst = nl[cand, edge]
                ok = (dst >= 0) & (dst != lab[cand]) & target[np.maximum(dst, 0)] & (best < 0)
                # A candidate triangle is anchored at the boundary when at
                # least one vertex of the shared edge (the edge that touches
                # the neighbour) already participates in a face of that
                # neighbour.  Half-edge convention: nb[fi, e] is the
                # neighbour across the edge from F[fi, e] to F[fi, (e+1)%3].
                dmax = np.maximum(dst, 0)
                v0 = F[cand, edge].astype(np.int64) * nr + dmax
                v1 = F[cand, (edge + 1) % 3].astype(np.int64) * nr + dmax
                ok &= np.isin(v0, touch) | np.isin(v1, touch)
                best[ok] = dst[ok]
            keep = best >= 0
            cand, best = cand[keep], best[keep]
            if not len(cand):
                break
            gap_new = np.full(len(cand), np.inf)
            gap_own = np.full(len(cand), np.inf)
            for rj in np.unique(best):
                k = np.nonzero(best == rj)[0]
                fit = self.accept(regs[rj].prim, regs[rj].sense, cand[k])
                gap_new[k[fit]] = self._gap(regs[rj].prim, cand[k[fit]])
            for ri in np.unique(lab[cand]):
                k = np.nonzero(lab[cand] == ri)[0]
                gap_own[k] = self._gap(regs[ri].prim, cand[k])
            move = (gap_new <= self.tol) & (gap_new < gap_own)
            if not move.any():
                break
            lab[cand[move]] = best[move]
            changed = True
        if changed:
            self._rebuild_regions()
        return changed

    def _find_sweeps(self, sharp_deg=30.0, min_regions=3):
        """Smooth patches (bounded by sharp edges) that one swept surface
        explains (see sweep.py: a helical gear's tooth flank, a thread, a
        straight gear's tooth side) become one face, instead of the many
        small planes, cylinders and freeform pieces that each follow a bit
        of it. Patches already one good analytic surface are left alone.
        Returns the number of patches replaced."""
        from scipy import sparse
        from scipy.sparse.csgraph import connected_components

        from .sweep import MAX_RMS_DEG, fit_motion, fit_swept, motion_rms

        F, fn, fa, nb, lab = self.F, self.fn, self.fa, self.nb, self.label
        nf = len(F)
        rows = np.repeat(np.arange(nf), 3)
        cols = nb.reshape(-1)
        m = cols >= 0
        smooth = m.copy()
        smooth[m] = (fn[rows[m]] * fn[cols[m]]).sum(1) >= np.cos(np.radians(sharp_deg))
        G = sparse.coo_matrix((np.ones(smooth.sum(), np.int8), (rows[smooth], cols[smooth])), shape=(nf, nf))
        ncomp, comp = connected_components(G, directed=False)
        total = float(fa.sum())
        order = np.argsort(comp, kind="stable")
        starts = np.r_[0, np.nonzero(np.diff(comp[order]))[0] + 1, nf]
        cen = self.V[F].mean(1)
        found = 0
        kinds, sure = {}, 1.0
        cands = []
        for k in range(len(starts) - 1):
            faces = order[starts[k]:starts[k + 1]]
            # (no limit on area: a ring gear's flanks are each a tiny share
            # of the part)
            if len(faces) < 12 or fa[faces].sum() < 1e-5 * total:
                continue
            regs = np.unique(lab[faces])
            if len(regs) < min_regions:
                continue  # already one or two faces: nothing to gain
            if any(_locked(self.regions[r].prim) for r in regs):
                continue  # (stl2curves' surfaces stay: see _locked)
            # one good analytic surface covering nearly all of it stays
            areas = np.bincount(np.searchsorted(regs, lab[faces]), weights=fa[faces])
            top = self.regions[regs[int(np.argmax(areas))]]
            if areas.max() > 0.95 * areas.sum() and not top.weak and top.kind != "freeform":
                continue
            cands.append(faces)
        # The patches of one part often share one motion (all flanks of a
        # gear screw about its axis): fitted on all of them at once it is
        # far better determined than on one thin flank (whose normals barely
        # vary), and the flanks come out exactly alike. Groups of patches
        # that agree on a motion get it; the rest use their own.
        own = [fit_motion(cen[f], fn[f], fa[f]) for f in cands]
        # a patch no one motion explains may hold parts that one does (a
        # filleted extrusion: side, fillet and top are one smooth patch)
        split = []
        for f, m in zip(cands, own):
            subs = self._sub_patches(f, min_regions) if m is None else []
            split += subs if subs else [(f, m)]
        cands = [f for f, _ in split]
        own = [m for _, m in split]
        motion = list(own)
        left = set(range(len(cands)))
        area = [float(fa[f].sum()) for f in cands]

        def agree(mot):
            return [i for i in left if motion_rms(mot, cen[cands[i]], fn[cands[i]], fa[cands[i]]) <= MAX_RMS_DEG]

        def fit_on(idx):
            u = np.concatenate([cands[i] for i in idx])
            return fit_motion(cen[u], fn[u], fa[u])

        while len(left) >= 2:
            hyps = []
            swept_like = [i for i in left if own[i] is not None and own[i].kind != "revolution"]
            if len(swept_like) >= 2:
                hyps.append(fit_on(swept_like))
            big = sorted((i for i in left if own[i] is not None), key=lambda i: -area[i])[:20]
            hyps += [own[i] for i in big]
            # a thin flank alone can't tell its motion for sure (its normals
            # barely vary), but it can propose one: kept only when other
            # patches agree, and then refitted on all of them
            unsure = sorted((i for i in left if own[i] is None), key=lambda i: -area[i])[:20]
            hyps += [fit_motion(cen[cands[i]], fn[cands[i]], fa[cands[i]], min_gap=1.2) for i in unsure]
            best, best_area = None, 0.0
            for h in hyps:
                if h is None:
                    continue
                g = agree(h)
                ga = sum(area[i] for i in g)
                if len(g) >= 2 and ga > best_area:
                    best, best_area, group = h, ga, g
            if best is None:
                break
            ref = fit_on(group)
            if ref is None:
                # the group together must be sure of it (a proposal from a
                # thin patch alone is not enough)
                for i in group:
                    left.discard(i)
                continue
            group = agree(ref) or group
            for i in group:
                motion[i] = ref
            left -= set(group)
        for i, faces in enumerate(cands):
            vid = np.unique(F[faces])
            mot = motion[i]
            if mot is None:
                continue
            if True:
                # one cone, cylinder, sphere or torus, when it fits, is
                # better than a sweep: a turned straight line or arc is one
                # (a faceted chamfer has corners only at its two ends, too
                # few for a profile curve), so are lines through one point
                # along a circle, and pieces of one cylinder that a patch
                # holds (extruded, they followed a printed liner 2.5 times
                # less closely than the cylinder)
                prim = self._revolved_analytic(faces)
                if prim is not None:
                    lab[faces] = len(self.regions)
                    self.regions.append(Region(prim, faces, self.sense_of(prim, faces)))
                    found += 1
                    continue
            surf, dev = fit_swept(cen[faces], fn[faces], fa[faces], self.tol, X=self.V[vid], motion=mot)
            if surf is None or dev > self.tol:
                continue
            mot = surf.motion
            # all the way round (a thread): not yet. A turned face short of
            # a full turn is one face (the rounded rim round a hole, broken
            # only where an arm leaves it), as long as its range, margins
            # included, doesn't come back over itself
            most = 1.95 * np.pi if mot.kind == "revolution" else 1.6 * np.pi
            if mot.kind != "extrusion" and mot.turns(surf.t_range[1] - surf.t_range[0]) > most:
                continue
            # every triangle of it on the surface, between its corners too
            T = self.V[F[faces]]
            M = np.concatenate([T, (T + np.roll(T, -1, axis=1)) / 2], axis=1).reshape(-1, 3)
            if surf.distance(M).max() > self.tol_mid:
                continue
            if not self._no_worse(surf, faces):
                continue
            sense = self.sense_of(surf, faces)
            faces = self._take_in_pieces(surf, faces)
            if surf.curve.closed:
                # all the way round: two faces of half the profile each
                half = surf.half_of(cen[faces])
                if half.all() or not half.any():
                    continue
                for hs, hf in zip(surf.halves(), (faces[half], faces[~half])):
                    lab[hf] = len(self.regions)
                    self.regions.append(Region(hs, hf, self.sense_of(hs, hf)))
            else:
                lab[faces] = len(self.regions)
                self.regions.append(Region(surf, faces, sense))
            found += 1
            kinds[mot.kind] = kinds.get(mot.kind, 0) + 1
            sure = min(sure, mot.sureness)
        if kinds:
            words = {"helix": "helical", "extrusion": "extruded", "revolution": "turned", "taper": "tapered"}
            self.report.log("Found " + ", ".join(f"{n} {words[k]}" for k, n in kinds.items())
                            + f" surface(s) swept along one motion (sureness {sure:.0%})")
        if found:
            self._rebuild_regions()
        return found

    def _find_fillets(self, min_regions=3):
        """Fillets along curved edges (see fillet.py): neighbouring tori and
        cylinders of one radius, which the segmenter cut a rounded edge
        into, become one pipe face when every triangle lies on it. Pieces
        that are all one torus or cylinder already are left alone. Returns
        the number of fillets found."""
        from scipy import sparse
        from scipy.sparse.csgraph import connected_components

        from .fillet import fit_pipe

        regs, lab, nb, F, fa = self.regions, self.label, self.nb, self.F, self.fa
        nr, nf = len(regs), len(F)
        rad = np.full(nr, np.nan)
        for i, reg in enumerate(regs):
            if reg.weak or not len(reg.faces) or _locked(reg.prim):
                continue
            if reg.kind in ("torus", "cylinder") and not hasattr(reg.prim, "motion"):
                rad[i] = reg.prim.r
        rows = np.repeat(np.arange(nf), 3)
        cols = nb.reshape(-1)
        m = cols >= 0
        a, b = lab[rows[m]], lab[cols[m]]
        k = (a != b) & (a >= 0) & (b >= 0)
        a, b = a[k], b[k]
        k = np.isfinite(rad[a]) & np.isfinite(rad[b])
        a, b = a[k], b[k]
        same = np.abs(rad[a] - rad[b]) <= np.maximum(0.03 * np.minimum(rad[a], rad[b]), 0.5 * self.tol)
        G = sparse.coo_matrix((np.ones(same.sum(), np.int8), (a[same], b[same])), shape=(nr, nr))
        ncomp, comp = connected_components(G, directed=False)
        cen = self.V[F].mean(1)
        found = 0
        for c in range(ncomp):
            members = np.nonzero(comp == c)[0]
            if len(members) < min_regions:
                continue
            p0 = regs[members[0]].prim
            if all(regs[i].prim.kind == p0.kind and regs[i].prim.same_as(p0, self.tol, self.ext) for i in members[1:]):
                continue
            faces = np.concatenate([np.asarray(regs[i].faces) for i in members])
            # spine samples: each triangle's centre moved onto its piece's
            # spine (a torus's centre circle, a cylinder's axis)
            C = []
            for i in members:
                p, P = regs[i].prim, cen[regs[i].faces]
                C.append(p._spine(P) if p.kind == "torus" else p.a + np.outer((P - p.a) @ p.d, p.d))
            w = np.array([fa[regs[i].faces].sum() for i in members])
            r = float(rad[members][np.argsort(rad[members])][np.searchsorted(np.cumsum(w[np.argsort(rad[members])]), 0.5 * w.sum())])
            C = np.vstack(C)
            surf, dev = fit_pipe(C, self.V[np.unique(F[faces])], r, self.tol)
            # the fillet's other pieces (the segmenter fits some as cones or
            # freeform cells): small neighbours near the pipe join, and it is
            # fitted again on all of them
            for _ in range(2):
                if surf is None:
                    break
                inside = np.zeros(nf, dtype=bool)
                inside[faces] = True
                near = nb[faces]
                near = np.unique(lab[near[near >= 0]])
                more = []
                for l in near:
                    if l < 0 or hasattr(regs[l].prim, "motion"):
                        continue
                    g = np.asarray(regs[l].faces)
                    g = g[(lab[g] == l) & ~inside[g]]
                    if len(g) and len(g) <= 0.2 * len(faces) and self._gap(surf, g).max() <= 3 * self.tol:
                        more.append(g)
                if not more:
                    break
                g = np.concatenate(more)
                faces = np.concatenate([faces, g])
                C = np.vstack([C, surf.curve.at(surf.foot(cen[g])[0])])
                surf, dev = fit_pipe(C, self.V[np.unique(F[faces])], r, self.tol)
            if surf is None or dev > self.tol:
                continue
            T = self.V[F[faces]]
            M = np.concatenate([T, (T + np.roll(T, -1, axis=1)) / 2], axis=1).reshape(-1, 3)
            if surf.distance(M).max() > self.tol_mid or not self._no_worse(surf, faces):
                continue
            faces = self._take_in_pieces(surf, faces)
            if surf.curve.closed:
                halves = surf.halves()
                half = surf.half_of(cen[faces])
                if halves is None or half.all() or not half.any():
                    continue
                for hs, hf in zip(halves, (faces[half], faces[~half])):
                    lab[hf] = len(self.regions)
                    self.regions.append(Region(hs, hf, self.sense_of(hs, hf)))
            else:
                lab[faces] = len(self.regions)
                self.regions.append(Region(surf, faces, self.sense_of(surf, faces)))
            found += 1
        if found:
            self.report.log(f"Found {found} fillet(s) along curved edges (a ball rolled along the edge)")
            self._rebuild_regions()
        return found

    def _sub_patches(self, faces, min_regions=3, max_deg=3.0, min_tris=30):
        """Parts of a smooth patch that one motion explains, as [(faces,
        motion)]: the side of an extrusion whose top edge is rounded (the
        fillet joins side and top smoothly, so the three are one patch). The
        patch's own analytic regions propose the motions: a cylinder its
        axis to slide along or turn about, a cone its axis to turn about. The triangles square to a motion (within max_deg), in
        connected pieces over at least min_regions regions, are confirmed by
        fitting the motion on them alone: it must come out the motion
        proposed."""
        from .sweep import Motion, fit_motion

        F, fn, fa, nb, lab = self.F, self.fn, self.fa, self.nb, self.label
        regs = np.unique(lab[faces])
        props = []
        for r in regs:
            p = self.regions[r].prim
            w = float(fa[self.regions[r].faces].sum())
            if p.kind == "cylinder":
                props += [(w, Motion("extrusion", p.a, p.d)), (w, Motion("revolution", p.a, p.d))]
            elif p.kind == "cone":
                props.append((w, Motion("revolution", p.apex, p.d)))
            # (not a torus: inside a patch no one motion explains, those are
            # nearly always pieces of a fillet, which _find_fillets joins; a
            # motion proposed by one turned a fillet into a helix)
        # (extrusions along one direction are one motion, whatever cylinder
        # proposed them)
        seen, hyps = [], []
        for w, m in sorted(props, key=lambda x: -x[0]):
            key = (m.kind, m.d, m.a)
            if any(k == m.kind and abs(d @ m.d) > np.cos(np.radians(1.0))
                   and (k == "extrusion" or np.linalg.norm(np.cross(a - m.a, m.d)) < self.tol)
                   for k, d, a in seen):
                continue
            seen.append(key)
            hyps.append(m)
        # (curved triangles only: a flat face has its surface already, and
        # every plane containing a motion's direction agrees with it, which
        # on a printed mount made patches of 20,000 triangles that only cost
        # time, and a stray small one that changed the faces around it)
        flat = np.array([r.kind == "plane" and not r.weak for r in self.regions], dtype=bool)
        faces = faces[~flat[lab[faces]]]
        cen = self.V[F[faces]].mean(1)
        used = np.zeros(len(F), dtype=bool)
        lim = np.sin(np.radians(max_deg))
        out = []
        for mot in hyps[:60]:
            v = mot.velocity(cen)
            sp = np.linalg.norm(v, axis=1)
            ok = (sp > 1e-12) & (np.abs((fn[faces] * v).sum(1)) <= lim * np.maximum(sp, 1e-300)) & ~used[faces]
            if ok.sum() < min_tris:
                continue
            for comp in _cluster_components(faces[ok], nb):
                comp = np.asarray(comp)
                if len(comp) < min_tris or len(np.unique(lab[comp])) < min_regions:
                    continue
                m = fit_motion(self.V[F[comp]].mean(1), fn[comp], fa[comp])
                # confirmed only as the motion proposed (fitted on the
                # agreeing triangles alone, a fillet's came out a helix)
                if m is None or m.kind != mot.kind or abs(m.d @ mot.d) < np.cos(np.radians(2.0)):
                    continue
                used[comp] = True
                out.append((comp, m))
        return out

    def _no_worse(self, surf, faces, slack=1.1, many=5):
        """Whether a swept surface or fillet is worth the regions it would
        replace: it follows their triangles clearly more closely (largest
        gap of corners, edge midpoints and centres), or about as closely
        and stands for many of them. On a printed liner, extrusions 1.4
        tolerances off replaced cylinders 0.9 off and the largest gap grew
        threefold; others, as close as the three cylinders they replaced
        by that measure, still stood off the mesh between those points,
        where the cylinders hadn't."""
        new = float(self._gap(surf, faces).max())
        old = 0.0
        lab = self.label
        regs = [r for r in np.unique(lab[faces]) if r >= 0]
        for r in regs:
            g = faces[lab[faces] == r]
            old = max(old, float(self._gap(self.regions[r].prim, g).max()))
        if new <= old / slack:
            return True
        return len(regs) >= many and new <= slack * old + 0.05 * self.tol

    def _take_in_pieces(self, surf, faces, share=0.1):
        """faces of a swept surface, plus the small neighbouring regions
        that lie on it (every corner, edge midpoint and centre within the
        tolerance). At the fan of long thin triangles where a tooth flank
        meets a top or bottom face, a region reaching across the flank's
        border leaves single triangles of the flank outside it (their
        normals are too noisy to count as smooth), which would become tiny
        planes of their own; left out, they make the flank touch itself at
        the fan's vertex, and the cut that separates it leaves necks
        narrower than the tolerance, so the faces come out invalid (a bevel
        gear). Each connected piece of a neighbour outside the surface is
        judged on its own."""
        lab, nb = self.label, self.nb
        inside = np.zeros(len(self.F), dtype=bool)
        inside[faces] = True
        near = nb[faces]
        near = near[near >= 0]
        near = near[~inside[near]]
        out = [faces]
        for l in np.unique(lab[near]):
            if l < 0 or hasattr(self.regions[l].prim, "motion"):
                continue
            g = np.asarray(self.regions[l].faces)
            g = g[(lab[g] == l) & ~inside[g]]
            if not len(g):
                continue
            for piece in _cluster_components(g, nb):
                piece = np.asarray(piece)
                if len(piece) > share * len(faces) or not inside[nb[piece][nb[piece] >= 0]].any():
                    continue
                if self._gap(surf, piece).max() <= self.tol:
                    out.append(piece)
                    inside[piece] = True
        return np.concatenate(out) if len(out) > 1 else faces

    def _revolved_analytic(self, faces):
        """A cone, cylinder, sphere or torus that every triangle of faces
        lies on, or None."""
        P, N, ok, W = self.region_points(faces, True)
        for cls in (Cone, Cylinder, Sphere, Torus):
            prim = self._robust_fit(cls, P[ok], N[ok], W[ok])
            if prim is None or prim.degenerate(self.ext):
                continue
            if self.accept(prim, self.sense_of(prim, faces), faces).all():
                return prim
        return None

    def _make_freeform(self, smooth_deg=35.0):
        """Clusters of facet-like leftovers become freeform B-spline cells.

        A weak region (one flat facet that no surface claimed) is facet-like
        when it meets another weak region across a smooth edge; isolated weak
        regions bounded by sharp edges are genuine small planar faces."""
        from scipy import sparse
        from scipy.sparse.csgraph import connected_components

        regs = self.regions
        lab = self.label
        weak = np.array([r.weak for r in regs], dtype=bool)
        if not weak.any():
            return
        nf = len(self.F)
        rows = np.repeat(np.arange(nf), 3)
        cols = self.nb.reshape(-1)
        m = cols >= 0
        rows, cols = rows[m], cols[m]
        cs = (self.fn[rows] * self.fn[cols]).sum(1)
        smooth = (cs >= np.cos(np.radians(smooth_deg))) | self.unsure[rows] | self.unsure[cols]
        lr, lc = lab[rows], lab[cols]
        m = smooth & (lr != lc) & weak[lr] & weak[lc]
        nr = len(regs)
        G = sparse.coo_matrix((np.ones(m.sum(), dtype=np.int8), (lr[m], lc[m])), shape=(nr, nr))
        ncomp, comp = connected_components(G, directed=False)
        sizes = np.bincount(comp, minlength=ncomp)
        from .fidelity import designed_flats

        ff = weak & (sizes[comp] >= 2)
        # (a piece the mesh itself shows to be a designed plane stays one: of the 112 big pieces made into cells on 33 parts
        # with the designer's STEP, all 112 were planes; obj_2_02's flats and chamfers, 20% of the part, went this way)
        ff &= ~designed_flats(self.V, self.F, self.fn, self.fa, lab, nr)
        # a coarse curve's facets turn through three or more directions; flat pieces in only
        # one or two (a wall and its chamfer, the chamfer cut into pieces) are a designed
        # crease, which no smooth surface follows (see FACET_TURNS)
        # (and a flat piece that dwarfs the rest, not a thin strip, is a face that was drawn: filament
        # guide's top and bottom, 797 and 992 mm2, went to B-spline cells beside their small chamfer
        # pieces, and with them nearly every edit of those faces)
        area = np.array([float(self.fa[np.asarray(r.faces, dtype=np.int64)].sum()) for r in regs])
        for c in np.unique(comp[ff]):
            while True:
                members = np.nonzero(ff & (comp == c))[0]
                if len(members) < 2:
                    break
                typical = float(np.median(area[members]))
                drawn = [i for i in members if area[i] >= FACET_DWARFS * typical
                         and not self._strip_like(np.asarray(regs[i].faces, dtype=np.int64))]
                if not drawn:
                    break
                ff[drawn] = False
        for c in np.unique(comp[ff]):
            members = np.nonzero(ff & (comp == c))[0]
            dirs = []
            for i in members:
                nrm = np.asarray(regs[i].prim.n, dtype=float)
                if not any(abs(float(nrm @ d)) >= np.cos(np.radians(FACET_SAME_DEG)) for d in dirs):
                    dirs.append(nrm)
            if len(dirs) < FACET_TURNS:
                ff[members] = False
        if not ff.any():
            return
        ffaces = np.nonzero(ff[lab])[0]
        keep = [i for i in range(nr) if not ff[i]]
        new_regs = [regs[i] for i in keep]
        new_lab = -np.ones(nf, dtype=np.int64)
        for k, i in enumerate(keep):
            new_lab[regs[i].faces] = k
        cells = []
        for cfaces in _cluster_components(ffaces, self.nb):
            cells.extend(fit_freeform_cells(self, cfaces))
        # A cell that can't follow its own triangles (fit_freeform_cells keeps
        # its best try once a cell is too small to split) gives them back to the
        # flat faces it was made from, which follow them exactly: a crease
        # between two big flat faces (a wall and its chamfer) fits no smooth
        # surface, and the B-spline bulged 0.4 mm off it; the facets of a coarse
        # curved surface it can't follow either are better flat than off the
        # mesh. Within the mesh's own coarseness a cell is right (a true curve
        # stands off coarse facets by that much). A flat face goes back whole, so
        # a cell sharing one with a cell that missed goes back too.
        miss = max(self.tol, self.deflection)
        back = np.zeros(nr, dtype=bool)
        bad = [c.prim.dev > miss for c in cells]
        while True:
            for c, b in zip(cells, bad):
                if b:
                    back[np.unique(lab[c.faces])] = True
            more = [not b and back[lab[c.faces]].any() for c, b in zip(cells, bad)]
            if not any(more):
                break
            bad = [b or m for b, m in zip(bad, more)]
        for i in np.nonzero(back)[0]:
            new_lab[regs[i].faces] = len(new_regs)
            new_regs.append(regs[i])
        kept = [c for c, b in zip(cells, bad) if not b]
        for cell in kept:
            cell.parts = [regs[i] for i in np.unique(lab[cell.faces])]
            new_lab[cell.faces] = len(new_regs)
            new_regs.append(cell)
        if back.any():
            self.report.log(f"{int(back.sum())} flat face(s) kept flat: no smooth surface follows them "
                            "(a crease, or a coarse curve too far off its facets)")
        self.freeform_faces = np.concatenate([c.faces for c in kept]) if kept else np.zeros(0, dtype=np.int64)
        self.regions = new_regs
        self.label = new_lab

    def _rebuild_regions(self, refit=True, merge=True):
        """Split disconnected labels, refit primitives and merge equal
        ones (each unless turned off)."""
        from scipy import sparse
        from scipy.sparse.csgraph import connected_components

        nf = len(self.F)
        lab = self.label
        rows = np.repeat(np.arange(nf), 3)
        cols = self.nb.reshape(-1)
        m = (cols >= 0) & (lab[rows] == lab[np.maximum(cols, 0)])
        G = sparse.coo_matrix((np.ones(m.sum(), dtype=np.int8), (rows[m], cols[m])), shape=(nf, nf))
        ncomp, comp = connected_components(G, directed=False)
        old = self.regions
        new_regions = []
        new_label = np.empty(nf, dtype=np.int64)
        order = np.argsort(comp, kind="stable")
        sc = comp[order]
        starts = np.r_[0, np.nonzero(np.diff(sc))[0] + 1, nf]
        for k in range(len(starts) - 1):
            faces = order[starts[k]:starts[k + 1]]
            r = old[lab[faces[0]]]
            prim, sense = r.prim, r.sense
            if not refit or _locked(prim):
                pass
            elif prim.kind not in ("plane", "freeform") and faces.size >= 3:
                P, N, ok, W = self.region_points(faces, True)
                p2 = prim.refit(P[ok], N[ok], W[ok])
                if p2 is not None and not p2.degenerate(self.ext) and self.accept(p2, sense, faces, 1.5 * self.tol).all():
                    prim = p2
                prim, sense = self.simplify(prim, sense, faces)
            elif prim.kind == "plane":
                P, N, ok, W = self.region_points(faces, True)
                p2 = prim.refit(P, None, W)
                if p2.distance(P).max() <= self.tol:
                    if getattr(prim, "on_design", None) is not None:
                        p2.on_design = prim.on_design
                    prim = p2
            weak = r.weak and prim.kind == "plane"
            new_label[faces] = len(new_regions)
            reg = Region(prim, faces, sense, weak)
            if faces.size == len(r.faces):
                # (whole, it keeps what it was made of: a pooled surface goes back to its pieces if its face fails)
                reg.parts, reg.pooled = r.parts, r.pooled
            new_regions.append(reg)
        self.regions = new_regions
        self.label = new_label
        if merge:
            self._merge_equal()

    def _merge_equal(self):
        regs = self.regions
        lab = self.label
        nb = self.nb
        nbl = np.where(nb >= 0, lab[np.maximum(nb, 0)], -1)
        pairs = set()
        for j in range(3):
            a = lab
            b = nbl[:, j]
            m = (b >= 0) & (a != b)
            for x, y in zip(a[m], b[m]):
                pairs.add((min(x, y), max(x, y)))
        parent = list(range(len(regs)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for x, y in sorted(pairs):
            rx, ry = regs[x], regs[y]
            if rx.kind != ry.kind or rx.kind == "plane" and (rx.weak or ry.weak):
                continue
            if rx.sense != ry.sense:
                continue
            # (a designed plane doesn't merge with a piece that isn't on it)
            near = getattr(rx.prim, "on_design", None) or getattr(ry.prim, "on_design", None)
            if near is not None and rx.kind == "plane" and (
                    float(rx.prim.distance(self.V[np.unique(self.F[ry.faces])]).max()) > near
                    or float(ry.prim.distance(self.V[np.unique(self.F[rx.faces])]).max()) > near):
                continue
            if rx.prim.same_as(ry.prim, self.tol, self.ext):
                fx, fy = find(x), find(y)
                if fx != fy:
                    parent[fy] = fx
        roots = {}
        out = []
        members = collections.defaultdict(list)
        for i, r in enumerate(regs):
            f = find(i)
            if f not in roots:
                roots[f] = len(out)
                out.append(Region(regs[f].prim, [], regs[f].sense, regs[f].weak))
            elif _locked(r.prim) and not _locked(out[roots[f]].prim):
                # (a surface stl2curves found, locked, outlives an equal fitted one)
                out[roots[f]].prim, out[roots[f]].sense = r.prim, r.sense
            out[roots[f]].faces.append(r.faces)
            members[roots[f]].append(r)
        newlab = np.empty_like(lab)
        for f, k in roots.items():
            r = out[k]
            if len(r.faces) == 1:
                # (unmerged, it keeps what it was made of: a pooled surface goes back to its pieces if its face fails)
                r.parts, r.pooled = regs[f].parts, regs[f].pooled
            elif any(m.pooled for m in members[k]):
                # (merged with a pooled surface, the whole keeps its lineage: every pooled member's pieces and every other
                # member as it was, so a face that doesn't build still goes back to them; without this test13's rim, two
                # pooled tori the rebuild merged, had nothing to go back to and became freeform cells, and the drawn
                # cylinder tiles between the torus pieces went with it, 2026-11-07)
                r.pooled = True
                r.parts = [p for m in members[k] for p in (m.parts if m.pooled and m.parts else [m])]
        for r in out:
            r.faces = np.concatenate(r.faces)
        for i, r in enumerate(out):
            newlab[r.faces] = i
        self.regions = out
        self.label = newlab

    def _pool_pieces(self):
        """Neighbouring pieces that are one surface join it (loose edges and tiny faces, 2026-11-05).
        A smooth area the fits broke into tiles (allen's grip: two drawn B-spline faces became about
        80 planes, cylinders and tori; knob's 8 drawn blends 28 tori and 11 planes) meets itself along
        borders at 0.1-15 degrees where no exact curve exists, and every corner on them reads back
        loose. _merge_equal joins pieces of one fit; this joins pieces that are one surface once
        fitted together, in two stages. First by a simple surface of the members' own kinds or what
        those generalise to (a draft cone broken into cylinder tiles, a bend into them; five kinds
        per try cost knob 12 s on one core, and a simpler kind that fits is simplify()'s to find in
        the rebuild after): a whole cluster of such neighbours, then pairs, smallest first, to a
        fixpoint. Then, what no simple surface holds, as one freeform cell per cluster, when
        that leaves fewer faces and follows the mesh (POOL_FREEFORM; the drawn blends and grips are
        B-splines, and the first stage can only tidy their tiles). A piece whose vertices lie on its
        own surface (POOL_EXACT_SHARE of them: a drawn face, or a piece of one) is joined only by a
        surface that holds its vertices as closely (a cylinder holds a drawn flat within the tolerance, never that closely;
        two pieces of one drawn cylinder it holds exactly), its own surface tried first; a cell takes
        it, like any member, only when it holds it as closely as its own surface did (measured
        2026-11-06: no drawn face the judge listed as lost had gone into a cell, while the exactness
        rule kept 183 tiles of tc_greaser's blend out of cells that would have held 49 of 66 of
        them), except a piece holding a drawn flat, which never goes into a cell: a plane the
        designer drew must come out a plane face, whatever a cell would hold; a surface stl2curves
        found is never refitted, though a neighbour that lies on it joins it; a border whose facets bend as a crease does (POOL_DEG,
        and POOL_CREASE for the freeform stage); pieces in exact contact (a fillet on its cylinder)
        are never made one cell, nor is a piece that dwarfs the tiles beside it (FACET_DWARFS: a drawn
        face a blend runs into, like allen's shaft, which 30 cells took in with the grip's tiles), and
        a cluster becomes cells only as POOL_FEWER times fewer faces. A join never holds a
        piece much worse than its own surface did (POOL_WORSE): within the tolerance isn't enough. A pair or cluster that failed is
        remembered by its faces. Runs once, when the surfaces have grown as far as they will (run):
        during growth it swallowed a fillet's first pieces into the cylinder beside them, and cost
        17 passes. Returns how many pieces fewer there are."""
        classes = [Plane, Cylinder, Cone, Sphere, Torus]
        kinds = {c.kind: c for c in classes}
        # (a kind and what it generalises to: a draft cone broken into cylinder tiles is a cone only as a whole, a bend a
        # torus; a plane generalises to nothing, since a cylinder member covers the plane-and-cylinder case)
        wider = {"cylinder": (Cone, Torus), "sphere": (Torus,)}
        tol = self.tol
        failed = self.__dict__.setdefault("_pool_failed", set())
        stats = self.__dict__.setdefault("pool_stats", collections.Counter())    # (what was tried and why not, for tracing)
        V, F, nb = self.V, self.F, self.nb
        before = getattr(self, "pooled", 0)
        self.pooled = before

        # (the drawn flats, by the mesh's own showing: the exact facet groups fidelity.designed_flats calls planes)
        sf = getattr(self, "sf_lab", None)
        drawn_flat = None
        if sf is not None:
            from .fidelity import designed_flats

            n_groups = int(sf.max()) + 1
            drawn_flat = (sf, designed_flats(V, F, self.fn, self.fa, sf, n_groups), np.bincount(sf, minlength=n_groups))

        def borders(freeform):
            """The candidate borders (x, y) between pieces that may be tiles of one surface, with
            each piece's name, whether its surface is fixed, how far its own surface stands off it
            and the vertices a join must hold; None when there are none."""
            regs, lab = self.regions, self.label
            nr = len(regs)
            if nr < 2:
                return None
            nf = len(F)
            rows = np.repeat(np.arange(nf), 3)
            cols = nb.reshape(-1)
            m = (cols >= 0) & (rows < cols)
            rows, cols = rows[m], cols[m]
            lr, lc = lab[rows], lab[cols]
            bend = np.degrees(np.arccos(np.clip((self.fn[rows] * self.fn[cols]).sum(1), -1.0, 1.0)))
            # (how much each piece's own facets bend, for the crease test)
            inner = np.zeros(nr)
            same = lr == lc
            np.maximum.at(inner, lr[same], bend[same])
            across = ~same
            rows, cols, lr, lc, bend = rows[across], cols[across], lr[across], lc[across], bend[across]
            if not len(rows):
                return None
            key = np.minimum(lr, lc) * nr + np.maximum(lr, lc)
            order = np.argsort(key, kind="stable")
            key, rows, cols, bend = key[order], rows[order], cols[order], bend[order]
            starts = np.r_[0, np.nonzero(np.diff(key))[0] + 1, len(key)]
            plain = np.array([r.kind in kinds and not r.weak and not getattr(r, "facet", False) for r in regs])
            exact_at = max(POOL_EXACT, POOL_NOISE * float(getattr(self, "noise", 0.0)))
            names, fixed, own, held, flat = [], [], [], [], []
            for i, r in enumerate(regs):
                names.append(hash(np.sort(np.asarray(r.faces, dtype=np.int64)).tobytes()))
                if not plain[i]:
                    fixed.append(2)
                    own.append(np.inf)
                    held.append(None)
                    flat.append(False)
                    continue
                fc = np.asarray(r.faces, dtype=np.int64)
                # (the vertices a join must hold exactly: those of a drawn flat inside the piece, else the piece's interior
                # vertices, whose every triangle is the piece's: a ring of a neighbour's triangles at the border doesn't count)
                vid = _held_vertices(self, fc, drawn_flat)
                # (a piece holding a drawn flat never goes into a cell, however closely the cell would hold it: a plane the
                # designer drew must come out a plane face, which can be picked, offset and sketched on; a B-spline can't)
                flat.append(len(_drawn_flats_in(fc, drawn_flat)) > 0)
                # (2: locked, its surface is the only one; 1: exact, a surface must hold it as closely; 0: a tile)
                fixed.append(2 if _locked(r.prim) else int(_lies_on(r.prim, V[vid], exact_at)))
                held.append(vid)
                # (how far its own surface stands off it: a join may be little worse)
                own.append(max(float(self.face_error(r.prim, r.sense, fc)[0].max()), exact_at))
            cen = None
            pairs = []
            for k in range(len(starts) - 1):
                a, b = starts[k], starts[k + 1]
                x, y = int(key[a] // nr), int(key[a] % nr)
                rx, ry = regs[x], regs[y]
                stats["borders"] += 1
                if not (plain[x] and plain[y]):
                    stats["not plain"] += 1
                    continue
                if fixed[x] == 2 and fixed[y] == 2 or freeform and (fixed[x] == 2 or fixed[y] == 2):
                    stats["fixed"] += 1
                    continue
                if freeform and (flat[x] or flat[y]):
                    stats["drawn flat"] += 1
                    continue
                worst = float(bend[a:b].max())
                if worst > POOL_DEG or freeform and worst > POOL_CREASE[0] * max(inner[x], inner[y]) + POOL_CREASE[1]:
                    stats["facets bend more"] += 1
                    continue
                if freeform and contact(rx.prim, ry.prim) is not None:
                    stats["in contact"] += 1
                    continue
                if cen is None:
                    cen = V[F].mean(1)
                pick = np.linspace(a, b - 1, min(b - a, POOL_SAMPLE)).astype(int)
                P = 0.5 * (cen[rows[pick]] + cen[cols[pick]])
                cs = np.abs((rx.prim.normal(P) * ry.prim.normal(P)).sum(1))
                if float(np.degrees(np.arccos(np.clip(np.median(cs), -1.0, 1.0)))) > POOL_DEG:
                    stats["surfaces meet steeply"] += 1
                    continue
                pairs.append((x, y))
            return (pairs, names, fixed, own, held, exact_at) if pairs else None

        def fit_union(regs, members, fixed, own, held, exact_at, try_kinds):
            """The lowest-rank surface of try_kinds that holds every member within tolerance and
            little worse than its own surface did (POOL_WORSE), and an exact member's held
            vertices within exact_at, with its sense, or None. A locked member's own surface is
            the only candidate; an exact member's is tried first."""
            faces = np.concatenate([np.asarray(regs[i].faces, dtype=np.int64) for i in members])
            locked = [i for i in members if fixed[i] == 2]
            if len(locked) > 1:
                return None
            exact = [(i, held[i]) for i in members if fixed[i] == 1]

            def holds(p2, s2):
                if not self.accept(p2, s2, faces).all() or not all(_lies_on(p2, V[vid], exact_at) for _, vid in exact):
                    return False
                return all(float(self.face_error(p2, s2, np.asarray(regs[i].faces, dtype=np.int64))[0].max()) <= POOL_WORSE * own[i]
                           for i in members)

            for i in locked + [i for i, _ in exact]:
                r = regs[i]
                if holds(r.prim, r.sense):
                    return r.prim, r.sense
            if locked:
                return None
            P, N, ok, W = self.region_points(faces, True)
            axes = [regs[i].prim for i in members if isinstance(regs[i].prim, _OnAxis)]
            # (cylinders whose axes lie within POOL_BEND degrees of each other don't bend round a centre: no torus of
            # them. A cone of them stays possible whatever their radii: a draft's tiles side by side round it are
            # parallel cylinders of one radius, and the cone is what the designer drew)
            cyl = [regs[i].prim for i in members if regs[i].kind == "cylinder"]
            straight = len(cyl) == len(members) and len(cyl) > 1 and all(
                abs(float(np.dot(a.d, b.d))) >= np.cos(np.radians(POOL_BEND)) for k, a in enumerate(cyl) for b in cyl[k + 1:])
            for cls in sorted(try_kinds, key=lambda c: c.rank):
                if straight and cls is Torus:
                    stats["kind held back"] += 1
                    continue
                if cls is Plane:
                    p2 = Plane.fit(P, (self.fn[faces] * self.fa[faces, None]), W=W)
                    s2 = 1.0
                elif axes:
                    p2 = on_axis(cls.kind, axes[0].ka, axes[0].kd, P, W)
                    if p2 is None or p2.degenerate(self.ext):
                        continue
                    s2 = self.sense_of(p2, faces)
                else:
                    p2 = self._robust_fit(cls, P[ok], N[ok], W[ok])
                    if p2 is None:
                        continue
                    s2 = self.sense_of(p2, faces)
                if p2.kind == "torus" and p2.r >= POOL_SPINDLE * p2.R:
                    stats["spindle refused"] += 1
                    continue
                # (a generalisation holds its members' own surfaces as its limit: a torus of cylinder tiles has their
                # radius as its tube, a cone of them their radius where they are; a fit that lands elsewhere found
                # another surface through the cloud. Counted only, until measured (POOL_LIMIT_HOLDS))
                if cyl and p2.kind in ("torus", "cone") and _off_its_limit(p2, cyl, [regs[i] for i in members if regs[i].kind == "cylinder"], V, F, tol):
                    stats["generalisation off its limit"] += 1
                    if POOL_LIMIT_HOLDS:
                        continue
                if holds(p2, s2):
                    return p2, s2
            return None

        def try_for(members):
            """The kinds a union of these pieces is tried as: their own, and what every member's kind generalises
            to (two or three fits instead of five: a simpler kind that fits is simplify()'s to find in the rebuild
            after). A generalisation one member doesn't share is out: a plane member keeps a cluster off Cone and
            Torus (tc: a cylinder tile and a few plane triangles, exact by their size, fitted as spindle tori with
            r 2 to 11 times R, and the drawn planes ended under tori)."""
            own = {kinds[self.regions[i].kind] for i in members}
            shared = None
            for i in members:
                k = self.regions[i].kind
                reach = {kinds[k]} | set(wider.get(k, ()))
                shared = reach if shared is None else shared & reach
            return own | (shared or set())

        def clusters_of(pairs, nr):
            parent = list(range(nr))

            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for x, y in pairs:
                parent[find(y)] = find(x)
            out = {}
            for x, y in pairs:
                out.setdefault(find(x), set()).update((x, y))
            return [sorted(c) for c in out.values()]

        def apply(groups):
            """groups: (members, replacement regions): the members go, the replacements come and remember them."""
            regs, lab = self.regions, self.label
            gone = {i for members, _ in groups for i in members}
            out = [r for i, r in enumerate(regs) if i not in gone]
            for members, new in groups:
                for r in new:
                    r.parts, r.pooled = [regs[i] for i in members], True
                out.extend(new)
            newlab = np.empty_like(lab)
            for i, r in enumerate(out):
                r.faces = np.asarray(r.faces, dtype=np.int64)
                newlab[r.faces] = i
            self.pooled += len(regs) - len(out)
            self.regions, self.label = out, newlab

        # --- one simple surface, to a fixpoint ---
        for _ in range(POOL_PASSES):
            got = borders(freeform=False)
            if got is None:
                break
            pairs, names, fixed, own, held, exact_at = got
            regs = self.regions
            taken = set()
            groups = []
            # (whole clusters first: tiles of one bend are a torus only together)
            for members in clusters_of(pairs, len(regs)):
                if len(members) < 3:
                    continue
                name = tuple(names[i] for i in members)
                if name in failed:
                    stats["clusters remembered"] += 1
                    continue
                stats["clusters tried"] += 1
                fit = fit_union(regs, members, fixed, own, held, exact_at, try_for(members))
                if fit is None:
                    failed.add(name)
                    continue
                stats["clusters joined"] += 1
                if fit[0].kind not in {regs[i].kind for i in members}:
                    stats["joined as another kind"] += 1      # (a sphere tile and a plane piece as a cone, say)
                faces = np.concatenate([np.asarray(regs[i].faces, dtype=np.int64) for i in members])
                groups.append((members, [Region(fit[0], faces, fit[1], False)]))
                taken.update(members)
            # (then pairs, the smallest first: a small piece is likeliest a tile)
            size = [len(r.faces) for r in regs]
            for x, y in sorted(pairs, key=lambda q: size[q[0]] + size[q[1]]):
                if x in taken or y in taken:
                    continue
                name = (min(names[x], names[y]), max(names[x], names[y]))
                if name in failed:
                    stats["pairs remembered"] += 1
                    continue
                stats["pairs tried"] += 1
                fit = fit_union(regs, [x, y], fixed, own, held, exact_at, try_for((x, y)))
                if fit is None:
                    failed.add(name)
                    continue
                stats["pairs joined"] += 1
                if fit[0].kind not in (regs[x].kind, regs[y].kind):
                    stats["joined as another kind"] += 1
                faces = np.concatenate([np.asarray(regs[i].faces, dtype=np.int64) for i in (x, y)])
                groups.append(([x, y], [Region(fit[0], faces, fit[1], False)]))
                taken.update((x, y))
            if not groups:
                break
            apply(groups)
        # --- what no simple surface holds: one freeform cell per cluster, when that is far fewer faces ---
        if POOL_FREEFORM:
            got = borders(freeform=True)
            if got is not None:
                pairs, names, fixed, own, held, exact_at = got
                regs = self.regions
                miss = max(tol, self.deflection)

                def held_by(cells, i):
                    """Whether the cells hold member i at its held vertices (its drawn flat's, its interior ones) within
                    POOL_WORSE times what its own surface held them to, or exact_at: a corner of each of its triangles
                    against the cell that triangle went to."""
                    fc = np.asarray(regs[i].faces, dtype=np.int64)
                    hv = held[i]
                    bound = max(POOL_WORSE * float(np.percentile(regs[i].prim.distance(V[hv]), POOL_EXACT_SHARE)), exact_at)
                    corners = F[fc]
                    sel = np.isin(corners, hv)
                    if not sel.any():
                        return True
                    cell_of = np.full(len(F), -1, dtype=np.int64)
                    for k, c in enumerate(cells):
                        cell_of[np.asarray(c.faces, dtype=np.int64)] = k
                    k_tri = np.repeat(cell_of[fc], 3).reshape(-1, 3)[sel]
                    P = V[corners[sel]]
                    d = np.empty(len(P))
                    for k in np.unique(k_tri):
                        at = k_tri == k
                        d[at] = cells[k].prim.distance(P[at]) if k >= 0 else np.inf
                    return float(np.percentile(d, POOL_EXACT_SHARE)) <= bound
                area = np.array([float(self.fa[np.asarray(r.faces, dtype=np.int64)].sum()) for r in regs])
                # (a piece that dwarfs the tiles beside it is a drawn face they run into, not one of them: it stays out,
                # and the tiles cluster without it)
                drawn = set()
                for members in clusters_of(pairs, len(regs)):
                    typical = float(np.median(area[members]))
                    drawn.update(i for i in members if area[i] >= FACET_DWARFS * typical
                                 and not self._strip_like(np.asarray(regs[i].faces, dtype=np.int64)))
                stats["dwarfs"] += len(drawn)
                pairs = [(x, y) for x, y in pairs if x not in drawn and y not in drawn]
                groups = []
                for members in clusters_of(pairs, len(regs)):
                    name = ("freeform",) + tuple(names[i] for i in members)
                    if name in failed:
                        stats["cells remembered"] += 1
                        continue
                    stats["cells tried"] += 1
                    members = list(members)
                    cells = None
                    for _ in range(POOL_LEAVE):
                        faces = np.concatenate([np.asarray(regs[i].faces, dtype=np.int64) for i in members])
                        cells = fit_freeform_cells(self, faces)
                        # (the cells follow the mesh at least as well as the tiles did, give or take)
                        close = min(miss, POOL_WORSE * max(own[i] for i in members))
                        if not cells or len(cells) > len(members) // POOL_FEWER or any(c.prim.dev > close for c in cells) \
                                or sum(len(c.faces) for c in cells) != len(faces):
                            cells = None
                            break
                        # (and every member as closely as its own surface held it, give or take, at the vertices its
                        # exactness is judged on: a cell that follows a cluster on the whole can still stand 1.5 mm off
                        # one tile (test13, tc_greaser: 17 of 66 tiles measured), and a drawn face among the tiles is
                        # held exactly or stays out; the members a cell doesn't hold leave, and the rest are tried again)
                        loose = {i for i in members if not held_by(cells, i)}
                        if not loose:
                            break
                        stats["members left out"] += len(loose)
                        members = [i for i in members if i not in loose]
                        cells = None
                        if len(members) < POOL_FEWER:
                            break
                    if cells is None:
                        failed.add(name)
                        continue
                    stats["cells joined"] += 1
                    groups.append((members, cells))
                if groups:
                    apply(groups)
        return self.pooled - before


def _radius_sigma(cyl, P):
    """The uncertainty of a cylinder's radius from the points it was fitted to: sigma^2 (J^T J)^-1 for the five parameters
    (two of the axis point across the axis, two of its direction, the radius), the Jacobian taken numerically on the
    signed radial residual, sigma from the residual itself. A tile spanning a small arc of a big cylinder has a radius
    that the points barely pin; a wide tile a well-known one. inf when the system is singular."""
    P = np.asarray(P, float)
    if len(P) < 7:
        return np.inf
    d = cyl.d / np.linalg.norm(cyl.d)
    e1 = np.cross(d, [1.0, 0.0, 0.0])
    if np.linalg.norm(e1) < 0.1:
        e1 = np.cross(d, [0.0, 1.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(d, e1)

    def residual(x):
        a = cyl.a + x[0] * e1 + x[1] * e2
        dd = d + x[2] * e1 + x[3] * e2
        dd = dd / np.linalg.norm(dd)
        v = P - a
        radial = np.linalg.norm(v - np.outer(v @ dd, dd), axis=1)
        return radial - (cyl.r + x[4])

    scale = max(float(cyl.r), 1e-3)
    h = np.array([1e-4 * scale, 1e-4 * scale, 1e-4, 1e-4, 1e-4 * scale])
    r0 = residual(np.zeros(5))
    J = np.empty((len(P), 5))
    for k in range(5):
        x = np.zeros(5)
        x[k] = h[k]
        J[:, k] = (residual(x) - r0) / h[k]
    dof = max(len(P) - 5, 1)
    sigma2 = float(r0 @ r0) / dof
    try:
        cov = np.linalg.inv(J.T @ J)
    except np.linalg.LinAlgError:
        return np.inf
    var = sigma2 * cov[4, 4]
    return float(np.sqrt(var)) if var > 0 else 0.0


def _off_its_limit(prim, cyls, regs, V, F, tol, detail=None):
    """Whether a torus or cone fitted over cylinder tiles stands off their geometry: the torus's tube radius, or the cone's
    radius at each tile, differs from the tile's own radius by more than POOL_SIGMA times that radius's own uncertainty
    (_radius_sigma), or the tolerance. detail, a list if given, gets (member r, radius there, sigma_r) per member."""
    off = False
    for c, r in zip(cyls, regs):
        fc = np.asarray(r.faces, dtype=np.int64)
        if prim.kind == "torus":
            rho = float(prim.r)
        else:
            p = V[F[fc]].mean((0, 1))
            rho = abs(float((p - prim.apex) @ prim.d)) * np.tan(prim.alpha)
        sig = _radius_sigma(c, V[np.unique(F[fc])])
        if detail is not None:
            detail.append((round(float(c.r), 3), round(rho, 3), round(sig, 4)))
        if abs(rho - c.r) > max(POOL_SIGMA * sig, tol):
            off = True
    return off


def _drawn_flats_in(faces, drawn_flat):
    """The drawn flats inside a piece: the exact facet groups fidelity.designed_flats calls planes (by its own floors in
    triangles and area, none of ours on top) that lie mostly in the piece. drawn_flat: (group of each triangle, which
    groups are planes, each group's size), the size optional."""
    if drawn_flat is None:
        return np.zeros(0, dtype=np.int64)
    sf, flat = drawn_flat[0], drawn_flat[1]
    total = drawn_flat[2] if len(drawn_flat) > 2 else np.bincount(sf, minlength=len(flat))
    here = np.bincount(sf[faces], minlength=len(flat))
    return np.nonzero(flat & (here > 0) & (2 * here >= total))[0]


def _held_vertices(seg, faces, drawn_flat):
    """The vertices of a piece that a surface must hold for the piece to be exact: those of a drawn flat inside it (an
    exact facet group fidelity.designed_flats calls a plane, by its own floors in triangles and area and none of ours on
    top, most of it in the piece), else its interior vertices (every triangle at the vertex is the piece's), else, when
    there are fewer than POOL_INTERIOR of those, all of them. drawn_flat: (group of each triangle, which groups are
    planes, each group's size), the size optional."""
    F = seg.F
    if drawn_flat is not None:
        drawn = _drawn_flats_in(faces, drawn_flat)
        if len(drawn):
            return np.unique(F[faces[np.isin(drawn_flat[0][faces], drawn)]])
    vid, counts = np.unique(F[faces], return_counts=True)
    deg = seg.__dict__.get("_vertex_degree")
    if deg is None:
        deg = seg._vertex_degree = np.bincount(F.ravel(), minlength=len(seg.V))
    inside = vid[counts == deg[vid]]
    return inside if len(inside) >= POOL_INTERIOR else vid


def _lies_on(prim, P, within):
    """Whether the points P lie on the surface to within `within`, POOL_EXACT_SHARE of them: a drawn face's piece
    carries a few stray vertices of its neighbours along its border."""
    return float(np.percentile(prim.distance(P), POOL_EXACT_SHARE)) <= within


def _cluster_components(faces, nb):
    from .freeform import _components

    return _components(faces, nb)


def fit_freeform_cells(seg, faces, max_rounds=6):
    """Split a face set into height-field disk cells and fit each with a
    B-spline; cells whose fit misses the tolerance are split again."""
    from .freeform import fit_cell, split_cells, _geodesic_split

    V, F = seg.V, seg.F
    out = []
    todo = split_cells(V, F, faces, seg.fn, seg.fa, seg.nb)
    rounds = 0
    cen = None
    while todo:
        cfaces, n = todo.pop()
        q = float(getattr(seg, "detail", 0.7))
        surf = fit_cell(V, F, cfaces, n, seg.tol, detail=q, max_ctrl=int(16 + 30 * q))
        if surf is None:
            continue
        if surf.dev > seg.tol and len(cfaces) > 8 and rounds < 400:
            rounds += 1
            if cen is None:
                cen = V[F].mean(1)
            parts = _geodesic_split(cfaces, cen, seg.nb, 2)
            if len(parts) >= 2:
                for p in parts:
                    todo.extend(split_cells(V, F, p, seg.fn, seg.fa, seg.nb))
                continue
        reg = Region(surf, cfaces, 1.0, False)
        out.append(reg)
    return out


# ----------------------------------------------------------------------------
# B-rep construction with analytic surfaces
# ----------------------------------------------------------------------------


def _dir(v):
    return gp_Dir(float(v[0]), float(v[1]), float(v[2]))


class CADAssembler(BRepAssembler):
    def __init__(self, V, rg, regions, tol, report=NULL):
        surfaces = [_SurfAdapter(r) for r in regions]
        super().__init__(np.array(V, dtype=np.float64, copy=True), rg, surfaces, tol, report)
        self.regions = regions
        self.sfe = ShapeFix_Edge()
        self.frames = {}
        self.seams = {}
        self.surf_params = {}
        self.fixed_closed = set()
        self.unseamed = set()
        self.sphere_mode = {}
        self.pole_caps = {}
        self.tube_seams = {}
        self.ext = float(np.linalg.norm(V.max(0) - V.min(0)))
        self.worked_out = {}
        self.fit_instead = set()

    # --- edges ----------------------------------------------------------
    def build_chain_edges(self, chain):
        L = self.regions[chain.left]
        R = self.regions[chain.right] if chain.right >= 0 else None
        both_planar = L.kind == "plane" and (R is None or R.kind == "plane")
        if EXACT_EDGES and not both_planar and self._exact_chain_edge(chain):
            return
        if len(chain.verts) == 2:
            return super().build_chain_edges(chain)
        if R is not None and (L.prim is R.prim or getattr(L.prim, "twin", None) is R.prim) and not chain.closed:
            # one surface cut in two (see split_pinched_regions, and the
            # two halves of a swept surface that goes all the way round,
            # SweptSurface.halves): the cut
            # can be any curve on it, so it runs smoothly from corner to
            # corner instead of zigzagging along the mesh's triangles (a
            # zigzag edge folds over itself and makes both faces invalid)
            if L.kind == "plane":
                if self._plane_cut_edges(chain, L.prim):
                    return
            e = self._on_surface_edge(chain, L.prim)
            if e is not None:
                chain.edges = [e]
                return
        if both_planar:
            if not chain.closed:
                P = self.V[chain.verts]
                a, b = P[0], P[-1]
                d = b - a
                L = np.linalg.norm(d)
                if L > MIN_TOL:
                    d = d / L
                    Q = P - a
                    off = np.linalg.norm(Q - np.outer(Q @ d, d), axis=1).max()
                    # (mesh noise along a long straight border, not a corner in a short one:
                    # a straight edge's corner tolerances grow with off, see LINE_BEND)
                    if off <= 5 * self.tol and off <= LINE_BEND * L:
                        e = self._line_edge(chain.verts[0], chain.verts[-1])
                        self._set_edge_tol(e, chain, off)
                        chain.edges = [e]
                        return
            return super().build_chain_edges(chain)
        if self.chain_is_straight(chain):
            return super().build_chain_edges(chain)
        P = self.V[chain.verts]
        # circle?
        circ = _fit_circle_3d(P[:-1] if chain.closed else P)
        if circ is not None and circ[3] <= self.tol:
            c, n, r, _ = circ
            c, n, r = self._snap_circle(chain, c, n, r)
            e = self._circle_edge(chain, c, n, r)
            if e is not None:
                chain.edges = [e]
                return
        e = self._bspline_edge(chain, P)
        if e is not None:
            chain.edges = [e]
            return
        chain.edges = self._polyline_edges(chain.verts)

    def _chain_prims(self, chain):
        """The distinct surfaces either side of a border (one, when one surface was cut in two)."""
        out = []
        for rid in (chain.left, chain.right):
            if rid >= 0:
                prim = self.regions[rid].prim
                if not any(prim is q or getattr(q, "twin", None) is prim for q in out):
                    out.append(prim)
        return out

    def solve_corners(self):
        """Puts each corner where its surfaces meet, before any edge is made from it (the points
        between corners are looked for only when an edge is laid: _exact_chain_edge). The mesh's
        points only come near the surfaces, and a file whose corners stand off their faces by hundredths of a millimetre
        reads as valid but can't be edited: Fusion finds its faces' meeting points anew for any
        change. A designer's file has them within a millionth of a millimetre."""
        rg = self.rg
        at = {}
        for c in rg.chains:
            for vi in (c.verts[0], c.verts[-1]):
                at.setdefault(vi, []).extend(self._chain_prims(c))
        reach = CORNER_REACH * self.tol
        moved = {}
        for vi, prims in at.items():
            seen = []
            for q in prims:
                if not any(q is s or getattr(s, "twin", None) is q for s in seen):
                    seen.append(q)
            x = _meet(self.V[vi], seen, reach)
            if x is not None:
                moved[vi] = x
        for c in rg.chains:
            # (a corner pair that would come together makes no edge: both stay as they were)
            if c.closed:
                continue
            a, b = c.verts[0], c.verts[-1]
            pa, pb = moved.get(a, self.V[a]), moved.get(b, self.V[b])
            if np.linalg.norm(pa - pb) <= 10 * MIN_TOL:
                moved.pop(a, None)
                moved.pop(b, None)
        for vi, x in moved.items():
            self.V[vi] = x

    def _exact_chain_edge(self, chain):
        """The edge between two surfaces as the line, circle or smooth curve where they meet,
        worked out from the surfaces between the solved corners (points are added where the mesh
        has too few to show the curve). False when there is no such sharp meeting line: faces
        joining smoothly, one surface cut in two, or an open border; those are left to the rest
        of build_chain_edges."""
        prims = self._chain_prims(chain)
        if chain.left < 0 or chain.right < 0 or len(prims) != 2:
            return False
        # (exact, it crossed its neighbour at a cusp: see _cusp_crossing)
        if (chain.verts[0], chain.verts[-1]) in self.fit_instead:
            return False
        # (a freeform surface is only fitted to within the tolerance, and a designer's own edges stand
        # off theirs too: working these out gains nothing, cost 300 of test14's 520 s in this pass, and a
        # narrow band laid this way broke on read-back and went to 44 flat pieces on main plate)
        if any(hasattr(q, "geom") for q in prims):
            return False
        P = self.V[chain.verts]
        P = P[np.r_[True, np.linalg.norm(np.diff(P, axis=0), axis=1) > MIN_TOL]]
        if chain.closed and len(P) < 4:
            return False
        if not chain.closed and len(P) < 2:
            return False
        # (the rounds build most borders again unchanged: each round took as long as the last)
        key = (tuple(chain.verts[:1]), tuple(chain.verts[-1:]), len(chain.verts), P[0].tobytes(), P[-1].tobytes(),
               _prim_key(prims[0]), _prim_key(prims[1]))
        was = self.worked_out.get(key)
        if was is not None:
            if was[0] == "none":
                return False
            if was[0] == "line":
                e = self._line_edge(chain.verts[0], chain.verts[-1])
                self._set_edge_tol(e, chain, EDGE_FIT)
                chain.edges = [e]
                return True
            if was[0] == "circle":
                e = self._circle_edge(chain, was[1], was[2], was[3], exact=True)
                if e is not None:
                    chain.edges = [e]
                    return True
            if was[0] == "curve":
                e = self._curve_edge(chain, was[1])
                if e is not None:
                    chain.edges = [e]
                    return True
            if was[0] == "ellipse":
                e = self._ellipse_edge(chain, was)
                if e is not None:
                    chain.edges = [e]
                    return True
        got = self._work_out_chain(chain, P, prims)
        self.worked_out[key] = got
        if got[0] == "none":
            return False
        if got[0] == "line":
            e = self._line_edge(chain.verts[0], chain.verts[-1])
            self._set_edge_tol(e, chain, EDGE_FIT)
        elif got[0] == "circle":
            e = self._circle_edge(chain, got[1], got[2], got[3], exact=True)
        elif got[0] == "ellipse":
            e = self._ellipse_edge(chain, got)
        else:
            e = self._curve_edge(chain, got[1])
        if e is None:
            self.worked_out[key] = ("none",)
            return False
        chain.edges = [e]
        return True

    def _ellipse_edge(self, chain, known):
        """The arc of a plane's ellipse across a cylinder, between the chain's corners."""
        from OCP.Geom import Geom_Ellipse
        from OCP.gp import gp_Elips

        _, c, n, x, major, minor = known
        P = self.V[chain.verts]
        y = np.cross(n, x)
        ang = np.unwrap(np.arctan2((P - c) @ y / minor, (P - c) @ x / major))
        if ang[-1] < ang[0]:
            n, y, ang = -n, -y, -ang
        gc = Geom_Ellipse(gp_Elips(gp_Ax2(_pnt(c), _dir(n), _dir(x)), float(major), float(minor)))
        v0, v1 = self.vertex(chain.verts[0]), self.vertex(chain.verts[-1])
        if chain.closed:
            mk = BRepBuilderAPI_MakeEdge(gc, v0, v0, 0.0, 2 * np.pi)
        else:
            if ang[-1] - ang[0] >= 2 * np.pi - 1e-6:
                return None
            mk = BRepBuilderAPI_MakeEdge(gc, v0, v1, float(ang[0]), float(ang[-1]))
        if not mk.IsDone():
            return None
        e = mk.Edge()
        self._set_edge_tol(e, chain, EDGE_FIT)
        return e

    def _curve_edge(self, chain, curve):
        v0, v1 = self.vertex(chain.verts[0]), self.vertex(chain.verts[-1])
        mk = BRepBuilderAPI_MakeEdge(curve, v0, v1, curve.FirstParameter(), curve.LastParameter())
        if not mk.IsDone():
            return None
        e = mk.Edge()
        self._set_edge_tol(e, chain, EDGE_FIT)
        return e

    def _work_out_chain(self, chain, P, prims):
        """What the border between two surfaces is: ("line",), ("circle", c, n, r), ("curve", curve)
        or ("none",) when it isn't a sharp meeting line (see _exact_chain_edge)."""
        reach = CORNER_REACH * self.tol
        touch = contact(prims[0], prims[1])
        if touch is not None:
            if touch[0] == "line" and not chain.closed:
                return ("line",)
            if touch[0] == "circle":
                return ("circle", touch[1], touch[2], touch[3])
            return ("none",)
        if _meeting_angle(P, prims) < EDGE_TANGENT_DEG:
            return ("none",)
        # (a plane through a cylinder, cone or sphere, or two surfaces on one axis, meet in a known
        # curve: a fitted one misses the corners by enough to read back as crossing its neighbour)
        known = _known_curve(prims, P)
        if known is not None and float(_curve_gap(known, P)) <= reach:
            return known
        if not chain.closed:
            # (most are straight: the middle of the chord settles that, at no cost)
            a, b = P[0], P[-1]
            if np.linalg.norm(b - a) <= MIN_TOL:
                return ("none",)
            ok = True
            for s in EDGE_LINE_AT:
                m = a + s * (b - a)
                x = _meet(m, prims, reach)
                if x is None or float(np.linalg.norm(x - m)) > EDGE_FIT:
                    ok = False
                    break
            if ok:
                return ("line",)
        # (the corners stay as solved; the points between them are searched for afresh)
        if len(P) > EDGE_START:
            P = P[np.unique(np.r_[np.linspace(0, len(P) - 1, EDGE_START).astype(int), len(P) - 1])]
        Q = _densify(P, prims, reach)
        if Q is None:
            return ("none",)
        circ = _fit_circle_3d(Q[:-1] if chain.closed else Q)
        if circ is not None and circ[3] <= EDGE_FIT:
            return ("circle", circ[0], circ[1], circ[2])
        curve = self._meeting_curve(chain, Q, prims, reach)
        return ("none",) if curve is None else ("curve", curve)

    def _meeting_curve(self, chain, Q, prims, reach):
        """A smooth curve through points on both surfaces, with more points where it strays
        between them. None when it can't be laid (the points double back)."""
        from OCP.GeomAPI import GeomAPI_Interpolate
        from OCP.collections import HArray1_gp_Pnt

        curve = None
        for _ in range(5):
            if len(Q) > EDGE_POINTS:
                break
            turn = np.diff(Q, axis=0)
            turn = turn / np.maximum(np.linalg.norm(turn, axis=1), 1e-30)[:, None]
            if len(turn) > 1 and float((turn[:-1] * turn[1:]).sum(axis=1).min()) < 0.3:
                return None     # (doubles back: no smooth curve goes through these)
            pts = Q[:-1] if chain.closed else Q
            h = HArray1_gp_Pnt(1, len(pts))
            for i, p in enumerate(pts):
                h.SetValue(i + 1, _pnt(p))
            try:
                it = GeomAPI_Interpolate(h, bool(chain.closed), 1e-9)
                it.Perform()
            except Exception as fault:
                faults.caught(fault)
                return None
            if not it.IsDone():
                return None
            curve = it.Curve()
            n = len(Q) - 1
            t0, t1 = curve.FirstParameter(), curve.LastParameter()
            # (between points the curve's parameter runs by chord length, near enough)
            chord = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))]
            ts = t0 + (t1 - t0) * (chord[:-1] + chord[1:]) / (2 * chord[-1])
            M = np.array([[curve.Value(float(t)).X(), curve.Value(float(t)).Y(), curve.Value(float(t)).Z()] for t in ts])
            Mx = [_meet(m, prims, reach) for m in M]
            off = np.array([np.linalg.norm(m - x) if x is not None else 0.0 for m, x in zip(M, Mx)])
            if off.max() <= EDGE_FIT:
                break
            out = [Q[0]]
            for i in range(n):
                if off[i] > EDGE_FIT:
                    out.append(Mx[i])
                out.append(Q[i + 1])
            Q = np.array(out)
        return curve

    def _plane_cut_edges(self, chain, plane):
        """The cut between two pieces of one plane: a straight line from corner to corner when
        that passes clear of everything else on the two pieces, or else the mesh's own path,
        which can't cross anything. The line, taken blindly, ran through a standoff on Rak Uni's
        floor and past others, leaving them on the wrong piece: the pieces were invalid and
        became freeform patches. Returns False when neither will do (the path folds over itself)."""
        n = plane.n
        u = np.cross(n, [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0])
        u /= np.linalg.norm(u)
        v = np.cross(n, u)

        def flat(P):
            return np.c_[P @ u, P @ v]

        path = flat(self.V[chain.verts])
        a, b = path[0], path[-1]
        d = b - a
        length = float(np.linalg.norm(d))
        if length <= MIN_TOL:
            return False
        ends = {chain.verts[0], chain.verts[-1]}
        others = []
        for r in {chain.left, chain.right}:
            for loop in self.rg.region_loops[r]:
                for cid, _ in loop:
                    if cid != chain.id:
                        others.append(self.rg.chains[cid].verts)
        clear = True
        margin = CUT_CLEARANCE * self.tol
        for verts in others:
            Q = flat(self.V[verts])
            # (a corner of something else within reach of the line)
            t = np.clip((Q - a) @ d / (length * length), 0.0, 1.0)
            near = np.linalg.norm(Q - (a + t[:, None] * d), axis=1) <= margin
            if any(k and verts[i] not in ends for i, k in enumerate(near)):
                clear = False
                break
            # (an edge of it across the line)
            s1 = (Q[:-1] - a) @ np.array([-d[1], d[0]])
            s2 = (Q[1:] - a) @ np.array([-d[1], d[0]])
            cross = np.nonzero(s1 * s2 < 0)[0]
            if len(cross):
                at = Q[cross] + (Q[cross + 1] - Q[cross]) * (s1[cross] / (s1[cross] - s2[cross]))[:, None]
                t = (at - a) @ d / (length * length)
                if ((t > 1e-9) & (t < 1.0 - 1e-9)).any():
                    clear = False
                    break
            # (something else between the line and the path: it would change pieces)
            if len(path) > 2 and _inside_polygon(Q, path).any():
                clear = False
                break
        if clear:
            e = self._line_edge(chain.verts[0], chain.verts[-1])
            Q = path - a
            off = float(np.abs(Q @ np.array([-d[1], d[0]])).max()) / length
            # (the chord only has to lie on the plane; how far it strays from the mesh's path
            # doesn't matter, both pieces being one surface)
            self._set_edge_tol(e, chain, min(off, self.tol))
            chain.edges = [e]
            return True
        if _polyline_folds(path):
            return False
        chain.edges = self._polyline_edges(chain.verts)
        return True

    def _on_surface_edge(self, chain, prim, n=24):
        """The straight line between a chain's end corners, laid onto the
        surface, as one smooth edge (None when that doesn't work: the line
        would pass through the surface's axis, say)."""
        a, b = self.V[chain.verts[0]], self.V[chain.verts[-1]]
        if np.linalg.norm(b - a) <= MIN_TOL:
            return None
        t = np.linspace(0.0, 1.0, n)[:, None]
        P = _project(prim, a + t * (b - a))
        if P is None or not np.isfinite(P).all():
            return None
        # a smooth curve: neighbouring samples stay evenly spaced (no jump
        # across the surface where the line ran through its axis)
        seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
        if seg.max() > 3.0 * max(seg.mean(), MIN_TOL):
            return None
        P[0], P[-1] = a, b
        curve, dev = fit_chain_curve(P, self.tol)
        if curve is None:
            return None
        S = _sample_curve(curve, 4 * n)
        off = float(prim.distance(S).max())
        if off > self.tol:
            return None
        v0, v1 = self.vertex(chain.verts[0]), self.vertex(chain.verts[-1])
        mk = BRepBuilderAPI_MakeEdge(curve, v0, v1, curve.FirstParameter(), curve.LastParameter())
        if not mk.IsDone():
            return None
        e = mk.Edge()
        self._set_edge_tol(e, chain, max(dev, off))
        return e

    def _set_edge_tol(self, e, chain, curve_dev):
        t = max(MIN_TOL, curve_dev * 1.5 + 1e-9)
        for vi in (chain.verts[0], chain.verts[-1]):
            self.vtol[vi] = max(self.vtol[vi], t)
        self.B.UpdateEdge(e, t)
        for vi in (chain.verts[0], chain.verts[-1]):
            self.B.UpdateVertex(self.vertex(vi), max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9))

    def _snap_circle(self, chain, c, n, r):
        """A circle around the axis of an adjacent cylinder or cone is one of
        that surface's own circles: take its exact axis and radius, so the
        edge lies exactly on the surface and its start lines up exactly with
        the face's seam (tiny holes and pins otherwise come out slightly
        crossed when other programs read the file)."""
        if not chain.closed:
            return c, n, r  # a short arc's fitted axis is too uncertain
        for rid in (chain.left, chain.right):
            if rid < 0:
                continue
            prim = self.regions[rid].prim
            if prim.kind not in ("cylinder", "cone"):
                continue
            d = prim.d
            if abs(float(n @ d)) < np.cos(np.radians(2.0)):
                continue
            if prim.kind == "cylinder":
                h = float((c - prim.a) @ d)
                c2, r2 = prim.a + h * d, prim.r
            else:
                h = float((c - prim.apex) @ d)
                if h <= 0:
                    continue
                c2, r2 = prim.apex + h * d, h * np.tan(prim.alpha)
            if np.linalg.norm(c2 - c) > self.tol or abs(r2 - r) > self.tol:
                continue
            return c2, (d if float(n @ d) > 0 else -d), float(r2)
        return c, n, r

    def _circle_edge(self, chain, c, n, r, exact=False):
        P = self.V[chain.verts]
        # orient circle normal so that the chain runs counter-clockwise
        u = unit(P[0] - c - ((P[0] - c) @ n) * n)
        vv = np.cross(n, u)
        ang = np.unwrap(np.arctan2((P - c) @ vv, (P - c) @ u))
        if ang[-1] < ang[0]:
            n = -n
            vv = -vv
            ang = -ang
        ax = gp_Ax2(_pnt(c), _dir(n), _dir(u))
        circ = gp_Circ(ax, float(r))
        from OCP.Geom import Geom_Circle

        gc = Geom_Circle(circ)
        dev = float(np.abs(np.linalg.norm((P - c) - np.outer((P - c) @ n, n), axis=1) - r).max())
        dev = max(dev, float(np.abs((P - c) @ n).max()))
        v0 = self.vertex(chain.verts[0])
        if chain.closed:
            span = ang[-1] - ang[0]
            if abs(span - 2 * np.pi) > 0.3:
                return None
            # vertex position may not be exactly on the circle
            self.vtol[chain.verts[0]] = max(self.vtol[chain.verts[0]], dev)
            self.B.UpdateVertex(v0, max(MIN_TOL, 1.5 * self.vtol[chain.verts[0]] + 1e-9))
            mk = BRepBuilderAPI_MakeEdge(gc, v0, v0, 0.0, 2 * np.pi)
        else:
            v1 = self.vertex(chain.verts[-1])
            a0, a1 = float(ang[0]), float(ang[-1])
            if a1 - a0 > 2 * np.pi - 1e-6:
                return None
            # (an arc turning next to nothing is a line taken for a circle: three points of a plane's
            # meeting with a cylinder, 5 mm long, gave radius 43 m on Rak N. Its parameter range is too
            # small for OpenCascade to place on the cylinder, and settling then measured 5.6 m of
            # tolerance, which wrecked the floor it bounds. What can't be an arc falls to a line or a curve)
            if a1 - a0 < ARC_MIN_TURN:
                return None
            for vi in (chain.verts[0], chain.verts[-1]):
                self.vtol[vi] = max(self.vtol[vi], dev)
                self.B.UpdateVertex(self.vertex(vi), max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9))
            mk = BRepBuilderAPI_MakeEdge(gc, v0, v1, a0, a1)
        if not mk.IsDone():
            return None
        # an arc through the points can still bulge away between them
        span = (2 * np.pi) if chain.closed else (ang[-1] - ang[0])
        a0 = 0.0 if chain.closed else float(ang[0])
        ts = np.linspace(a0, a0 + span, max(24, 3 * len(P)))
        S = c + r * (np.outer(np.cos(ts), u) + np.outer(np.sin(ts), vv))
        # (an arc worked out from the surfaces may stand off the mesh's coarse chords, but not as far as
        # the fit check calls a face stray: xLCD cover's octagonal pocket passed for a cylinder, whose
        # arc stood 0.45 mm off a flat side; the flat base it bounds then went to B-spline cells)
        from .fidelity import Verdict

        room = (Verdict.P999_K if exact else 1.0) * self.tol
        if _polyline_distance(S, P) > room:
            return None
        e = mk.Edge()
        self._set_edge_tol(e, chain, dev)
        return e

    def _bspline_edge(self, chain, P):
        """Smooth curve through the chain points. Candidates are checked
        against the actual polyline (curve samples -> nearest segment) so an
        approximation that overshoots between points is never used."""
        curve, dev = fit_chain_curve(P, self.tol)
        if curve is None:
            return None
        v0 = self.vertex(chain.verts[0])
        v1 = self.vertex(chain.verts[-1])
        ends = [curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())]
        for vi, pe in zip((chain.verts[0], chain.verts[-1]), ends):
            d = pe.Distance(_pnt(self.V[vi]))
            self.vtol[vi] = max(self.vtol[vi], d, dev)
            self.B.UpdateVertex(self.vertex(vi), max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9))
        mk = BRepBuilderAPI_MakeEdge(curve, v0, v1, curve.FirstParameter(), curve.LastParameter())
        if not mk.IsDone():
            return None
        e = mk.Edge()
        self._set_edge_tol(e, chain, dev)
        return e

    # --- seams on periodic surfaces --------------------------------------
    #
    # A face that wraps all the way around a cylinder, cone or torus needs a
    # seam edge: one wire made of [bottom loop, seam, top loop, seam
    # reversed]. Seams are planned before any edge is built so that the seam
    # end points can become real vertices on the (shared) boundary chains.

    def edges_made(self):
        """Fitted curves leaving a corner at a shallow angle can cross their neighbour before any face is
        made: such a curve follows the mesh, whose own edges cross nothing, as far as the crossing.
        Done here, the faces are built once, from edges that don't cross. (Rak Uni's 3.8 mm2 flat
        face: a spline 0.02 mm off the mesh crossed the arc beside it 0.49 mm along, and
        OpenCascade's mesher dropped the face, though it reads valid.)
        The look is in the surface's tangent plane at the corner, which is true only near it: far
        round a curved surface two edges cross there that never meet. So a crossing counts when the
        two are within the tolerance of each other at it (on 341board 338 of 349 looked crossed
        from 0.33 mm or more away, none between, and 334 of its 1,012 borders became 24,361 lines:
        each build took 11 minutes in place of one). The rest of the edge stays a fitted curve."""
        for _ in range(CORNER_ROUNDS):
            if not self._corners_where_edges_cross():
                break
        done = {}
        for vi, rid, lines in self._edges_at_corners():
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    A, B = lines[i], lines[j]
                    if A[0] is B[0] or not (A[3] or B[3]):
                        continue
                    for i_a, i_b, t, w in _line_crossings(A[2], B[2]):
                        pa = A[4][i_a] + t * (A[4][i_a + 1] - A[4][i_a])
                        pb = B[4][i_b] + w * (B[4][i_b + 1] - B[4][i_b])
                        if np.linalg.norm(pa - pb) > self.tol:
                            continue
                        for line, i_x, f_x in ((A, i_a, t), (B, i_b, w)):
                            if line[3]:
                                reach = line[5][i_x] + f_x * (line[5][i_x + 1] - line[5][i_x])
                                done.setdefault(id(line[0]), [line[0], 0.0, 0.0])
                                k = 1 if line[1] == 0 else 2
                                done[id(line[0])][k] = max(done[id(line[0])][k], float(reach))
        for c, from_start, from_end in done.values():
            c.edges = self._follows_mesh_near_ends(c, from_start, from_end)

    def _edges_at_corners(self):
        """Each corner of each region, with the edges of that region leaving it, as lines of points packed towards the corner
        in the surface's tangent plane there: (corner, region, [(border, its end, points in the plane, fitted curve?,
        points from the corner, length along them)])."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_BSplineCurve

        at = {}
        for c in self.rg.chains:
            if c.closed or not c.edges:
                continue
            for end in (0, -1):
                for rid in (c.left, c.right):
                    if rid >= 0:
                        at.setdefault((c.verts[end], rid), []).append((c, end))
        s = np.geomspace(CROSS_NEAR, 1.0, CROSS_POINTS)
        for (vi, rid), here in at.items():
            if len(here) < 2:
                continue
            f = _foot(self.regions[rid].prim, self.V[vi])
            if f is None:
                continue
            u, v = perp_basis(f[1])
            lines = []
            for c, end in here:
                ca = BRepAdaptor_Curve(c.edges[end])
                a, b = ca.FirstParameter(), ca.LastParameter()
                ts = a + (b - a) * s if end == 0 else b - (b - a) * s
                Q = np.array([[p.X(), p.Y(), p.Z()] for p in (ca.Value(float(w)) for w in ts)]) - self.V[vi]
                along = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))]
                lines.append((c, end, np.c_[Q @ u, Q @ v], ca.GetType() == GeomAbs_BSplineCurve, Q, along))
            yield vi, rid, lines

    def _corners_where_edges_cross(self):
        """Two exact edges leaving a corner nearly tangent can really cross just beside it: the corner is
        solved only along the directions its surfaces pin down (_meet), which leaves it short of where its
        edges meet, and a surface fitted a hair into its neighbour cuts it twice, either side of the tangent point.
        xlcd-cover's floor: a straight edge and an ellipse crossed 0.088 mm from their corner, 0.0004 mm apart. The
        crossing is where the corner is: it goes there when the two run within the tolerance of each other all
        the way to it, so nothing moves across them, and it is on all the corner's surfaces within the
        tolerance. Every edge at the corner is laid again from it. A large tolerance hides such a crossing
        in memory, but a STEP file keeps no tolerances, so it comes back when the file is read. (A corner
        between two such meetings, one on each side of a pinch, can't go to both: it stays, as it would
        only put one of them further off. Returns whether any corner moved.)"""
        moves = {}
        torn = set()
        for vi, rid, lines in self._edges_at_corners():
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    A, B = lines[i], lines[j]
                    if A[0] is B[0] or A[3] or B[3]:
                        continue
                    for off in _crossings_beside(A[2], A[4], B[2], B[4], self.tol):
                        x = self.V[vi] + off
                        reach = float(np.linalg.norm(off))
                        if vi in moves and float(off @ (moves[vi][1] - self.V[vi])) < 0.0:
                            torn.add(vi)
                        if vi not in moves or reach < moves[vi][0]:
                            moves[vi] = (reach, x)
        moved = {}
        for vi, (reach, x) in moves.items():
            if vi in torn:
                continue
            prims = []
            for c in self.rg.chains:
                if vi in (c.verts[0], c.verts[-1]):
                    prims += self._chain_prims(c)
            feet = [_foot(q, x) for q in prims]
            if all(f is None or np.linalg.norm(f[0] - x) <= self.tol for f in feet):
                moved[vi] = x
        if not moved:
            return False
        for vi, x in moved.items():
            self.V[vi] = x
            self._verts.pop(vi, None)
        for c in self.rg.chains:
            if c.verts[0] in moved or c.verts[-1] in moved:
                self.chain_edges_or_fail(c)
        return True

    def _follows_mesh_near_ends(self, chain, from_start, from_end):
        """A border's edges following the mesh for these lengths from its two ends (up to the first of
        its points past them), and a fitted curve between."""
        P = self.V[chain.verts]
        run = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
        i = int(np.searchsorted(run, from_start)) if from_start > 0.0 else 0
        j = int(np.searchsorted(run, run[-1] - from_end, side="right")) - 1 if from_end > 0.0 else len(P) - 1
        if j - i < 2:
            return self._polyline_edges(chain.verts)
        middle = SimpleNamespace(verts=chain.verts[i:j + 1])
        e = self._bspline_edge(middle, P[i:j + 1])
        if e is None:
            return self._polyline_edges(chain.verts)
        edges = self._polyline_edges(chain.verts[:i + 1]) if i > 0 else []
        edges.append(e)
        if j < len(P) - 1:
            edges += self._polyline_edges(chain.verts[j:])
        return edges

    def _unfold_chains(self):
        """A border that runs out and straight back along itself, round an arm of a region with no
        width, encloses nothing, and leaves it out. Left in, OpenCascade's mesher leaves out whatever
        lies behind the fold and says nothing: on Rak Uni a 3.6 mm fold in the floor's outline cost
        183 mm2 of its triangles, though the face reads valid and no crossing shows."""
        cos_fold = np.cos(np.radians(180.0 - FOLD_DEG))
        for c in self.rg.chains:
            v = list(c.verts)
            k = 1
            while 0 < k < len(v) - 1:
                a, b = self.V[v[k]] - self.V[v[k - 1]], self.V[v[k + 1]] - self.V[v[k]]
                la, lb = np.linalg.norm(a), np.linalg.norm(b)
                if la > MIN_TOL and lb > MIN_TOL and float(a @ b) <= cos_fold * la * lb:
                    del v[k]
                    k = max(1, k - 1)
                else:
                    k += 1
            c.verts = v

    def build(self, progress=None):
        self._unfold_chains()
        self.split_thin_loops()
        if EXACT_EDGES:
            self.solve_corners()
        self.plan_seams()
        if TURN_SNAP:
            self.snap_turn_cuts()
        return super().build(progress)

    def snap_turn_cuts(self):
        """The corners of a cut between two turns of one surface (split_turns)
        are put exactly on the meridian it was cut at, so the edge between them
        is that meridian's line (_on_surface_edge) and lies on the seam of both
        faces: a face a whole turn wide has no room for its seam anywhere else."""
        for c in self.rg.chains:
            if c.closed or c.left < 0 or c.right < 0:
                continue
            L, R = self.regions[c.left], self.regions[c.right]
            if L.cut is None or L.cut is not R.cut or L.prim is not R.prim:
                continue
            fr = self.region_frame(c.left)
            for vi in (c.verts[0], c.verts[-1]):
                self.V[vi] = fr.at_angle(self.snap_to_surface(c.left, self.V[vi]), 0.0)

    def add_point(self, p):
        self.V = np.vstack([self.V, np.asarray(p, dtype=np.float64)[None]])
        self.vtol = np.r_[self.vtol, 0.0]
        return len(self.V) - 1

    def region_frame(self, r):
        fr = self.frames.get(r)
        if fr is not None:
            return fr
        reg = self.regions[r]
        prim = reg.prim
        P = self.V[np.unique(self.rg.F[reg.faces])]
        if prim.kind == "cylinder":
            o, d = prim.a, prim.d
        elif prim.kind == "cone":
            o, d = prim.apex, prim.d
        elif prim.kind == "torus":
            o, d = prim.c, prim.d
        elif prim.kind == "sphere":
            return self._sphere_frame(r, P)
        else:
            return None
        # (one turn of several: its seam on the meridian it was cut at)
        fr = _Frame(o, d, reg.cut if reg.cut is not None else self._seam_dir(P, o, d))
        self.frames[r] = fr
        return fr

    def _sphere_frame(self, r, P):
        """Small sphere patches get their poles pushed out of the way; big
        caps and bands get the axis of their largest boundary loop so the
        boundary wraps around it (handled with a seam / pole edge)."""
        prim = self.regions[r].prim
        c = prim.c
        dirs = P - c
        dirs /= np.maximum(np.linalg.norm(dirs, axis=1), 1e-30)[:, None]
        m = unit(dirs.mean(0)) if np.any(dirs.mean(0)) else np.array([0, 0, 1.0])
        spread = float(np.degrees(np.arccos(np.clip(dirs @ m, -1, 1)).max())) if len(dirs) else 0.0
        loops = self.rg.region_loops[r]
        if loops and (spread > 70.0 or np.linalg.norm(dirs.mean(0)) < 0.3):
            big = max(loops, key=lambda l: len(self.rg.loop_vertices(l)))
            L = self.V[self.rg.loop_vertices(big)]
            if len(L) >= 3:
                Lc = L.mean(0)
                _, _, vt = np.linalg.svd(L - Lc, full_matrices=False)
                n = vt[2]
                if np.dot(n, m) < 0:
                    n = -n
                fr = _Frame(c, n, self._seam_dir(P, c, n))
                self.sphere_mode[r] = "axial"
                self.frames[r] = fr
                return fr
        u, v = perp_basis(m)
        fr = _Frame(c, u, -m)
        self.sphere_mode[r] = "patch"
        self.frames[r] = fr
        return fr

    def loop_anchors(self, loop):
        out = []
        for cid, fwd in loop:
            c = self.rg.chains[cid]
            if c.closed and cid not in self.fixed_closed:
                continue
            out.append(c.verts[0])
            out.append(c.verts[-1])
        return list(dict.fromkeys(out))

    def plan_seams(self):
        rg = self.rg
        for r, reg in enumerate(self.regions):
            if reg.kind not in PERIODIC and reg.kind != "sphere":
                continue
            loops = rg.region_loops[r]
            if not loops:
                continue
            fr = self.region_frame(r)
            if reg.kind == "sphere" and self.sphere_mode.get(r) != "axial":
                continue
            info = []
            for li, loop in enumerate(loops):
                vs = rg.loop_vertices(loop)
                ang = np.unwrap(fr.angle(self.V[vs + vs[:1]]))
                w = int(np.round((ang[-1] - ang[0]) / (2 * np.pi)))
                info.append((li, w, ang))
            wrap = [x for x in info if x[1] != 0]
            if not wrap:
                if reg.kind == "torus":
                    self._plan_tube_seam(r, loops, fr)
                continue
            # one loop round the axis: a cap round the sphere's pole or the
            # cone's apex (a face with no seam there is invalid)
            if reg.kind in ("sphere", "cone") and len(wrap) == 1:
                self._plan_pole_cap(r, loops, info, wrap[0][0], fr)
                continue
            if len(wrap) != 2:
                self.unseamed.add(r)
                continue
            forb = [(a.min() - 0.03, a.max() + 0.03) for (_, w, a) in info if w == 0]

            def allowed(a):
                for lo, hi in forb:
                    k = np.floor((a - lo) / (2 * np.pi))
                    aa = a - k * 2 * np.pi
                    if lo <= aa <= hi:
                        return False
                return True

            def clearance(a):
                if not forb:
                    return np.pi
                best = np.pi
                for lo, hi in forb:
                    for edge in (lo, hi):
                        dd = abs(_wrap(a - edge))
                        best = min(best, dd)
                return best

            A, Bl = wrap[0][0], wrap[1][0]
            anchors = {A: self.loop_anchors(loops[A]), Bl: self.loop_anchors(loops[Bl])}
            lines = self._seam_lines(r, [rg.loop_vertices(loops[A]), rg.loop_vertices(loops[Bl])], [wrap[0][2], wrap[1][2]])
            cand = []
            for L in (A, Bl):
                other = Bl if L == A else A
                oang = [fr.angle(self.V[v][None])[0] for v in anchors[other]]
                for v in anchors[L]:
                    a = fr.angle(self.V[v][None])[0]
                    if not allowed(a):
                        continue
                    comp = (not anchors[other]) or any(abs(_wrap(b - a)) < 1e-9 for b in oang)
                    cand.append((comp, clearance(a), a, _seam_score(lines, a)))
            if cand:
                cand.sort(key=lambda t: (t[3] < SEAM_SHALLOW, not t[0], -t[1]))
                u0 = cand[0][2]
            else:
                vs = rg.loop_vertices(loops[A])
                a = fr.angle(self.V[vs[:1]])[0]
                u0 = a if allowed(a) else _best_gap(forb)
            if _seam_score(lines, u0) < SEAM_SHALLOW:
                # (no corner will do: a clear meridian takes a new corner on each loop)
                scored = [(_seam_score(lines, a), a) for a in np.linspace(-np.pi, np.pi, SEAM_LOOKS, endpoint=False) if allowed(a)]
                if scored and max(scored)[0] >= SEAM_SHALLOW:
                    u0 = float(max(scored)[1])
            sv = []
            for L, other in ((A, Bl), (Bl, A)):
                v = self.ensure_seam_vertex(r, loops[L], u0, fr, self.V[rg.loop_vertices(loops[other])].mean(axis=0))
                if v is None:
                    break
                sv.append(v)
            if len(sv) != 2 or sv[0] == sv[1]:
                self.unseamed.add(r)
                continue
            self.frames[r] = fr.rotated(u0)
            self.seams[r] = (A, Bl, sv[0], sv[1])

    def _seam_lines(self, r, loops, angles):
        """For _seam_score, each loop round the axis as (angle round the axis, height along it, and radius from it, at each
        of its points, in the loop's order and closed), the height in millimetres."""
        prim = self.regions[r].prim
        d = self.region_frame(r).d
        at = {"cylinder": "a", "cone": "apex", "torus": "c", "sphere": "c"}[prim.kind]
        out = []
        for vs, ang in zip(loops, angles):
            P = self.V[vs + vs[:1]]
            rel = P - getattr(prim, at)
            h = rel @ d
            rho = np.linalg.norm(rel - np.outer(h, d), axis=1)
            if prim.kind == "torus":
                h = prim.r * np.unwrap(np.arctan2(h, rho - prim.R))
            out.append((ang, h, rho))
        if prim.kind == "torus":
            # (the two loops on one branch of the tube's angle)
            turn = 2 * np.pi * prim.r
            a, b = out
            out[1] = (b[0], b[1] - turn * np.round((b[1].mean() - a[1].mean()) / turn), b[2])
        return out

    def _seam_lines_to_pole(self, r, vs, ang, fr):
        """For _seam_score, a loop round a cone's apex or a sphere's pole, and the pole as the seam's other end: a line
        with no radius that every meridian crosses once. Also returns the pole's position."""
        prim = self.regions[r].prim
        d = fr.d
        at = prim.apex if prim.kind == "cone" else prim.c
        pole = at if prim.kind == "cone" else prim.c + prim.r * d
        P = self.V[vs + vs[:1]]
        rel = P - at
        h = rel @ d
        rho = np.linalg.norm(rel - np.outer(h, d), axis=1)
        # (the pole on the side of the loop the face covers: a cone's apex is at height 0, a sphere's at its radius)
        high = float((pole - at) @ d)
        start = float(ang[0])
        return [(ang, h, rho), (np.array([start, start + 2 * np.pi]), np.array([high, high]), np.zeros(2))], pole

    def _plan_pole_cap(self, r, loops, info, A, fr):
        forb = [(a.min() - 0.03, a.max() + 0.03) for (_, w, a) in info if w == 0]
        anchors = self.loop_anchors(loops[A])
        vs = self.rg.loop_vertices(loops[A])
        lines, pole = self._seam_lines_to_pole(r, vs, np.unwrap(fr.angle(self.V[vs + vs[:1]])), fr)

        def allowed(a):
            for lo, hi in forb:
                k = np.floor((a - lo) / (2 * np.pi))
                if lo <= a - k * 2 * np.pi <= hi:
                    return False
            return True

        u0 = None
        fits = [fr.angle(self.V[v][None])[0] for v in anchors]
        fits = [a for a in fits if allowed(a)]
        clear = [a for a in fits if _seam_score(lines, a) >= SEAM_SHALLOW]
        if clear or fits:
            u0 = (clear or fits)[0]
        if u0 is None:
            if anchors and forb:
                self.unseamed.add(r)
                return
            u0 = fr.angle(self.V[vs[:1]])[0] if not forb else _best_gap(forb)
        if _seam_score(lines, u0) < SEAM_SHALLOW:
            scored = [(_seam_score(lines, a), a) for a in np.linspace(-np.pi, np.pi, SEAM_LOOKS, endpoint=False) if allowed(a)]
            if scored and max(scored)[0] >= SEAM_SHALLOW:
                u0 = float(max(scored)[1])
        sv = self.ensure_seam_vertex(r, loops[A], u0, fr, pole)
        if sv is None:
            self.unseamed.add(r)
            return
        self.frames[r] = fr.rotated(u0)
        self.pole_caps[r] = (A, sv)

    # --- a seam round a torus's tube ----------------------------------
    #
    # A torus face that goes all the way round its tube but only part way
    # round its axis (a bent rod, a bent pipe's bore: stl2curves hands these
    # over) is bounded by two loops round the tube. Like a face round the axis,
    # it needs a seam: here along one of the torus's parallels (a circle round
    # the axis at one tube angle v0), giving one wire [seam, loop, seam
    # reversed, loop]. Without it the face was invalid and became a few dozen
    # freeform cells (a quarter ring: 24 faces instead of 3).

    def _tube_angle(self, r, P):
        """The angle round the tube (the torus's v) of points P."""
        prim = self.regions[r].prim
        rel = np.atleast_2d(P) - prim.c
        h = rel @ prim.d
        rho = np.linalg.norm(rel - np.outer(h, prim.d), axis=1)
        return np.arctan2(h, rho - prim.R)

    def _tube_turns(self, r, loop):
        vs = self.rg.loop_vertices(loop)
        a = np.unwrap(self._tube_angle(r, self.V[vs + vs[:1]]))
        return int(np.round((a[-1] - a[0]) / (2 * np.pi)))

    def _on_parallel(self, r, p, v0):
        """Point p moved round the tube onto the parallel at tube angle v0."""
        prim = self.regions[r].prim
        fr = self.frames[r]
        u = fr.angle(np.asarray(p, dtype=np.float64)[None])[0]
        radial = np.cos(u) * fr.x + np.sin(u) * fr.y
        return prim.c + (prim.R + prim.r * np.cos(v0)) * radial + prim.r * np.sin(v0) * prim.d

    def _plan_tube_seam(self, r, loops, fr):
        turns = [self._tube_turns(r, loop) for loop in loops]
        wrap = [i for i, w in enumerate(turns) if w != 0]
        if not wrap:
            return
        if len(wrap) != 2:
            self.unseamed.add(r)
            return
        A, Bl = wrap
        # the parallel: through a corner of loop A clear of every other loop
        forb = []
        for i, loop in enumerate(loops):
            if i not in wrap:
                a = np.unwrap(self._tube_angle(r, self.V[self.rg.loop_vertices(loop)]))
                forb.append((a.min() - 0.03, a.max() + 0.03))

        def allowed(a):
            return all(not (lo <= lo + np.mod(a - lo, 2 * np.pi) <= hi) for lo, hi in forb)

        lines = self._tube_seam_lines(r, fr, [loops[A], loops[Bl]])
        cand = [float(self._tube_angle(r, self.V[v])[0]) for v in self.loop_anchors(loops[A])]
        cand = [a for a in cand if allowed(a)]
        clear = [a for a in cand if _seam_score(lines, a) >= SEAM_SHALLOW]
        if clear or cand:
            v0 = (clear or cand)[0]
        elif forb:
            v0 = float(_best_gap(forb))
        else:
            v0 = float(self._tube_angle(r, self.V[self.rg.loop_vertices(loops[A])[:1]])[0])
        if _seam_score(lines, v0) < SEAM_SHALLOW:
            # (no corner will do: a clear parallel takes a new corner on each loop)
            scored = [(_seam_score(lines, a), a) for a in np.linspace(-np.pi, np.pi, SEAM_LOOKS, endpoint=False) if allowed(a)]
            if scored and max(scored)[0] >= SEAM_SHALLOW:
                v0 = float(max(scored)[1])
        mid = [self.V[self.rg.loop_vertices(loops[i])].mean(axis=0) for i in (Bl, A)]
        sA = self._ensure_tube_seam_vertex(r, loops[A], v0, mid[0])
        sB = self._ensure_tube_seam_vertex(r, loops[Bl], v0, mid[1]) if sA is not None else None
        if sA is None or sB is None or sA == sB:
            self.unseamed.add(r)
            return
        self.tube_seams[r] = (A, Bl, sA, sB, v0)

    def _tube_seam_lines(self, r, fr, loops):
        """For _seam_score, each loop round the tube as (angle round the tube, length along the axis's turn, and the tube's
        radius, at each of its points, in the loop's order and closed)."""
        prim = self.regions[r].prim
        out = []
        for loop in loops:
            vs = self.rg.loop_vertices(loop)
            P = self.V[vs + vs[:1]]
            out.append((np.unwrap(self._tube_angle(r, P)), prim.R * np.unwrap(fr.angle(P)), np.full(len(P), prim.r)))
        # (the two loops on one branch of the turn round the axis)
        turn = 2 * np.pi * prim.R
        a, b = out
        out[1] = (b[0], b[1] - turn * np.round((b[1].mean() - a[1].mean()) / turn), b[2])
        return out

    def _ensure_tube_seam_vertex(self, r, loop, v0, toward):
        """A corner on the loop at tube angle v0: an existing one, the nearest of them to ``toward`` (see ensure_seam_vertex), or
        a new one where the loop crosses that angle (as ensure_seam_vertex round the axis)."""
        rg = self.rg
        on = []
        for cid, fwd in loop:
            c = rg.chains[cid]
            if c.closed and cid not in self.fixed_closed:
                continue
            for v in (c.verts[0], c.verts[-1]):
                if abs(_wrap(self._tube_angle(r, self.V[v])[0] - v0)) < 1e-9:
                    on.append(v)
        if on:
            return min(on, key=lambda v: float(np.linalg.norm(self.V[v] - toward)))
        for cid, fwd in loop:
            c = rg.chains[cid]
            P = self.V[c.verts]
            rel = _wrap(self._tube_angle(r, P) - v0)
            for i in range(len(P) - 1):
                a0, a1 = rel[i], rel[i + 1]
                if abs(a1 - a0) > np.pi or not ((a0 <= 0.0 < a1) or (a1 <= 0.0 < a0)):
                    continue
                t = a0 / (a0 - a1) if a0 != a1 else 0.0
                q = self.add_point(self._on_parallel(r, P[i] + t * (P[i + 1] - P[i]), v0))
                rg.insert_vertex(cid, i + 1, q)
                if c.closed and cid in self.fixed_closed:
                    # (the circle's corner belongs to the face on its other side: see ensure_seam_vertex)
                    rg.split_chain(cid, i + 1)
                    self.fixed_closed.discard(cid)
                elif c.closed:
                    rg.rotate_closed_chain(cid, i + 1)
                    self.fixed_closed.add(cid)
                else:
                    rg.split_chain(cid, i + 1)
                return q
        return None

    def _tube_seam_wire(self, r, face, nat, A, Bl, sA, sB, v0):
        """[seam, loop round the tube at the larger u, seam reversed, the other
        loop]: the face spans u from one loop to the other and v from v0 to
        v0 + 2 pi; the seam is used at v0 one way and at v0 + 2 pi the other."""
        from OCP.Geom import Geom_Circle

        prim = self.regions[r].prim
        fr = self.frames[r]
        tol = max(self.tol, MIN_TOL)
        two_pi = 2 * np.pi
        wA, wB = self._tube_turns(r, nat[A]), self._tube_turns(r, nat[Bl])
        if wA < 0 < wB:
            left, right, sl, sr = A, Bl, sA, sB
        elif wB < 0 < wA:
            left, right, sl, sr = Bl, A, sB, sA
        else:
            raise RuntimeError("inconsistent loops round the tube")
        ul = float(fr.angle(self.V[sl][None])[0])
        ur = ul + float(np.mod(fr.angle(self.V[sr][None])[0] - ul, two_pi))
        for vi in (sl, sr):
            d = float(np.linalg.norm(self.V[vi] - self._on_parallel(r, self.V[vi], v0)))
            self.vtol[vi] = max(self.vtol[vi], d)
            self.B.UpdateVertex(self.vertex(vi), max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9))
        centre = prim.c + prim.r * np.sin(v0) * prim.d
        geom = Geom_Circle(gp_Ax2(_pnt(centre), _dir(prim.d), _dir(fr.x)), float(prim.R + prim.r * np.cos(v0)))
        e = TopoDS_Edge()
        self.B.MakeEdge(e, geom, tol)
        self.B.Add(e, self.vertex(sl))
        self.B.Add(e, self.vertex(sr).Reversed())
        self.B.Range(e, ul, ur)
        pc1 = Geom2d_Line(gp_Pnt2d(0.0, v0), gp_Dir2d(1.0, 0.0))
        pc2 = Geom2d_Line(gp_Pnt2d(0.0, v0 + two_pi), gp_Dir2d(1.0, 0.0))
        self.B.UpdateEdge(e, pc1, pc2, face, tol)
        self.B.Range(e, face, ul, ur)
        self.B.SameParameter(e, True)
        self.B.SameRange(e, True)
        w = TopoDS_Wire()
        self.B.MakeWire(w)
        self.B.Add(w, e)
        for x in self._loop_edges_from(nat[right], sr):
            self.B.Add(w, x)
        self.B.Add(w, e.Reversed())
        for x in self._loop_edges_from(nat[left], sl):
            self.B.Add(w, x)
        w.Closed(True)
        return w

    def ensure_seam_vertex(self, r, loop, u0, fr, toward=None):
        """The corner of a loop the seam at angle u0 starts from, made if there is none. (toward: a point
        on the other side, the other loop's middle. An edge can lie on the seam's meridian, with a corner on it at each end:
        the seam starts from the nearer, or it runs along that edge the whole way, which crosses it.)"""
        rg = self.rg
        on = []
        for cid, fwd in loop:
            c = rg.chains[cid]
            if c.closed and cid not in self.fixed_closed:
                continue
            for v in (c.verts[0], c.verts[-1]):
                if abs(_wrap(fr.angle(self.V[v][None])[0] - u0)) < 1e-9:
                    on.append(v)
        if on:
            if toward is None:
                return on[0]
            return min(on, key=lambda v: float(np.linalg.norm(self.V[v] - toward)))
        ext = self.ext
        for cid, fwd in loop:
            c = rg.chains[cid]
            P = self.V[c.verts]
            rel = _wrap(fr.angle(P) - u0)
            for i in range(len(P) - 1):
                a0, a1 = rel[i], rel[i + 1]
                if abs(a1 - a0) > np.pi:
                    continue  # jump across the opposite side
                if not ((a0 <= 0.0 < a1) or (a1 <= 0.0 < a0) or (a0 < 0.0 <= a1) or (a1 < 0.0 <= a0)):
                    continue
                if abs(a0) < 1e-12 and 0 < i:
                    k = i  # existing interior vertex exactly on the seam
                    q = c.verts[k]
                else:
                    t = a0 / (a0 - a1) if a0 != a1 else 0.0
                    p = P[i] + t * (P[i + 1] - P[i])
                    # a straight-line blend misses the seam angle slightly;
                    # put the point exactly on it (on tiny holes and pins
                    # the miss is enough to make the boundary cross the seam)
                    p = fr.at_angle(self.snap_to_surface(r, p), u0)
                    if np.linalg.norm(p - P[i]) < 1e-9 * ext and i > 0:
                        k, q = i, c.verts[i]
                    elif np.linalg.norm(p - P[i + 1]) < 1e-9 * ext and i + 1 < len(P) - 1:
                        k, q = i + 1, c.verts[i + 1]
                    else:
                        q = self.add_point(p)
                        rg.insert_vertex(cid, i + 1, q)
                        k = i + 1
                if c.closed and cid in self.fixed_closed:
                    # (the circle holds a seam's corner already, its first point, for the face on its other side:
                    # turned to start here, that face's corner would be lost, and the face with it (test2's 30 mm2
                    # cylinder: "seam vertex not on loop"). Split, each face keeps a corner of its own.)
                    rg.split_chain(cid, k)
                    self.fixed_closed.discard(cid)
                elif c.closed:
                    rg.rotate_closed_chain(cid, k)
                    self.fixed_closed.add(cid)
                elif 0 < k < len(c.verts) - 1:
                    rg.split_chain(cid, k)
                return q
        return None

    def snap_to_surface(self, r, p):
        prim = self.regions[r].prim
        p = np.asarray(p, dtype=np.float64)
        if prim.kind in ("cylinder", "cone", "torus", "sphere"):
            d = prim.distance(p[None])[0]
            n = prim.normal(p[None])[0]
            if prim.kind == "cone":
                s = prim.signed(p[None])[0]
                return p - s * n
            # move along the surface normal by the signed offset
            if prim.kind == "cylinder":
                rel = p - prim.a
                h = rel @ prim.d
                rad = rel - h * prim.d
                return prim.a + h * prim.d + prim.r * unit(rad)
            if prim.kind == "sphere":
                return prim.c + prim.r * unit(p - prim.c)
            if prim.kind == "torus":
                q = prim._spine(p[None])[0]
                return q + prim.r * unit(p - q)
        return p

    # --- faces ----------------------------------------------------------
    def closed_region_ok(self, r):
        return self.regions[r].kind in ("sphere", "torus")

    def build_face(self, r):
        reg = self.regions[r]
        if reg.kind == "plane":
            return self._planar_face(r, self.surfaces[r], self.rg.region_loops[r])
        if reg.kind == "freeform":
            surf = reg.prim.geom()
        else:
            surf = self._make_surface(reg, r)
        loops = self.rg.region_loops[r]
        if not loops:
            # a closed surface on its own (full sphere or torus)
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace

            mf = BRepBuilderAPI_MakeFace(surf, 1e-7)
            if not mf.IsDone():
                raise RuntimeError("closed surface face failed")
            face = mf.Face()
            if reg.sense < 0:
                face.Reverse()
            return face
        tol = max(self.tol, MIN_TOL)
        nat = loops if reg.sense > 0 else [_reverse_loop(l) for l in loops]
        face = self._bounded_face(r, surf, nat, reg.sense < 0, tol)
        if (reg.kind in PERIODIC + ("sphere",) and r not in self.seams and r not in self.pole_caps
                and r not in self.tube_seams):
            # On a closed surface a boundary running the wrong way round
            # trims the complementary part of it: valid, but wrong. The
            # mesh area of the region tells which way is right.
            want = self.region_area(r)
            got = _face_area(face)
            if abs(got - want) > 0.15 * want:
                alt = self._bounded_face(r, surf, [_reverse_loop(l) for l in nat], reg.sense > 0, tol)
                if abs(_face_area(alt) - want) < abs(got - want):
                    face = alt
        return face

    def region_area(self, r):
        if getattr(self, "_fa", None) is None:
            T = self.V[self.rg.F]
            self._fa = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
        return float(self._fa[self.regions[r].faces].sum())

    def _bounded_face(self, r, surf, nat, reverse, tol):
        swept = self.regions[r].prim if hasattr(self.regions[r].prim, "pcurve") else None
        face = TopoDS_Face()
        self.B.MakeFace(face, surf, tol)
        wires = []
        seam = self.seams.get(r)
        used = set()
        if seam is not None:
            A, Bl, sA, sB = seam
            wires.append(self._seam_wire(r, face, nat, A, Bl, sA, sB))
            used = {A, Bl}
        elif r in self.pole_caps:
            A, sA = self.pole_caps[r]
            wires.append(self._pole_cap_wire(r, face, nat, A, sA))
            used = {A}
        elif r in self.tube_seams:
            A, Bl, sA, sB, v0 = self.tube_seams[r]
            wires.append(self._tube_seam_wire(r, face, nat, A, Bl, sA, sB, v0))
            used = {A, Bl}
        for i, loop in enumerate(nat):
            if i not in used:
                wires.append(self.wire_for_loop(loop))
        for w in wires:
            exp = TopExp_Explorer(w, TopAbs_EDGE)
            while exp.More():
                e = TopoDS.Edge(exp.Current())
                exp.Next()
                if BRep_Tool.IsClosed_s(e, face):
                    continue  # seam: explicit pcurves already
                if swept is not None:
                    # placed exactly from the sweep (projection onto a
                    # twisted flank went astray: the faces were invalid);
                    # the edge's own curve, fitted along the mesh's border,
                    # can stand off this surface by more than its tolerance,
                    # which then has to cover the gap
                    pc = swept.pcurve(e)
                    if pc is not None:
                        gap = _pcurve_gap(e, pc, swept)
                        self.B.UpdateEdge(e, pc, face, max(BRep_Tool.Tolerance_s(e), 1.5 * gap))
                        continue
                self.sfe.FixAddPCurve(e, face, False, tol)
            sfw = ShapeFix_Wire(w, face, tol)
            sfw.FixShifted()
            self.B.Add(face, sfw.Wire())
        if reverse:
            face.Reverse()
        return face

    def _loop_edges_from(self, loop, start):
        k = None
        for i, (cid, fwd) in enumerate(loop):
            c = self.rg.chains[cid]
            sv = c.verts[0] if fwd else c.verts[-1]
            if sv == start:
                k = i
                break
        if k is None:
            raise RuntimeError("seam vertex not on loop")
        loop = loop[k:] + loop[:k]
        out = []
        for cid, fwd in loop:
            edges = self.rg.chains[cid].edges
            if fwd:
                out.extend(edges)
            else:
                out.extend(e.Reversed() for e in reversed(edges))
        return out

    def _winding(self, r, loop):
        fr = self.frames[r]
        vs = self.rg.loop_vertices(loop)
        ang = np.unwrap(fr.angle(self.V[vs + vs[:1]]))
        return int(np.round((ang[-1] - ang[0]) / (2 * np.pi)))

    def _vparam(self, r, P):
        reg = self.regions[r]
        prim = reg.prim
        sp = self.surf_params[r]
        if prim.kind == "cylinder":
            return float((P - prim.a) @ prim.d)
        if prim.kind == "cone":
            return float(((P - sp["loc"]) @ prim.d) / np.cos(prim.alpha))
        if prim.kind == "torus":
            rel = P - prim.c
            h = rel @ prim.d
            rho = np.linalg.norm(rel - h * prim.d)
            v = np.arctan2(h, rho - prim.R)
            # express in the region's v branch
            v0 = sp["vcut"]
            return float(v0 + np.mod(v - v0, 2 * np.pi))
        if prim.kind == "sphere":
            fr = self.frames[r]
            return float(np.arcsin(np.clip(((P - prim.c) @ fr.d) / prim.r, -1.0, 1.0)))
        raise ValueError(prim.kind)

    def _meridian(self, r, v0, v1):
        from OCP.Geom import Geom_Circle

        prim = self.regions[r].prim
        fr = self.frames[r]
        n = np.cross(fr.x, fr.d)
        geom = Geom_Circle(gp_Ax2(_pnt(prim.c), _dir(n), _dir(fr.x)), prim.r)
        p = lambda v: prim.c + prim.r * (np.cos(v) * fr.x + np.sin(v) * fr.d)
        return geom, p(v0), p(v1)

    def _generator(self, r, v0, v1):
        """A cone's straight line at u=0, parametrised by the surface's own v
        (as _meridian is for a sphere), and its points at v0 and v1."""
        from OCP.Geom import Geom_Line

        prim = self.regions[r].prim
        fr = self.frames[r]
        sp = self.surf_params[r]
        sa, ca = np.sin(prim.alpha), np.cos(prim.alpha)
        g = sa * fr.x + ca * prim.d
        base = sp["loc"] + sp["R0"] * fr.x          # v = 0 on the seam
        return Geom_Line(_pnt(base), _dir(g)), base + v0 * g, base + v1 * g

    def _pole_cap_wire(self, r, face, nat, A, s):
        """Wire of a cap containing a pole: [loop, seam, degenerate pole edge,
        seam reversed]. The pole is a sphere's pole or a cone's apex (a pin's
        point, a drill point, a countersink run to its tip: without the
        degenerate edge such a cone face is invalid, and the InfinityCubeFidget's
        32 cone tips each became a few dozen flat triangles)."""
        prim = self.regions[r].prim
        fr = self.frames[r]
        tol = max(self.tol, MIN_TOL)
        two_pi = 2 * np.pi
        w_a = self._winding(r, nat[A])
        v0 = self._vparam(r, self.V[s])
        north = w_a > 0
        if w_a == 0:
            raise RuntimeError("cap loop does not wrap")
        if prim.kind == "cone":
            # the apex is where the cone's v is lowest (it opens along d), so
            # the face lies below its loop, which runs clockwise in (u, v)
            vp = self._vparam(r, prim.apex)
            if north or not vp < v0:
                raise RuntimeError("cone cap does not hold its apex")
            pole_pt = np.asarray(prim.apex, dtype=np.float64)
        else:
            vp = np.pi / 2 if north else -np.pi / 2
            pole_pt = prim.c + prim.r * np.sign(vp) * fr.d
        pole = TopoDS_Vertex()
        self.B.MakeVertex(pole, _pnt(pole_pt), tol)
        # seam: meridian at u=0 between the loop and the pole, oriented by
        # increasing v
        lo, hi = (v0, vp) if north else (vp, v0)
        if prim.kind == "cone":
            geom, p_lo, p_hi = self._generator(r, lo, hi)
        else:
            geom, p_lo, p_hi = self._meridian(r, lo, hi)
        d = float(np.linalg.norm(self.V[s] - (p_lo if north else p_hi)))
        self.vtol[s] = max(self.vtol[s], d)
        self.B.UpdateVertex(self.vertex(s), max(MIN_TOL, 1.5 * self.vtol[s] + 1e-9))
        seam = TopoDS_Edge()
        self.B.MakeEdge(seam, geom, tol)
        if north:
            self.B.Add(seam, self.vertex(s))
            self.B.Add(seam, pole.Reversed())
        else:
            self.B.Add(seam, pole)
            self.B.Add(seam, self.vertex(s).Reversed())
        self.B.Range(seam, lo, hi)
        pc1 = Geom2d_Line(gp_Pnt2d(two_pi, 0.0), gp_Dir2d(0.0, 1.0))
        pc2 = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(0.0, 1.0))
        self.B.UpdateEdge(seam, pc1, pc2, face, tol)
        self.B.Range(seam, face, lo, hi)
        # degenerate edge at the pole
        deg = TopoDS_Edge()
        self.B.MakeEdge(deg)
        self.B.Add(deg, pole)
        self.B.Add(deg, pole.Reversed())
        self.B.Degenerated(deg, True)
        if north:
            pcd = Geom2d_Line(gp_Pnt2d(two_pi, vp), gp_Dir2d(-1.0, 0.0))
        else:
            pcd = Geom2d_Line(gp_Pnt2d(0.0, vp), gp_Dir2d(1.0, 0.0))
        self.B.UpdateEdge(deg, pcd, face, tol)
        self.B.Range(deg, face, 0.0, two_pi)
        self.B.Range(deg, 0.0, two_pi)
        w = TopoDS_Wire()
        self.B.MakeWire(w)
        for x in self._loop_edges_from(nat[A], s):
            self.B.Add(w, x)
        if north:
            self.B.Add(w, seam)
            self.B.Add(w, deg)
            self.B.Add(w, seam.Reversed())
        else:
            self.B.Add(w, seam.Reversed())
            self.B.Add(w, deg)
            self.B.Add(w, seam)
        w.Closed(True)
        return w

    def _seam_wire(self, r, face, nat, A, Bl, sA, sB):
        wA = self._winding(r, nat[A])
        wB = self._winding(r, nat[Bl])
        if wA > 0 > wB:
            bot, top, sbot, stop = A, Bl, sA, sB
        elif wB > 0 > wA:
            bot, top, sbot, stop = Bl, A, sB, sA
        else:
            raise RuntimeError("inconsistent wrapping loops")
        vb = self._vparam(r, self.V[sbot])
        vt = self._vparam(r, self.V[stop])
        if not vt > vb:
            raise RuntimeError("seam loops in unexpected order")
        e = self._seam_edge(r, face, sbot, stop, vb, vt)
        w = TopoDS_Wire()
        self.B.MakeWire(w)
        for x in self._loop_edges_from(nat[bot], sbot):
            self.B.Add(w, x)
        self.B.Add(w, e)
        for x in self._loop_edges_from(nat[top], stop):
            self.B.Add(w, x)
        self.B.Add(w, e.Reversed())
        w.Closed(True)
        return w

    def _seam_edge(self, r, face, sbot, stop, vb, vt):
        from OCP.Geom import Geom_Circle, Geom_Line

        prim = self.regions[r].prim
        fr = self.frames[r]
        sp = self.surf_params[r]
        tol = max(self.tol, MIN_TOL)
        two_pi = 2 * np.pi
        if prim.kind == "cylinder":
            p0 = prim.a + prim.r * fr.x + vb * prim.d
            p1 = prim.a + prim.r * fr.x + vt * prim.d
            geom = Geom_Line(_pnt(p0), _dir(prim.d))
            t0, t1 = 0.0, vt - vb
            pc1 = Geom2d_Line(gp_Pnt2d(two_pi, vb), gp_Dir2d(0.0, 1.0))
            pc2 = Geom2d_Line(gp_Pnt2d(0.0, vb), gp_Dir2d(0.0, 1.0))
        elif prim.kind == "cone":
            loc, R0 = sp["loc"], sp["R0"]
            sa, ca = np.sin(prim.alpha), np.cos(prim.alpha)
            g = sa * fr.x + ca * prim.d
            p0 = loc + (R0 + vb * sa) * fr.x + vb * ca * prim.d
            p1 = loc + (R0 + vt * sa) * fr.x + vt * ca * prim.d
            geom = Geom_Line(_pnt(p0), _dir(g))
            t0, t1 = 0.0, vt - vb
            pc1 = Geom2d_Line(gp_Pnt2d(two_pi, vb), gp_Dir2d(0.0, 1.0))
            pc2 = Geom2d_Line(gp_Pnt2d(0.0, vb), gp_Dir2d(0.0, 1.0))
        elif prim.kind == "sphere":
            geom, p0, p1 = self._meridian(r, vb, vt)
            t0, t1 = vb, vt
            pc1 = Geom2d_Line(gp_Pnt2d(two_pi, 0.0), gp_Dir2d(0.0, 1.0))
            pc2 = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(0.0, 1.0))
        elif prim.kind == "torus":
            center = prim.c + prim.R * fr.x
            n = np.cross(fr.x, prim.d)
            geom = Geom_Circle(gp_Ax2(_pnt(center), _dir(n), _dir(fr.x)), prim.r)
            p0 = center + prim.r * (np.cos(vb) * fr.x + np.sin(vb) * prim.d)
            p1 = center + prim.r * (np.cos(vt) * fr.x + np.sin(vt) * prim.d)
            t0, t1 = vb, vt
            pc1 = Geom2d_Line(gp_Pnt2d(two_pi, 0.0), gp_Dir2d(0.0, 1.0))
            pc2 = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(0.0, 1.0))
        else:
            raise ValueError(prim.kind)
        for vi, pe in ((sbot, p0), (stop, p1)):
            d = float(np.linalg.norm(self.V[vi] - pe))
            self.vtol[vi] = max(self.vtol[vi], d)
            self.B.UpdateVertex(self.vertex(vi), max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9))
        e = TopoDS_Edge()
        self.B.MakeEdge(e, geom, tol)
        self.B.Add(e, self.vertex(sbot))
        self.B.Add(e, self.vertex(stop).Reversed())
        self.B.Range(e, t0, t1)
        self.B.UpdateEdge(e, pc1, pc2, face, tol)
        self.B.Range(e, face, t0, t1)
        self.B.SameParameter(e, True)
        self.B.SameRange(e, True)
        return e

    def _make_surface(self, reg, r=None):
        prim = reg.prim
        P = self.V[np.unique(self.F_of(reg))]
        fr = self.region_frame(r) if r is not None else None
        sp = {}
        if prim.kind == "cylinder":
            x = fr.x if fr is not None else self._seam_dir(P, prim.a, prim.d)
            ax3 = gp_Ax3(_pnt(prim.a), _dir(prim.d), _dir(x))
            surf = Geom_CylindricalSurface(ax3, prim.r)
        elif prim.kind == "cone":
            h = (P - prim.apex) @ prim.d
            h0 = float(np.clip(h.mean(), 1e-6, None))
            loc = prim.apex + h0 * prim.d
            x = fr.x if fr is not None else self._seam_dir(P, loc, prim.d)
            ax3 = gp_Ax3(_pnt(loc), _dir(prim.d), _dir(x))
            R0 = h0 * np.tan(prim.alpha)
            surf = Geom_ConicalSurface(ax3, prim.alpha, R0)
            sp = {"loc": loc, "R0": R0}
        elif prim.kind == "sphere":
            if fr is not None:
                ax3 = gp_Ax3(_pnt(prim.c), _dir(fr.d), _dir(fr.x))
            else:
                m = unit((P - prim.c).mean(0)) if len(P) else np.array([0, 0, 1.0])
                if not np.any(m):
                    m = np.array([0, 0, 1.0])
                u, v = perp_basis(m)
                ax3 = gp_Ax3(_pnt(prim.c), _dir(u), _dir(-m))
            surf = Geom_SphericalSurface(ax3, prim.r)
        elif prim.kind == "torus":
            x = fr.x if fr is not None else self._seam_dir(P, prim.c, prim.d)
            ax3 = gp_Ax3(_pnt(prim.c), _dir(prim.d), _dir(x))
            surf = Geom_ToroidalSurface(ax3, prim.R, prim.r)
            # branch cut for the minor angle: middle of the largest gap
            rel = P - prim.c
            hh = rel @ prim.d
            rho = np.linalg.norm(rel - np.outer(hh, prim.d), axis=1)
            vv = np.sort(np.arctan2(hh, rho - prim.R))
            gaps = np.diff(np.r_[vv, vv[0] + 2 * np.pi])
            k = int(np.argmax(gaps))
            sp = {"vcut": float(vv[k] + gaps[k] / 2 - 2 * np.pi)}
        else:
            raise ValueError(prim.kind)
        if r is not None:
            self.surf_params[r] = sp
        return surf

    def F_of(self, reg):
        return self.rg.F[reg.faces]

    @staticmethod
    def _seam_dir(P, origin, d):
        rel = P - origin
        rad = rel - np.outer(rel @ d, d)
        u, v = perp_basis(d)
        ang = np.arctan2(rad @ v, rad @ u)
        # place the seam in the middle of the largest angular gap
        a = np.sort(ang)
        gaps = np.diff(np.r_[a, a[0] + 2 * np.pi])
        k = int(np.argmax(gaps))
        mid = a[k] + gaps[k] / 2
        return np.cos(mid) * u + np.sin(mid) * v


PERIODIC = ("cylinder", "cone", "torus")
# Swept surfaces, tapers and fillets (Segmenter._find_sweeps and
# _find_fillets); MMM_SWEEPS=0 turns them off
import os as _os

FIND_SWEEPS = _os.environ.get("MMM_SWEEPS") != "0"
POOLING = _os.environ.get("MMM_POOL") != "0"     # pieces of one surface are pooled (_pool_pieces)
POOL_FREEFORM = _os.environ.get("MMM_POOL_FREEFORM") != "0"   # tiles no simple surface holds become one freeform cell
# Saw-tooth borders straightened (Segmenter._straighten_borders); on by
# default, MMM_STRAIGHTEN=0 turns it off. Owner's rim, mount and tire:
# 154 -> 48, 164 -> 66 and 200 -> 24 saw-tooth edges. The small pieces a cut
# leaves are absorbed again, and a cut that squeezes a surface into a strip
# is undone: one 0.05 mm wide got triangles at the origin in a slicer's
# mesher (OpenCASCADE at 0.003 mm, angle 0.5)
STRAIGHTEN = _os.environ.get("MMM_STRAIGHTEN") != "0"
_PLURAL = {"plane": "planes", "cylinder": "cylinders", "cone": "cones", "sphere": "spheres", "torus": "tori", "freeform": "freeform patches"}


def _pcurve_gap(edge, pc, swept, n=40):
    """How far an edge's 3D curve is from the swept surface's points along
    the edge's curve on it (pc, sharing the edge's parameters)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    c = BRepAdaptor_Curve(edge)
    ts = np.linspace(c.FirstParameter(), c.LastParameter(), n)
    P = np.array([[*c.Value(float(t)).Coord()] for t in ts])
    g = swept.geom()
    Q = np.array([[*g.Value(pc.Value(float(t)).X(), pc.Value(float(t)).Y()).Coord()] for t in ts])
    return float(np.linalg.norm(P - Q, axis=1).max())


def _project(prim, P):
    """Points moved onto a surface (along its normal)."""
    k = prim.kind
    if k == "plane":
        return P - np.outer((P - prim.origin) @ prim.n, prim.n)
    if k == "cylinder":
        rad, h = prim._radial(P)
        rho = np.linalg.norm(rad, axis=1)
        if rho.min() < 1e-6 * max(prim.r, 1.0):
            return None
        return prim.a + np.outer(h, prim.d) + rad / rho[:, None] * prim.r
    if k == "sphere":
        v = P - prim.c
        rho = np.linalg.norm(v, axis=1)
        if rho.min() < 1e-6 * max(prim.r, 1.0):
            return None
        return prim.c + v / rho[:, None] * prim.r
    if k == "torus":
        q = prim._spine(P)
        v = P - q
        rho = np.linalg.norm(v, axis=1)
        if rho.min() < 1e-6 * max(prim.r, 1.0):
            return None
        return q + v / rho[:, None] * prim.r
    if k == "cone":
        Q = P.copy()
        for _ in range(3):
            Q = Q - prim.signed(Q)[:, None] * prim.normal(Q)
        return Q
    if hasattr(prim, "foot"):  # a swept surface
        return prim.X(*prim.foot(P))
    return None


_FOOTERS = {}           # a freeform surface's point finder, by the surface, see _foot
FOOTERS_KEPT = 500      # surfaces whose finders are kept


def _known_curve(prims, P):
    """The curve two surfaces meet in where that is a circle or an ellipse with known axes:
    ("circle", centre, normal, radius) or ("ellipse", centre, normal, major direction, major, minor);
    None for any other pair. P: points of the border, for choosing between a torus's two circles."""
    a, b = prims
    if a.kind != "plane" and b.kind == "plane":
        a, b = b, a
    square = 1.0 - 1e-12
    if a.kind == "plane":
        n = a.n
        if b.kind == "cylinder":
            c = float(n @ b.d)
            if abs(c) < 1e-3:
                return None     # (lines, which a chord tests for)
            h = float((a.origin - b.a) @ n) / c
            centre = b.a + h * b.d
            if abs(c) >= square:
                return "circle", centre, b.d, float(b.r)
            x = b.d - c * n
            x = x / np.linalg.norm(x)
            return "ellipse", centre, n, x, float(b.r) / abs(c), float(b.r)
        if b.kind == "cone" and abs(float(n @ b.d)) >= square:
            h = float((a.origin - b.apex) @ n) / float(n @ b.d)
            if h <= 0:
                return None
            return "circle", b.apex + h * b.d, b.d, float(h * np.tan(b.alpha))
        if b.kind == "sphere":
            s = float((a.origin - b.c) @ n)
            if abs(s) >= b.r:
                return None
            return "circle", b.c + s * n, n, float(np.sqrt(b.r * b.r - s * s))
        if b.kind == "torus" and abs(float(n @ b.d)) >= square:
            s = float((a.origin - b.c) @ b.d)
            if abs(s) >= b.r:
                return None
            rel = P[0] - b.c
            rho = float(np.linalg.norm(rel - (rel @ b.d) * b.d))
            w = float(np.sqrt(b.r * b.r - s * s))
            return "circle", b.c + s * b.d, b.d, float(min((b.R + w, b.R - w), key=lambda v: abs(v - rho)))
        return None
    if {a.kind, b.kind} == {"cylinder", "cone"}:
        cy, co = (a, b) if a.kind == "cylinder" else (b, a)
        if abs(float(cy.d @ co.d)) < square:
            return None
        off = co.apex - cy.a
        off = off - float(off @ cy.d) * cy.d
        if float(np.linalg.norm(off)) > 1e-9:
            return None
        h = cy.r / np.tan(co.alpha)
        return "circle", co.apex + h * co.d, cy.d, float(cy.r)
    return None


def _curve_gap(known, P):
    """How far the points of a border are from a known curve, at most."""
    if known[0] == "circle":
        _, c, n, r = known
        rel = P - c
        h = rel @ n
        rho = np.linalg.norm(rel - np.outer(h, n), axis=1)
        return float(np.hypot(rho - r, h).max())
    _, c, n, x, major, minor = known
    rel = P - c
    y = np.cross(n, x)
    u, v, h = rel @ x, rel @ y, rel @ n
    ang = np.arctan2(v / minor, u / major)
    return float(np.sqrt((u - major * np.cos(ang)) ** 2 + (v - minor * np.sin(ang)) ** 2 + h * h).max())


def _crossings_beside(A, QA, B, QB, tol):
    """Where two edges leaving a corner cross having run within tol of each other all the way there, from the corner:
    A, B their points in the surface's tangent plane, QA, QB the same points in space from the corner."""
    out = []
    for i_a, i_b, t, w in _line_crossings(A, B):
        pa = QA[i_a] + t * (QA[i_a + 1] - QA[i_a])
        pb = QB[i_b] + w * (QB[i_b + 1] - QB[i_b])
        if np.linalg.norm(pa - pb) > tol:
            continue
        before = np.vstack([QA[:i_a + 1], pa[None]])
        beside = np.vstack([QB[:i_b + 1], pb[None]])
        if np.linalg.norm(before[:, None] - beside[None], axis=2).min(axis=1).max() <= tol:
            out.append(0.5 * (pa + pb))
    return out


def _line_crossings(A, B):
    """Where two lines of points in a plane cross: (segment of A, segment of B, share along each). Each starts
    near a shared corner: touching at their ends doesn't count."""
    a, r = A[:-1], np.diff(A, axis=0)
    b, s = B[:-1], np.diff(B, axis=0)
    d = r[:, None, 0] * s[None, :, 1] - r[:, None, 1] * s[None, :, 0]
    gap = b[None] - a[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (gap[..., 0] * s[None, :, 1] - gap[..., 1] * s[None, :, 0]) / d
        w = (gap[..., 0] * r[:, None, 1] - gap[..., 1] * r[:, None, 0]) / d
    hit = (d != 0) & (t > 1e-9) & (t < 1 - 1e-9) & (w > 1e-9) & (w < 1 - 1e-9)
    return [(int(i), int(j), float(t[i, j]), float(w[i, j])) for i, j in np.argwhere(hit)]


def _prim_key(p):
    """What says two surfaces are the same, for keeping what was worked out from them."""
    if p.kind == "plane":
        return ("plane", p.origin.tobytes(), p.n.tobytes())
    if p.kind == "cylinder":
        return ("cylinder", p.a.tobytes(), p.d.tobytes(), p.r)
    if p.kind == "cone":
        return ("cone", p.apex.tobytes(), p.d.tobytes(), p.alpha)
    if p.kind == "sphere":
        return ("sphere", p.c.tobytes(), p.r)
    if p.kind == "torus":
        return ("torus", p.c.tobytes(), p.d.tobytes(), p.R, p.r)
    return ("other", id(p))


def _foot(prim, x):
    """The point of a surface nearest x and its unit normal there, or None when the surface can't
    tell: the point on its axis, say."""
    k = prim.kind
    if k in ("plane", "cylinder", "sphere", "torus", "cone"):
        Q = _project(prim, x[None])
        if Q is None:
            return None
        n = prim.normal(Q)[0]
        return Q[0], n
    if not hasattr(prim, "geom"):
        return None
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.gp import gp_Pnt, gp_Vec

    g = prim.geom()
    # (one finder kept per surface: making a projector for each point took nearly all of
    # this pass's time, 113 of 199 s on main plate)
    sa = _FOOTERS.get(id(g))
    if sa is None or sa[1] is not g:
        if len(_FOOTERS) > FOOTERS_KEPT:
            _FOOTERS.clear()
        sa = _FOOTERS[id(g)] = (ShapeAnalysis_Surface(g), g)
    uv = sa[0].ValueOfUV(_pnt(x), 1e-7)
    u, v = uv.X(), uv.Y()
    P, A, B = gp_Pnt(), gp_Vec(), gp_Vec()
    g.D1(u, v, P, A, B)
    n = np.cross([A.X(), A.Y(), A.Z()], [B.X(), B.Y(), B.Z()])
    L = float(np.linalg.norm(n))
    if L < 1e-12:
        return None
    return np.array([P.X(), P.Y(), P.Z()]), n / L


def _meet(x0, prims, reach, iters=6):
    """The point nearest x0 that lies on all the surfaces, by least-norm Gauss-Newton steps: only
    the directions the surfaces pin down are moved along, so nearly tangent surfaces don't throw
    the point far along their meeting line. None when it's more than reach away, however little
    is asked of it, or a surface can't tell."""
    x0 = np.asarray(x0, dtype=np.float64)
    for rcond in MEET_RCOND:
        x = x0.copy()
        for _ in range(iters):
            A, b = [], []
            for q in prims:
                f = _foot(q, x)
                if f is None:
                    return None
                A.append(f[1])
                b.append(float(f[1] @ (f[0] - x)))
            # (two surfaces meeting clearly, nearly every point asked for, need no more than this)
            if len(A) == 2:
                c = float(A[0] @ A[1])
                if c * c < MEET_CLEAR:
                    # the least step onto both: along the two normals
                    k = 1.0 / (1.0 - c * c)
                    u0, u1 = k * (b[0] - c * b[1]), k * (b[1] - c * b[0])
                    dx = u0 * A[0] + u1 * A[1]
                    x = x + dx
                    if float(dx @ dx) < 1e-20:
                        break
                    continue
            # (surfaces touching smoothly say nothing across their line of contact: the line does)
            for t, s in contact_rows(prims, x):
                A.append(t)
                b.append(s)
            A, b = np.array(A), np.array(b)
            U, W, Vt = np.linalg.svd(A, full_matrices=False)
            keep = W > rcond * W[0]
            dx = Vt[keep].T @ ((U[:, keep].T @ b) / W[keep])
            x = x + dx
            if float(np.linalg.norm(dx)) < 1e-10:
                break
        if np.isfinite(x).all() and float(np.linalg.norm(x - x0)) <= reach:
            return x
    return None


def _densify(P, prims, reach):
    """The points of a border, with one more on both surfaces between each two, so a border the
    mesh crosses in one or two steps still shows whether it's straight. Ends stay."""
    out = [P[0]]
    for a, b in zip(P[:-1], P[1:]):
        m = _meet((a + b) / 2, prims, reach)
        if m is not None:
            out.append(m)
        out.append(b)
    return np.array(out)


def _meeting_angle(Q, prims):
    """Degrees between two surfaces along a border, at its most nearly tangent (0 to 90), from
    a few of its points."""
    worst = 90.0
    for p in Q[np.unique(np.linspace(0, len(Q) - 1, 5).astype(int))]:
        a, b = _foot(prims[0], p), _foot(prims[1], p)
        if a is None or b is None:
            return 0.0
        c = min(1.0, abs(float(a[1] @ b[1])))
        worst = min(worst, float(np.degrees(np.arccos(c))))
    return worst


def _face_area(face):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    try:
        g = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, g, 1e-4)
        return abs(float(g.Mass()))
    except Exception as fault:
        faults.caught(fault)
        return 0.0


def _wrap(a):
    """Wrap angle(s) to (-pi, pi]."""
    return (np.asarray(a) + np.pi) % (2 * np.pi) - np.pi


def _seam_score(lines, a):
    """How well a seam along the meridian at angle a keeps clear of the two loops it joins (see _seam_lines): the
    least, over the loops' points within its span, of how far the point stands off it for how far along it the point
    is from where it meets a loop. A loop that merely crosses it scores by its slope. A stretch running beside it scores
    next to nothing, and -1 means a loop crosses that meridian more than once, as where a border runs backward.
    motorfeste: a 4,206 mm2 wall with a 58.8 mm slit in it, and its seam 0.01 mm from the slit's edge: their pcurves
    crossed and the wall, 8% of the part, went to B-spline cells. test13: a seam at the tip of a backward run (helper
    planes, 2026-11-03)."""
    met = []
    for ang, h, _ in lines:
        here = []
        # (either side of the meridian: an edge lying on it has a corner on it at each end)
        for side in (-1e-9, 1e-9):
            k = np.floor((ang - a - side) / (2 * np.pi))
            if int((k[1:] != k[:-1]).sum()) != 1:
                return -1.0
            i = int(np.nonzero(k[1:] != k[:-1])[0][0])
            step = ang[i + 1] - ang[i]
            target = a + side + 2 * np.pi * max(k[i], k[i + 1])
            here.append(h[i] + (target - ang[i]) / step * (h[i + 1] - h[i]) if step != 0.0 else h[i])
        met.append(here)
    mid = [float(np.mean(x[1])) for x in lines]
    # (the seam starts from whichever is nearer the other loop: see ensure_seam_vertex)
    first = min(met[0], key=lambda x: abs(x - mid[1]))
    second = min(met[1], key=lambda x: abs(x - mid[0]))
    lo, hi = min(first, second), max(first, second)
    if hi - lo <= 0.0:
        return float(np.pi)
    best = float(np.pi)
    # (a loop's own points where the seam meets it, a hair inside the span, say nothing)
    margin = max(SEAM_END * (hi - lo), 1e-6)
    for ang, h, rho in lines:
        inside = (h > lo + margin) & (h < hi - margin)
        if inside.any():
            off = np.abs(_wrap(ang[inside] - a)) * rho[inside]
            along = np.minimum(h[inside] - lo, hi - h[inside])
            best = min(best, float((off / along).min()))
    return best


def _best_gap(forb):
    if not forb:
        return 0.0
    edges = sorted((lo % (2 * np.pi), (lo % (2 * np.pi)) + (hi - lo)) for lo, hi in forb)
    best, best_mid = -1.0, 0.0
    for i, (lo, hi) in enumerate(edges):
        nlo = edges[(i + 1) % len(edges)][0] + (2 * np.pi if i + 1 == len(edges) else 0.0)
        gap = nlo - hi
        if gap > best:
            best, best_mid = gap, hi + gap / 2
    return float(_wrap(best_mid))


def _reverse_loop(loop):
    return [(cid, not fwd) for cid, fwd in reversed(loop)]


class _Frame:
    def __init__(self, o, d, x):
        self.o = np.asarray(o, dtype=np.float64)
        self.d = unit(np.asarray(d, dtype=np.float64))
        x = np.asarray(x, dtype=np.float64)
        self.x = unit(x - (x @ self.d) * self.d)
        self.y = np.cross(self.d, self.x)

    def angle(self, P):
        rel = np.atleast_2d(P) - self.o
        return np.arctan2(rel @ self.y, rel @ self.x)

    def rotated(self, a):
        return _Frame(self.o, self.d, np.cos(a) * self.x + np.sin(a) * self.y)

    def at_angle(self, p, a):
        """Rotate point p about the axis so that its angle is exactly a
        (it stays on any surface of revolution about this axis)."""
        rel = np.asarray(p, dtype=np.float64) - self.o
        h = rel @ self.d
        rad = rel - h * self.d
        rho = float(np.linalg.norm(rad))
        return self.o + h * self.d + rho * (np.cos(a) * self.x + np.sin(a) * self.y)


class _SurfAdapter:
    """Gives the base assembler a distance() / normal for any region."""

    def __init__(self, reg):
        self.reg = reg
        self.kind = reg.kind
        if reg.kind == "plane":
            self.origin = reg.prim.origin
            self.normal = reg.prim.n * reg.sense

    def distance(self, P):
        return self.reg.prim.distance(np.asarray(P))


def _polyline_distance(S, P):
    """Max distance from sample points S to the polyline P."""
    A = P[:-1]
    B = P[1:]
    AB = B - A
    L2 = np.maximum((AB * AB).sum(1), 1e-30)
    best = np.full(len(S), np.inf)
    for i0 in range(0, len(A), 256):
        a = A[i0:i0 + 256]
        ab = AB[i0:i0 + 256]
        l2 = L2[i0:i0 + 256]
        t = np.clip(((S[:, None, :] - a[None]) * ab[None]).sum(2) / l2[None], 0, 1)
        proj = a[None] + t[..., None] * ab[None]
        d = np.linalg.norm(S[:, None, :] - proj, axis=2).min(1)
        best = np.minimum(best, d)
    return float(best.max())


def _polyline_arc_positions(S, P):
    """Arc-length position along polyline P of each sample's nearest point."""
    A = P[:-1]
    AB = P[1:] - A
    L2 = np.maximum((AB * AB).sum(1), 1e-30)
    seg_len = np.sqrt(L2)
    cum = np.r_[0.0, np.cumsum(seg_len)]
    t = np.clip(((S[:, None, :] - A[None]) * AB[None]).sum(2) / L2[None], 0, 1)
    proj = A[None] + t[..., None] * AB[None]
    d = np.linalg.norm(S[:, None, :] - proj, axis=2)
    i = np.argmin(d, axis=1)
    return cum[i] + t[np.arange(len(S)), i] * seg_len[i], cum[-1]


def _monotonic_along(S, P, slack):
    """True if samples S advance along polyline P without going backwards
    (an overshoot that doubles back along the polyline is invisible to a
    plain distance check but breaks faces on periodic surfaces)."""
    if len(P) < 3 or len(S) < 4:
        return True
    pos, total = _polyline_arc_positions(S[1:-1], P)
    return bool((np.diff(pos) >= -slack).all())


def _sample_curve(curve, n):
    a, b = curve.FirstParameter(), curve.LastParameter()
    out = np.empty((n, 3))
    for i, t in enumerate(np.linspace(a, b, n)):
        p = curve.Value(t)
        out[i] = (p.X(), p.Y(), p.Z())
    return out


def _inside_polygon(Q, poly):
    """Which of the 2D points Q lie inside the closed polygon poly (crossing number)."""
    A, B = poly, np.roll(poly, -1, axis=0)
    y = Q[:, 1][:, None]
    between = (A[:, 1] > y) != (B[:, 1] > y)
    dy = B[:, 1] - A[:, 1]
    dy = np.where(np.abs(dy) < 1e-300, 1e-300, dy)
    at = A[:, 0] + (y - A[:, 1]) / dy * (B[:, 0] - A[:, 0])
    return ((Q[:, 0][:, None] < at) & between).sum(1) % 2 == 1


def _polyline_folds(P):
    """Whether a 2D path crosses or doubles back over itself (all pairs: such paths are short)."""
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    n = len(P) - 1
    for i in range(n):
        a, b = P[i], P[i + 1]
        # (back along the segment before)
        if i and np.dot(a - P[i - 1], b - a) < 0 and abs(side(P[i - 1], a, b)) <= 1e-9 * max(np.dot(b - a, b - a), 1e-30):
            return True
        for j in range(i + 2, n):
            c, d = P[j], P[j + 1]
            if side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0:
                return True
    return False


class _ChainRuler:
    """How far points are from a long chain, and how far along it, at a cost that grows with the chain and not with its
    square: the nearest of a set of markers along the chain, then the segments round that marker. A long segment gets more
    markers, as many as its length needs at the chain's own average spacing, so a point near it finds it and not a closer corner
    of the next turn of a helix."""

    def __init__(self, P):
        from scipy.spatial import cKDTree

        self.P = P
        self.AB = np.diff(P, axis=0)
        length = np.linalg.norm(self.AB, axis=1)
        self.L2 = np.maximum(length * length, 1e-30)
        self.cum = np.r_[0.0, np.cumsum(length)]
        step = self.cum[-1] / (4.0 * len(length))
        per = np.maximum(1, np.ceil(length / max(step, 1e-30)).astype(np.int64))
        self.owner = np.repeat(np.arange(len(length)), per)
        at = (np.arange(len(self.owner)) - np.repeat(np.r_[0, np.cumsum(per)[:-1]], per) + 0.5) / np.repeat(per, per)
        self.tree = cKDTree(P[self.owner] + at[:, None] * self.AB[self.owner])

    def place(self, Q):
        """(distance to the chain, position along it) of each point."""
        _, near = self.tree.query(Q, workers=-1)
        near = self.owner[near]
        best, where = None, None
        for shift in (-1, 0, 1):
            i = np.clip(near + shift, 0, len(self.AB) - 1)
            t = np.clip(((Q - self.P[i]) * self.AB[i]).sum(axis=1) / self.L2[i], 0.0, 1.0)
            d = np.linalg.norm(Q - (self.P[i] + t[:, None] * self.AB[i]), axis=1)
            pos = self.cum[i] + t * (self.cum[i + 1] - self.cum[i])
            if best is None:
                best, where = d, pos
            else:
                closer = d < best
                best, where = np.where(closer, d, best), np.where(closer, pos, where)
        return best, where


def _interpolated(Q, closed):
    """A cubic through these points (periodic for a closed chain), or None."""
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.collections import HArray1_gp_Pnt

    pts = Q[:-1] if closed else Q
    h = HArray1_gp_Pnt(1, len(pts))
    for i, p in enumerate(pts):
        h.SetValue(i + 1, _pnt(p))
    try:
        it = GeomAPI_Interpolate(h, bool(closed), 1e-9)
        it.Perform()
        return it.Curve() if it.IsDone() else None
    except Exception as fault:
        faults.caught(fault)
        return None


def _fit_long_chain(P, tol, closed):
    """A smooth curve for a chain too long to start from every point: the points it starts from, and more of the chain's own
    wherever the curve strays from the chain by more than ``tol``, either way (a curve near the chain can still skip a notch of
    it), or doubles back along it. So its length never decides: only how far the curve is from the mesh's path does, and a border
    takes as many points as its shape needs. Returns (curve, deviation), or (None, 0.0) if even every point won't do."""
    from OCP.Approx import Approx_ChordLength

    idx = np.unique(np.r_[np.linspace(0, len(P) - 1, CHAIN_START).astype(int), len(P) - 1])
    ruler = _ChainRuler(P)
    slack = 0.25 * tol

    def measure(curve, points):
        S = _sample_curve(curve, max(4 * points, 3 * len(P)))
        ds, pos = ruler.place(S)
        dp, _ = _ChainRuler(S).place(P)
        back = np.nonzero(np.diff(pos[1:-1]) < -slack)[0] + 1
        return ds, pos, dp, back

    if not closed:
        # (the approximation, which takes the fewest poles, while the first points will do)
        Q = P[idx]
        arr = Array1_gp_Pnt(1, len(Q))
        for i, p in enumerate(Q):
            arr.SetValue(i + 1, _pnt(p))
        try:
            ap = GeomAPI_PointsToBSpline(arr, Approx_ChordLength, 3, 3, GeomAbs_C2, max(tol * 0.25, 1e-7))
            if ap.IsDone():
                curve = ap.Curve()
                ds, _, dp, back = measure(curve, len(Q))
                if max(ds.max(), dp.max()) <= tol and not len(back):
                    return curve, float(max(ds.max(), dp.max()))
        except Exception as fault:
            faults.caught(fault)
    # (each round halves the spans that stray, so the rounds grow with the log of the chain's length)
    for _ in range(int(np.ceil(np.log2(len(P)))) + 2):
        curve = _interpolated(P[idx], closed)
        if curve is None:
            return None, 0.0
        ds, pos, dp, back = measure(curve, len(idx))
        gap = float(max(ds.max(), dp.max()))
        if gap <= tol and not len(back):
            return curve, gap
        corners = ruler.cum[idx]
        spans = np.r_[np.searchsorted(corners, pos[ds > tol]) - 1, np.searchsorted(corners, pos[back]) - 1,
                      np.searchsorted(idx, np.nonzero(dp > tol)[0]) - 1]
        spans = np.unique(np.clip(spans, 0, len(idx) - 2))
        wide = spans[idx[spans + 1] - idx[spans] >= 2]
        if not len(wide):
            return None, 0.0
        idx = np.union1d(idx, (idx[wide] + idx[wide + 1]) // 2)
    return None, 0.0


def _through_every_point(P):
    """One edge of straight pieces along the chain, every point of it. It can't stray: it is the mesh's path."""
    from OCP.Geom import Geom_BSplineCurve
    from OCP.collections import Array1_double, Array1_int

    n = len(P)
    poles = Array1_gp_Pnt(1, n)
    knots = Array1_double(1, n)
    mults = Array1_int(1, n)
    tpar = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    for i in range(n):
        poles.SetValue(i + 1, _pnt(P[i]))
        knots.SetValue(i + 1, float(tpar[i]))
        mults.SetValue(i + 1, 2 if i in (0, n - 1) else 1)
    try:
        return Geom_BSplineCurve(poles, knots, mults, 1), 0.0
    except Exception as fault:
        faults.caught(fault)
        return None, 0.0


def fit_chain_curve(P, tol):
    """Best smooth curve through a chain of points: chord-length cubic
    approximation, then cubic interpolation (periodic for closed chains),
    then an exact linear B-spline. Candidates must stay within ``tol`` of
    the polyline and advance monotonically along it.
    Returns (curve, deviation)."""
    from OCP.Approx import Approx_ChordLength

    P = np.asarray(P, dtype=np.float64)
    keep = np.r_[True, np.linalg.norm(np.diff(P, axis=0), axis=1) > 1e-9]
    P = P[keep]
    if len(P) < 2:
        return None, 0.0
    closed = len(P) > 3 and np.linalg.norm(P[0] - P[-1]) < 1e-9
    if len(P) > CHAIN_START:
        curve, dev = _fit_long_chain(P, tol, closed)
        return (curve, dev) if curve is not None else _through_every_point(P)
    Q = P
    nsamp = max(40, 4 * len(Q))
    slack = 0.25 * tol
    cands = []
    arr = Array1_gp_Pnt(1, len(Q))
    for i, p in enumerate(Q):
        arr.SetValue(i + 1, _pnt(p))
    if closed and len(Q) >= 4:
        curve = _interpolated(Q, True)
        if curve is not None:
            cands.append(curve)
    if len(Q) >= 3 and not closed:
        try:
            ap = GeomAPI_PointsToBSpline(arr, Approx_ChordLength, 3, 3, GeomAbs_C2, max(tol * 0.25, 1e-7))
            if ap.IsDone():
                cands.append(ap.Curve())
        except Exception as fault:
            faults.caught(fault)
        curve = _interpolated(Q, False)
        if curve is not None:
            cands.append(curve)
    for c in cands:
        S = _sample_curve(c, nsamp)
        dev = _polyline_distance(S, P)
        if dev <= tol and _monotonic_along(S, P, slack):
            return c, dev
    return _through_every_point(P)


def _fit_circle_3d(P):
    if len(P) < 3:
        return None
    c = P.mean(0)
    _, s, vt = np.linalg.svd(P - c, full_matrices=False)
    n = vt[2]
    u, v = vt[0], vt[1]
    Q = np.stack([(P - c) @ u, (P - c) @ v], axis=1)
    from .fitting import fit_circle_2d

    fc = fit_circle_2d(Q)
    if fc is None:
        return None
    c2, r = fc
    center = c + c2[0] * u + c2[1] * v
    rel = P - center
    radial = np.linalg.norm(rel - np.outer(rel @ n, n), axis=1)
    err = max(float(np.abs(radial - r).max()), float(np.abs(rel @ n).max()))
    return center, n, r, err


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------


def segment_shell(shell, tol, report=NULL):
    seg = Segmenter(shell.V, shell.F, shell.topo, tol, report)
    regions = seg.run()
    return seg, regions


def _self_crossing_regions(asm):
    """Region numbers whose constructed face contains a crossing wire (or whose check crashed:
    meshguard.self_crossings, so that a crash fails only that face)."""
    from . import meshguard

    rs = list(asm.region_faces)
    found = meshguard.self_crossings([asm.region_faces[r] for r in rs])
    return {r for r, hits in zip(rs, found) if hits}


def _join_level_planes(seg, labels, regions):
    """Neighbouring planes facing one way and a hair apart in level are one plane, drawn once: Rak Uni's
    floor came as planes at 4.398 and 4.400 mm. Two parallel planes have no line to meet in, so the
    border between them was a path along the mesh, which crossed the outline, and the crossing
    turned the 1,746 mm2 floor into B-spline cells. (Regions built as flat faces of their own
    triangles stay as they are.)"""
    parent = list(range(len(regions)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    nb = seg.nb.reshape(-1)
    a = np.repeat(labels, 3)
    b = np.where(nb >= 0, labels[np.maximum(nb, 0)], -1)
    m = (a != b) & (a >= 0) & (b >= 0)
    pairs = np.unique(np.sort(np.stack([a[m], b[m]], 1), axis=1), axis=0)
    step = LEVEL_STEP * seg.tol
    joined = False
    for x, y in pairs:
        x, y = int(x), int(y)
        rx, ry = regions[x], regions[y]
        if rx.kind != "plane" or ry.kind != "plane" or rx.facet or ry.facet or rx.cut is not None or ry.cut is not None:
            continue
        c = float(rx.prim.n @ ry.prim.n)
        if abs(c) < 1.0 - 1e-9 or c * rx.sense * ry.sense < 0:
            continue
        if abs(float((ry.prim.origin - rx.prim.origin) @ rx.prim.n)) <= step:
            fx, fy = find(x), find(y)
            if fx != fy:
                parent[fy] = fx
                joined = True
    if not joined:
        return labels, regions
    # (on copies: build_cad_shell keeps the labels and regions it passed in, to go back to when a
    # later try is refused. Altered in place, they no longer agreed, and the next demotion left 185
    # triangles of a Raspberry Pi case with no region, which ended CAD faces for the whole part)
    labels = labels.copy()
    regions = [copy.copy(r) for r in regions]
    area = [float(seg.fa[np.asarray(r.faces, dtype=np.int64)].sum()) if len(r.faces) else 0.0 for r in regions]
    groups = {}
    for r in range(len(regions)):
        groups.setdefault(find(r), []).append(r)
    for members in groups.values():
        if len(members) < 2:
            continue
        # (the biggest keeps its plane: the most of the mesh lies on it)
        keep = max(members, key=lambda r: area[r])
        for r in members:
            if r != keep and len(regions[r].faces):
                faces = np.asarray(regions[r].faces, dtype=np.int64)
                labels[faces] = keep
                regions[keep].faces = np.r_[np.asarray(regions[keep].faces, dtype=np.int64), faces]
                regions[r].faces = np.zeros(0, dtype=np.int64)
        # (the step between the levels is its own row of slivers, walls 0.002 mm high, facing sideways:
        # they are the plane too, or it has its own border along both sides of them)
        # (between the levels themselves, as well as near the plane kept: on Rak N the floor kept 4.400,
        # a step down to 4.395 stood past the limit from it, stayed out, and with the floor on both its
        # sides, the outline ran 2.6 mm out along it and straight back, which crosses itself)
        prim = regions[keep].prim
        levels = [float((regions[r].prim.origin - prim.origin) @ prim.n) for r in members]
        low, high = min(levels) - step, max(levels) + step
        for _ in range(4):
            own = np.asarray(regions[keep].faces, dtype=np.int64)
            cand = seg.nb[own].reshape(-1)
            cand = np.unique(cand[cand >= 0])
            cand = cand[labels[cand] != keep]
            if not len(cand):
                break
            T = seg.V[seg.F[cand]].reshape(-1, 3)
            level = ((T - prim.origin) @ prim.n).reshape(-1, 3)
            on = (level.min(1) >= low) & (level.max(1) <= high)
            take = cand[on]
            if not len(take):
                break
            for f in take:
                q = labels[f]
                regions[q].faces = np.asarray(regions[q].faces, dtype=np.int64)
                regions[q].faces = regions[q].faces[regions[q].faces != f]
            labels[take] = keep
            regions[keep].faces = np.r_[np.asarray(regions[keep].faces, dtype=np.int64), take]
    seg.label, seg.regions = labels, regions
    seg._drop_empty()
    # (taking a row of slivers can leave a neighbour in two pieces)
    seg._rebuild_regions(refit=False, merge=False)
    return seg.label, seg.regions


def _small_neighbours_crossing(seg, asm, r):
    """The small regions beside a border of region r that crosses another, which go to freeform in
    its place. Main plate's whole wall of 358 mm2 (the designer's one cylinder) was cut into
    B-spline cells, which no CAD program edits as a wall, for a crossing at one corner beside a
    face of 1.6 mm2 fitted a little wrong; those cells' edges were the worst of the file."""
    from . import meshguard

    face = asm.region_faces[r]
    area = float(seg.fa[np.asarray(asm.regions[r].faces, dtype=np.int64)].sum())
    out = set()
    for hit in meshguard.self_crossings([face])[0]:
        # (a check that died names no edges: nothing to hand on)
        named = [e for e in (hit.e1, hit.e2) if e is not None]
        for c in asm.rg.chains:
            if not c.edges or r not in (c.left, c.right):
                continue
            if any(x.IsSame(e) for x in c.edges for e in named):
                q = c.right if c.left == r else c.left
                if q < 0 or q == r:
                    continue
                o = asm.regions[q]
                if o.kind != "freeform" and float(seg.fa[np.asarray(o.faces, dtype=np.int64)].sum()) <= SMALL_BESIDE * area:
                    out.add(q)
    return out


def build_cad_shell(shell, tol, report=NULL, max_rounds=6, fit_rounds=3, detail=0.6, seg=None, tidy=True, tidied=None):
    """Segment + build. Analytic faces that cannot be built validly, or that
    don't follow the mesh (checked in the first ``fit_rounds`` rounds), are
    replaced by fitted freeform (B-spline) faces and the shell is rebuilt;
    freeform faces that fail are split into smaller cells. The later rounds
    only clean up faces that are still invalid (often tiny slivers left by
    the splitting, which go to a neighbouring face)."""
    from OCP.BRepCheck import BRepCheck_Analyzer

    from .fidelity import face_blown_up

    if seg is None or abs(seg.tol - tol) > 1e-12 * max(1.0, tol):
        seg = Segmenter(shell.V, shell.F, shell.topo, tol, report)
        seg.detail = detail
        regions = seg.run()
    else:
        regions = seg.regions
    labels = seg.label.copy()
    replaced = 0
    inaccurate = 0
    crossed_this_round = False
    fit_instead = set()  # borders (first and last vertex) fitted to the mesh, as exact arcs they crossed at a cusp
    worked_out = {}     # what each border between two surfaces came to, for the later rounds (_exact_chain_edge)

    def build(labels, regions):
        # (first, so a plane joined here is cut where it pinches, and the pieces of one cut keep one surface)
        if EXACT_EDGES:
            # (tidied, if given, is told when this changed a surface; tidy=False: see pipeline, _attempt_untidied)
            if tidy:
                before = [r.prim for r in regions]
                regularise(seg, labels, regions)
                if tidied is not None and any(a is not r.prim for a, r in zip(before, regions)):
                    tidied.append(True)
            labels, regions = _join_level_planes(seg, labels, regions)
        labels, regions = split_pinched_regions(seg, labels, regions)
        if TURN_SPLIT:
            labels, regions = split_turns(seg, labels, regions)
        # single triangles cut off from their region (every neighbour in
        # another) make a face OpenCASCADE can't build: they join a
        # neighbour (the ring of a printed mount came out as slivers
        # without this)
        seg.label, seg.regions = labels, regions
        seg._flat_slivers_to_long_edge()
        seg._absorb_isolated_fragments()
        seg._absorb_folded_islands()
        labels, regions = seg.label, seg.regions
        # those slivers can have been all that joined a region's two fans round a vertex, so
        # the pinch only shows now (Rak Uni: a hole tangent to the wall, whose 0.002 mm step
        # of slivers belonged to the floor; given to the wall, they left the floor's outline
        # and the hole's meeting at one vertex, and the file read back with crossing wires)
        for _ in range(PINCH_AGAIN):
            labels2, regions2 = split_pinched_regions(seg, labels, regions)
            if len(regions2) == len(regions):
                break
            seg.label, seg.regions = labels2, regions2
            seg._flat_slivers_to_long_edge()
            seg._absorb_isolated_fragments()
            seg._absorb_folded_islands()
            labels, regions = seg.label, seg.regions
        rg = RegionGraph(shell.V, shell.F, labels, shell.topo)
        asm = CADAssembler(shell.V, rg, regions, tol, report)
        asm.worked_out = worked_out
        asm.fit_instead = fit_instead
        faces = asm.build(lambda f: report.progress(0.6 + 0.35 * f))
        _settle_tolerances(asm.shell_from_faces(faces), tol)
        bad = set(r for r, _ in asm.failed_regions)
        for r, f in asm.region_faces.items():
            if not BRepCheck_Analyzer(f).IsValid():
                # an edge can be left with no tolerance at all (a scanned
                # plate's counterbore circle: both its faces were invalid
                # only because of it); settling the face's own tolerances
                # once more fixes that, instead of replacing the face
                _settle_tolerances(f, tol)
            # (a face whose surface blew up passes the validity check)
            if not BRepCheck_Analyzer(f).IsValid() or face_blown_up(f):
                bad.add(r)
        # a face can be valid here and broken in the saved STEP file read
        # back (a STEP file keeps no edge tolerances: an edge saved with 0.1
        # reads back with 1e-7): such faces are built again like broken ones
        # (a straightened border left one on the owner's mount, and the
        # whole mount fell back to smooth surfaces)
        broken = _broken_when_saved(asm) - bad
        # (once for each such cusp, up to CUSP_ROUNDS, before the face is given up)
        fresh = {c for r in broken for c in _cusp_crossing(asm, r)} - fit_instead
        if fresh and len(fit_instead) < 2 * CUSP_ROUNDS:
            fit_instead.update(fresh)
            report.log(f"{len(fresh)} exact edges crossing at a cusp are fitted to the mesh instead")
            return build(labels, regions)
        if broken:
            report.log(f"{len(broken)} face(s) that would be broken in the saved file are built again")
            bad |= broken
        return labels, regions, asm, faces, bad

    for rnd in range(max_rounds):
        labels, regions, asm, faces, bad = build(labels, regions)
        # A wire crossing is proof that a face boundary is not representable;
        # before turning that whole face into freeform cells, try a local
        # re-labelling of only the crossing face's border strips.  The trial
        # is retained only when rebuilding proves it removed a crossing and
        # did not add invalid faces.
        crossing = _self_crossing_regions(asm)
        if crossing and not crossed_this_round:
            crossed_this_round = True
            old_label, old_regions = seg.label, seg.regions
            seg.label, seg.regions = labels.copy(), list(regions)
            if seg._absorb_self_crossing_strips(crossing):
                trial_labels, trial_regions, trial_asm, trial_faces, trial_bad = build(seg.label.copy(), seg.regions)
                # Accept if crossings decreased and no *new* faces went invalid.
                # Faces already in bad are going to be replaced anyway, so only
                # newly-invalid faces count against the trial.
                new_bad = trial_bad - bad
                if (len(_self_crossing_regions(trial_asm)) < len(crossing)
                        and not new_bad):
                    labels, regions, asm, faces, bad = trial_labels, trial_regions, trial_asm, trial_faces, trial_bad
                    report.log("Removed border strips that caused a self-crossing CAD wire")
                else:
                    seg.label, seg.regions = old_label, old_regions
            else:
                seg.label, seg.regions = old_label, old_regions
        crossed_this_round = False  # reset for next round
        # (a big surface isn't to pay for a small neighbour's corner: see _small_neighbours_crossing)
        spared = set()
        for r in _self_crossing_regions(asm) & bad:
            small = _small_neighbours_crossing(seg, asm, r)
            if small:
                bad.discard(r)
                bad |= small
                spared.add(r)
        if rnd < min(fit_rounds, max_rounds - 1):
            # valid faces that still don't follow their part of the mesh (a
            # surface that bulges between the mesh's vertices, a face whose
            # edges tear it away from the mesh) are replaced the same way
            # (a face that crosses itself can't be meshed to compare: it gets its chance next round)
            off = _inaccurate_regions(seg, labels, asm, tol, skip=bad | spared) - bad - spared
            if off:
                report.log(f"{len(off)} surface(s) don't follow the mesh closely enough and are fitted again")
                inaccurate += len(off)
                bad |= off
        else:
            off = set()
        if not bad or rnd == max_rounds - 1:
            break
        replaced += len(bad)
        # a freeform face that still strays from the mesh becomes flat faces
        # (when its triangles are few, and big enough to be faces of their own)
        # (a swept surface that strays becomes freeform patches like any
        # other surface, not flat facets)
        # (a bigger one is cut up and fitted again: flat faces are the last resort, and mount_PETG got 1,159 of them from one surface)
        flat = {r for r in off if r < len(regions) and regions[r].kind == "freeform"
                and not hasattr(regions[r].prim, "motion") and len(regions[r].faces) <= FLAT_FEW
                and _facets_ok(seg, regions[r].faces)}
        # (a pooled surface whose face didn't build, or strayed from the mesh, goes back to the pieces it was made
        # of, which get their own chance: the join was wrong there, however well the surface held the triangles.
        # (2026-11-06: test12's 29 mm torus was a fillet strip of the segmentation's own, not a pooled surface; this rule
        # stands on its own: a join that doesn't build was wrong, and the pieces get their chance)
        back = {r for r in bad - flat if r < len(regions) and regions[r].pooled and _parts_cover(regions[r])}
        if back:
            report.log(f"{len(back)} pooled surface(s) didn't build and went back to their pieces")
            labels, regions = _unpool(labels, regions, back)
            bad, flat = _renumbered(bad, back), _renumbered(flat, back)
        labels, regions = _demote(seg, labels, regions, bad - flat, split_more=rnd > 0, facet=flat)
    # Splitting freeform faces can leave slivers too small to be valid faces;
    # those still broken at the end go to a neighbouring face (no new faces
    # are made, so this can't leave new slivers) and the shell is built once more.
    tiny = {r for r in bad if r < len(regions) and _too_small_for_face(seg, labels, regions[r].faces)}
    if tiny:
        labels2, regions2 = _demote(seg, labels, regions, tiny)
        labels2, regions2, asm2, faces2, bad2 = build(labels2, regions2)
        if len(bad2) < len(bad):
            labels, regions, asm, faces, bad = labels2, regions2, asm2, faces2, bad2
    # Faces still broken after every round become flat faces made of their own
    # triangles, as stl2curves leaves a patch it can't build faceted (merged engine):
    # one 5-triangle freeform cell, the only invalid face of 356, failed Rak N-USB's
    # whole body, which then went to another method (775 faces instead of 356).
    last = {r for r in bad if r < len(regions) and _facets_ok(seg, regions[r].faces)}
    if last:
        labels2, regions2 = _demote(seg, labels, regions, set(), facet=last)
        labels2, regions2, asm2, faces2, bad2 = build(labels2, regions2)
        # (the new flat faces can break the neighbour they now meet at a corner: on mount_PETG a strip's flat faces left its 9-triangle
        # cylinder invalid instead, one for one, and the whole part stayed invalid)
        for _ in range(LAST_ROUNDS):
            more = {r for r in bad2 if r < len(regions2) and not getattr(regions2[r].prim, "facet", False)
                    and _facets_ok(seg, regions2[r].faces)}
            if not bad2 or len(bad2) < len(bad) or not more:
                break
            labels2, regions2 = _demote(seg, labels2, regions2, set(), facet=more)
            labels2, regions2, asm2, faces2, bad2 = build(labels2, regions2)
        if len(bad2) < len(bad):
            report.log(f"{len(last)} face(s) that still wouldn't build became flat faces of their own triangles")
            labels, regions, asm, faces, bad = labels2, regions2, asm2, faces2, bad2
    # The rounds check each face written out with the others as a loose compound; the
    # saved file holds them as one shell, whose neighbours share each edge's single curve,
    # and that can break faces the compound check passes (and pass ones it flags: on
    # stl2step's test17 the two disagreed both ways). Checked the shell's way during the
    # rounds, faces that later rounds would have mended anyway were rebuilt (test17: 200
    # faces became 225), so it is done once, here: faces still broken that way become
    # flat faces of their own triangles, which follow the mesh exactly (Rak Uni-USB: two
    # freeform faces broke on read-back and the whole merged result was lost).
    only_unmeshable = set()
    saved = {r for r in _broken_when_saved(asm, shell=True, unmeshable=only_unmeshable) if r < len(regions)}
    if saved:
        # (a face that is valid but can't be meshed is rebuilt as a surface, never as flat facets:
        # Tuna, 2026-11-01, "no facets, ever"; only invalid faces keep that older last resort)
        flat = {r for r in saved - only_unmeshable if _facets_ok(seg, regions[r].faces)}
        labels2, regions2 = _demote(seg, labels, regions, saved - flat, facet=flat)
        labels2, regions2, asm2, faces2, bad2 = build(labels2, regions2)
        if len(_broken_when_saved(asm2, shell=True)) < len(saved) and len(bad2) <= len(bad):
            report.log(f"{len(saved)} face(s) that would be broken in the saved file were built again")
            labels, regions, asm, faces, bad = labels2, regions2, asm2, faces2, bad2
    # A flat face of a region's own triangles can still be broken: its triangles were grouped
    # within FACET_GROUP of its plane, so its corners stand off that plane by up to as much as
    # its edges' tolerance, and the check fails on the tie (Solar Arm Mount: 0.0054 mm against
    # 0.0054, and the whole part was saved faceted). Those regions are built once more with
    # only truly coplanar triangles sharing a face (FACET_FLAT): a lone triangle is exactly flat.
    strict = {r for r, f in asm.region_faces.items()
              if r < len(regions) and getattr(regions[r].prim, "facet", False)
              and not BRepCheck_Analyzer(f).IsValid()}
    if strict:
        labels2, regions2 = _demote(seg, labels, regions, set(), facet=strict, strict=strict)
        labels2, regions2, asm2, faces2, bad2 = build(labels2, regions2)
        if len(bad2) < len(bad):
            report.log(f"{len(strict)} flat face(s) that still wouldn't build were split into truly flat pieces")
            labels, regions, asm, faces, bad = labels2, regions2, asm2, faces2, bad2
    if replaced:
        report.log(f"{replaced} surface(s) rebuilt as freeform (B-spline) faces")
    bad_faces = [f for f in faces if not BRepCheck_Analyzer(f).IsValid()]
    if bad_faces:
        # (the area of each face's own triangles, in the order of faces: the repair must
        # not turn a face inside out, see _heal_faces)
        mesh_area = [float(seg.fa[np.asarray(regions[r].faces)].sum()) if r < len(regions) else float("inf")
                     for r in asm.region_faces]
        faces = _heal_faces(asm, faces, bad_faces, tol, report, mesh_area)
    return asm, faces, regions


def _far_on_own_face(faces, reg_of, d, near, rng):
    """Each region's face with the points past ``near`` and those of them to measure: all of them, or past
    TRUE_FACE_POINTS an even random share, so a count of far points stays a fair count. (A point takes under a
    millisecond. Measuring a few of them and letting their worst stand for the rest hid a real dent among the
    mesher's far points: 3 far points among 1,000, in 5 of 5 synthetic trials.)"""
    far = np.nonzero(d > near)[0]
    for r in np.unique(reg_of[far]):
        face = faces.get(int(r))
        if r < 0 or face is None:
            continue
        mine = far[reg_of[far] == r]
        yield face, mine, mine if len(mine) <= TRUE_FACE_POINTS else rng.choice(mine, TRUE_FACE_POINTS, replace=False)


def _stand_for_rest(d, mine, asked, rng):
    if len(asked) < len(mine):
        rest = np.setdiff1d(mine, asked, assume_unique=True)
        d[rest] = rng.choice(d[asked], len(rest))


def _measure_on_own_face(faces, reg_of, P, d, near, rng):
    """Points of a region's triangles found far from the tessellated shape are measured again against
    the region's own true face: OpenCascade's mesher can leave out part of a face that holds them, and the
    gap then is the mesher's, not the face's. Alters d in place."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    for face, mine, asked in _far_on_own_face(faces, reg_of, d, near, rng):
        for i in asked:
            ds = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(_pnt(P[i])).Vertex(), face)
            if ds.IsDone():
                d[i] = float(ds.Value())
        _stand_for_rest(d, mine, asked, rng)


def _chords_let_off(seg, regions, reg_of, tris, d, near):
    """A far point whose mesh triangle has its corners on the region's own surface is the mesh's chord, not the face's error: a
    mesh is a polyhedron inscribed in the design, its corners on the true surfaces and its sides cutting across them. A
    cylinder stands off its chords, and a plane's face ends at the true curve where its triangles end at a chord
    (obj_2_02: 0.6 mm on a mesh whose typical sag is 0.09, and ten surfaces rebuilt). Only for simple surfaces: a freeform
    one can really bulge between corners, and that's what this check is for. Only up to tol_mid, the most the mesh's chords
    stand off anything: past that it isn't a chord. Alters d in place."""
    far = np.nonzero((d > near) & (d <= seg.tol_mid))[0]
    for r in np.unique(reg_of[far]):
        if r < 0 or r >= len(regions):
            continue
        prim = regions[r].prim
        if regions[r].kind not in CHORD_KINDS or hasattr(prim, "geom"):
            continue
        mine = far[reg_of[far] == r]
        off = prim.distance(seg.V[seg.F[tris[mine]]].reshape(-1, 3)).reshape(-1, 3).max(axis=1)
        d[mine[off <= seg.tol]] = 0.0


def _face_points_on_true_face(faces, reg_of, Q, d, near, mesh, exact, rng, tris=None):
    """Points sampled on a face's triangles and found far from the mesh are moved onto the true face and measured
    again: those triangles are only what OpenCascade's mesher made of the face. Its chords cross a curved
    face, and it leaves a face's outline where the edge stands off the surface by its tolerance. x-end-clip's
    wall of 579 mm2, r 9.7, has every mesh corner on its surface within 1e-6 mm, yet meshed at 0.002 mm it held a
    triangle 34 mm long whose middle stands 0.47 mm inside it, and the check rebuilt that wall as B-spline cells.
    A face that really leaves the mesh is as far off on the true face. (Idea and first version: helper regr,
    2026-11-03.) Alters d, and tris (each point's nearest mesh triangle) if given, in place."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    for face, mine, asked in _far_on_own_face(faces, reg_of, d, near, rng):
        at, on = [], []
        for i in asked:
            ds = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(_pnt(Q[i])).Vertex(), face)
            if ds.IsDone() and ds.NbSolution() > 0:
                p = ds.PointOnShape2(1)
                at.append(i)
                on.append((p.X(), p.Y(), p.Z()))
        if on:
            got, tri = mesh.nearest(np.array(on), exact_above=exact)
            d[at] = got
            if tris is not None:
                tris[at] = tri
        _stand_for_rest(d, mine, asked, rng)


def _inaccurate_regions(seg, labels, asm, tol, share=0.05, count=3, far_count=None, skip=()):
    """Regions whose built face strays from the mesh triangles it stands
    for, found by measuring both ways (see fidelity.py): points of the face
    far from the mesh (bulges, flaps, torn edges) and points of the region's
    triangles far from the shape (dents, missing material).

    Distances are in units of the tolerance the result is judged by
    (fidelity.Verdict): ``tol``, or the mesh's own chordal deflection when
    it is coarser (a true cylinder stands off the flat sides of a coarse
    mesh's 16-gon, and that is right). A region is inaccurate when at least
    ``count`` of its points are beyond Verdict.P999_K tolerances (a bulge
    or a flap), or more than ``share`` of them beyond Verdict.P99_K. When the
    shell as a whole would still fail the final check (too many far points
    spread thinly over many faces), the faces holding most of them count
    too, until it would pass: fixing those beats losing the whole CAD
    result, and the rest stay as they are."""
    from .fidelity import Surface, Verdict, mesh_surface, sample, tessellate_faces

    # (faces already known to be broken are replaced anyway; meshing them
    # can fail)
    # (a designer's surface snapped over a coarse export stands off its chords by design)
    items = [(r, f) for r, f in asm.region_faces.items() if r not in skip
             and not (r < len(asm.regions) and getattr(asm.regions[r].prim, "designed", False))]
    if not items:
        return set()
    rng = np.random.default_rng(0)
    V, F = seg.V, seg.F
    SV, SF, owner = tessellate_faces([f for _, f in items], tol * 0.25)
    if len(SF) == 0:
        return set()
    own_region = np.array([r for r, _ in items], dtype=np.int64)[owner]
    mesh, _, spacing = mesh_surface(V, F)
    # (the tolerance the result is judged by: see fidelity.mesh_kind)
    allow = max(tol, mesh.deflection, mesh.noise)
    shape = Surface(SV, SF, max(spacing, tol * 0.5), rng)
    n = int(np.clip(20 * len(F), 20_000, 150_000))
    # face -> mesh
    Q, qt = sample(SV, SF, n, rng)
    dq, dq_tri = mesh.nearest(Q, exact_above=0.5 * allow)
    # mesh -> shape (by the region the triangle belongs to)
    P, pt = sample(V, F, n, rng)
    dp, _ = shape.nearest(P, exact_above=0.5 * allow)
    near, far = Verdict.P99_K * allow, Verdict.P999_K * allow
    _measure_on_own_face(dict(items), labels[pt], P, dp, near, rng)
    _face_points_on_true_face(dict(items), own_region[qt], Q, dq, near, mesh, 0.5 * allow, rng, dq_tri)
    _chords_let_off(seg, asm.regions, own_region[qt], dq_tri, dq, near)
    _chords_let_off(seg, asm.regions, labels[pt], pt, dp, near)
    out = set()
    for reg_of, d in ((own_region[qt], dq), (labels[pt], dp)):
        # (a surface left out of the shape above has nothing to be measured against: its triangles aren't far from it)
        ok = np.isin(reg_of, own_region)
        reg_of, d = reg_of[ok], d[ok]
        if not len(d):
            continue
        cnt = np.bincount(reg_of)
        n_near = np.bincount(reg_of, weights=(d > near).astype(float), minlength=len(cnt))
        n_far = np.bincount(reg_of, weights=(d > far).astype(float), minlength=len(cnt))
        out |= {int(r) for r in np.nonzero((n_far >= (far_count or count)) | (n_near > np.maximum(share * cnt, count)))[0]}
        # the whole shell: at most 1% of the points beyond P99_K, 0.1%
        # beyond P999_K (with a little margin)
        for nk, limit in ((n_near, 0.008 * len(d)), (n_far, 0.0008 * len(d))):
            left = nk.sum() - sum(nk[r] for r in out if r < len(nk))
            for r in np.argsort(-nk):
                if left <= limit or nk[r] <= 0:
                    break
                if int(r) not in out:
                    out.add(int(r))
                    left -= nk[r]
    return out


def _heal_faces(asm, faces, bad_faces, tol, report, mesh_area=None):
    """Repair the few faces that are still invalid with OpenCASCADE's face
    healer. A shared ReShape context propagates any edge it replaces to the
    neighbouring faces, so the shell stays watertight.

    That propagation can turn a valid neighbour inside out: on a periodic
    surface, an edge given back the other way round leaves the neighbour
    with everything but its patch (Bravo's motor shaft joint: a fillet strip
    beside a repaired sliver became the whole torus, 696 mm2, 8.3 mm outside
    the part, and the solid read back inside out). With ``mesh_area`` (the
    area of each face's own triangles, in the order of ``faces``), a face
    that comes out more than HEAL_GROWTH times that area, and HEAL_GROWTH_MM2
    more, is taken as such: the broken faces sharing an edge with it are
    left unrepaired and the rest are repaired again."""
    try:
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.ShapeBuild import ShapeBuild_ReShape
        from OCP.ShapeFix import ShapeFix_Face
        from OCP.TopAbs import TopAbs_FACE

        def repaired(heal):
            sh = asm.shell_from_faces(faces)
            ctx = ShapeBuild_ReShape()
            for f in heal:
                sff = ShapeFix_Face(f)
                sff.SetContext(ctx)
                sff.SetPrecision(max(tol * 0.1, 1e-7))
                sff.SetMaxTolerance(max(tol * 5, 1e-4))
                sff.Perform()
                fixed = sff.Face()
                if not fixed.IsSame(f):
                    ctx.Replace(f, fixed)
            healed = ctx.Apply(sh)
            out = []
            exp = TopExp_Explorer(healed, TopAbs_FACE)
            while exp.More():
                out.append(TopoDS.Face(exp.Current()))
                exp.Next()
            return out

        heal = list(bad_faces)
        for _ in range(HEAL_TRIES):
            out = repaired(heal)
            grown = _grown_faces(faces, out, mesh_area)
            if not grown:
                break
            left = [f for f in heal if not any(_share_edge(f, faces[i]) for i in grown)]
            report.log(f"Repairing {len(heal) - len(left)} face(s) turned {len(grown)} neighbour(s) inside out, "
                       "so those are left as they were")
            if len(left) == len(heal):
                return faces
            heal = left
        else:
            return faces
        still = sum(1 for f in out if not BRepCheck_Analyzer(f).IsValid())
        if len(out) >= len(faces) - len(bad_faces) and still < len(bad_faces):
            report.log(f"Repaired {len(bad_faces) - still} of {len(bad_faces)} remaining face(s)")
            return out
    except Exception as e:
        faults.caught(e)
        report.log(f"Automatic face repair was skipped ({e})")
    return faces


def _grown_faces(faces, out, mesh_area):
    """Indices of the faces whose repaired version (same position in ``out``:
    ShapeBuild_ReShape.Apply keeps the shell's order) is far bigger than their
    own triangles (_heal_faces). Nothing is found when the counts differ."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    if mesh_area is None or not (len(faces) == len(out) == len(mesh_area)):
        return []
    grown = []
    for i, (f, g) in enumerate(zip(faces, out)):
        if g.IsSame(f):
            continue
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(g, props)
        area, own = abs(props.Mass()), mesh_area[i]
        if area > HEAL_GROWTH * own and area > own + HEAL_GROWTH_MM2:
            grown.append(i)
    return grown


def _share_edge(a, b):
    """Whether faces a and b share an edge."""
    edges = _ShapeMap()
    TopExp.MapShapes_s(a, TopAbs_EDGE, edges)
    exp = TopExp_Explorer(b, TopAbs_EDGE)
    while exp.More():
        if edges.Contains(exp.Current()):
            return True
        exp.Next()
    return False


def split_pinched_regions(seg, labels, regions, max_passes=4):
    """A region whose triangles meet at a vertex in two separate fans (a
    hole touching the outer boundary at one point, two holes touching) is
    cut in two, so that no face's boundary touches itself. Such faces are
    valid in memory but STEP readers tend to mangle them."""
    from .freeform import seeded_partition

    F = seg.F
    nf = len(F)
    twin = seg.topo.twin
    k = np.arange(3 * nf)
    f = k // 3
    v = F.reshape(-1)
    h_in = 3 * f + (k % 3 + 2) % 3
    t = twin[h_in]
    g = np.where(t >= 0, t // 3, 0)
    cen = None
    labels = labels.copy()
    regions = list(regions)
    for _ in range(max_passes):
        lf = labels[f]
        # first triangle of each fan around a vertex (rotation order)
        start = (t < 0) | (labels[g] != lf)
        # (decoded with the count at this point: regions are added below)
        nr = len(regions) + 1
        key = v[start].astype(np.int64) * nr + lf[start]
        ks, counts = np.unique(key, return_counts=True)
        pinch = ks[counts >= 2]
        if not len(pinch):
            break
        if cen is None:
            cen = seg.V[F].mean(1)
        changed = False
        done = set()
        for pk in pinch:
            vv, ll = int(pk // nr), int(pk % nr)
            if ll in done:
                continue
            seeds = np.unique(f[start & (v == vv) & (lf == ll)])
            if len(seeds) < 2:
                continue
            done.add(ll)
            r = regions[ll]
            # The shortest cut from one outline to the other, on any surface: a plane or a strip of
            # cylinder keeps nearly all of itself as one face. Halving a fillet's cylinder at a
            # pinch (Rak N-USB, 104 mm2 cut into 81 and 22 along 33 mm) made one surface two faces
            # with a long shallow border between them: Fusion marked one bad, and all 14 slices
            # along the fillet failed. (Solar Panel Bracket Large went to 1,511 faces when this was
            # first tried at every look; the cut wasn't why, its body sits at the fit check's limit.)
            parts = _shortest_cut(seg, labels, ll, r, vv, seeds[:2])
            if parts is None and r.kind in PERIODIC:
                parts = _half_turns(r, seg.V[vv], cen)
            if parts is None:
                parts = seeded_partition(r.faces, seeds[:2], cen, seg.nb)
            if len(parts) < 2 or not len(parts[0]) or not len(parts[1]):
                continue
            a = Region(r.prim, parts[0], r.sense, r.weak)
            b = Region(r.prim, parts[1], r.sense, r.weak)
            a.facet = b.facet = r.facet
            regions[ll] = a
            labels[parts[1]] = len(regions)
            regions.append(b)
            changed = True
        if not changed:
            break
    return labels, regions


def _shortest_cut(seg, labels, ll, r, vv, seeds):
    """A region whose outlines touch at vertex vv only has to be cut once, from one outline to the
    other, for each piece to have a simple outline: the shortest way along the mesh's edges, not
    through any other outline. The floor keeps nearly all of itself as one face; the cut usually
    takes off a small wedge in the cusp beside the touch (Rak Uni: 3 mm beside a standoff tangent
    to the wall, where cutting half way between the fans took the floor apart in three, along
    53 mm of its triangles' edges). Returns the two parts, or None when no such cut exists."""
    import heapq
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    faces = np.asarray(r.faces)
    topo, nb, V = seg.topo, seg.nb, seg.V
    he = (3 * faces[:, None] + np.arange(3)).ravel()
    a, b = topo.he_from[he], topo.he_to[he]
    other = nb[np.repeat(faces, 3), np.tile(np.arange(3), len(faces))]
    inner = (other >= 0) & (labels[np.maximum(other, 0)] == ll)
    # the outlines, with the touching vertex taken out: the two it joined fall apart
    keep = ~inner & (a != vv) & (b != vv)
    ids, inv = np.unique(np.r_[a[~inner], b[~inner]], return_inverse=True)
    m = int((~inner).sum())
    A, B = inv[:m], inv[m:]
    kept = keep[~inner]
    n, comp = connected_components(coo_matrix((np.ones(int(kept.sum())), (A[kept], B[kept])), shape=(len(ids), len(ids))), directed=False)
    where = {int(v): i for i, v in enumerate(ids)}
    if int(vv) not in where:
        return None
    me = where[int(vv)]
    at = {int(comp[x]) for x, y in zip(A, B) if y == me} | {int(comp[y]) for x, y in zip(A, B) if x == me}
    at.discard(int(comp[me]))
    if len(at) != 2:
        return None
    src, dst = sorted(at)
    bound = {int(v): int(comp[i]) for v, i in where.items() if v != int(vv)}
    # shortest path over the region's inner edges
    adj = {}
    for x, y in zip(a[inner], b[inner]):
        x, y = int(x), int(y)
        if x < y:
            L = float(np.linalg.norm(V[x] - V[y]))
            adj.setdefault(x, []).append((y, L))
            adj.setdefault(y, []).append((x, L))
    dist = {v: 0.0 for v, c in bound.items() if c == src}
    back = {}
    heap = [(0.0, v) for v in dist]
    heapq.heapify(heap)
    end = None
    while heap:
        d, x = heapq.heappop(heap)
        if d > dist[x]:
            continue
        if bound.get(x) == dst:
            end = x
            break
        for y, L in adj.get(x, ()):
            # (not along the outline it starts from, and not through any other outline or the touch)
            if y == int(vv) or bound.get(y, dst) != dst:
                continue
            if d + L < dist.get(y, np.inf):
                dist[y] = d + L
                back[y] = x
                heapq.heappush(heap, (d + L, y))
    if end is None:
        return None
    cut = set()
    x = end
    while x in back:
        cut.add((min(x, back[x]), max(x, back[x])))
        x = back[x]
    # the pieces either side of it
    member = set(map(int, faces))
    seen = {int(seeds[0])}
    stack = [int(seeds[0])]
    while stack:
        f = stack.pop()
        for k in range(3):
            g = int(nb[f, k])
            if g < 0 or g not in member or g in seen:
                continue
            e = (int(topo.he_from[3 * f + k]), int(topo.he_to[3 * f + k]))
            if (min(e), max(e)) in cut:
                continue
            seen.add(g)
            stack.append(g)
    if int(seeds[1]) in seen:
        return None
    first = np.fromiter(seen, dtype=np.int64)
    rest = np.setdiff1d(faces, first)
    # (a piece with no size to speak of can't be a face: cutting half way between the fans is
    # then better; Solar Panel Bracket Large had a wedge of one zero-area triangle)
    for part in (first, rest):
        if _too_small_for_face(seg, labels, part, width_k=1.0):
            return None
    # (a piece of one triangle is a real face here: it must not be absorbed back, or the pinch returns)
    for part in (first, rest):
        if len(part) == 1:
            seg.cusps = getattr(seg, "cusps", set()) | {int(part[0])}
    return [first, rest]


def _half_turns(r, p, cen):
    """A cylinder, cone or torus face that wraps around its axis and touches
    itself at point p is cut into two halves by the angle around the axis,
    starting at p: each half spans about half a turn, which leaves room for
    the surface's seam (a face that wraps almost all the way round gets its
    seam right at the point where it touches itself, and comes out
    invalid). None when the halves wouldn't each be one piece."""
    prim = r.prim
    o = {"cylinder": "a", "cone": "apex", "torus": "c"}[prim.kind]
    o, d = getattr(prim, o), prim.d
    u, v = perp_basis(d)

    def ang(X):
        rel = X - o
        return np.arctan2(rel @ v, rel @ u)

    t = np.mod(ang(cen[r.faces]) - ang(p[None])[0], 2 * np.pi)
    half = t < np.pi
    parts = [r.faces[half], r.faces[~half]]
    if min(len(parts[0]), len(parts[1])) == 0:
        return None
    return parts


FIT_WEIGHT = 30         # border-straightening cut: a triangle moved to the surface it fits a tolerance worse costs this (an edge: ~100; 100 sent test13 faceted)
SLIVER_HEIGHT = 0.05    # tolerances: a triangle this low over its longest edge has no surface of its own (0.01 missed Solar Panel Bracket's)
TURN_SPLIT = True       # cut surfaces winding over a turn into one face a turn
TURN_SNAP = False       # move a cut's corners onto its meridian (off: on the owner's n-mount it made the cut cone pieces stray from the mesh, 231 faces against 193 without it)
TURN_GAP_DEG = 15.0     # degrees: a region whose triangles leave a gap this wide round its axis is under one turn
TURN_SLACK = 0.3        # radians: a boundary loop running further than a turn plus this winds more than once
TURN_CUTS = 72          # meridians tried for the cut, evenly round the axis
MIN_TURN_DEG = 20.0     # degrees: a piece spanning less is a sliver, joined to the piece beside it


CURVED_KINDS = ("cylinder", "cone", "sphere", "torus")


def _axis_of(prim):
    o = {"cylinder": "a", "cone": "apex", "torus": "c"}[prim.kind]
    return np.asarray(getattr(prim, o), dtype=np.float64), np.asarray(prim.d, dtype=np.float64)


def split_turns(seg, labels, regions):
    """A cylinder, cone or torus region that winds more than one turn round its
    axis (the strip of cone between a screw's thread at its tapered tip: 2.5
    turns on the owner's n-mount) has a boundary that overlaps itself in the
    surface's parameters, so its face is invalid. CAD makes one face per turn
    (the n-mount's designer: 360 + 360 + 90 degrees), and so is it cut here:
    at the meridian that crosses the fewest of its triangles, by the turn each
    triangle is on, counted along the region's own triangles (the angle
    unwrapped from one to the next: the n-mount's strip also touches itself
    one turn on, which a cut along the meridian alone leaves joined). Each
    piece keeps the surface; the edge between two pieces is laid on it
    (CADAssembler._on_surface_edge). A sliver of a last turn joins the piece
    beside it. Only regions whose boundary runs more than a turn are cut: a
    band round the axis gets its seam (plan_seams)."""
    cen = None
    cands = []
    for ri, r in enumerate(regions):
        if r.kind not in PERIODIC or len(r.faces) < 3:
            continue
        if cen is None:
            cen = seg.V[seg.F].mean(1)
        o, d = _axis_of(r.prim)
        u, v = perp_basis(d)
        rel = cen[r.faces] - o
        ang = np.arctan2(rel @ v, rel @ u)
        a = np.sort(ang)
        if np.diff(np.r_[a, a[0] + 2 * np.pi]).max() > np.radians(TURN_GAP_DEG):
            continue
        cands.append((ri, o, u, v, ang))
    if not cands:
        return labels, regions
    rg = RegionGraph(seg.V, seg.F, labels, seg.topo)
    labels = labels.copy()
    regions = list(regions)
    for ri, o, u, v, ang in cands:
        multi = False
        for loop in rg.region_loops[ri]:
            vs = rg.loop_vertices(loop)
            P = seg.V[vs + vs[:1]] - o
            w = np.unwrap(np.arctan2(P @ v, P @ u))
            # (how far the loop travels round beyond its net winding: a full ring winds once
            # and travels about once, more only by the width of a notch in its border,
            # which counted as a second turn cut two of test17's plain cylinders in four;
            # a helical strip travels 2.5 turns and comes back to a net 0)
            if np.ptp(w) - abs(w[-1] - w[0]) > 2 * np.pi + TURN_SLACK:
                multi = True
                break
        if not multi:
            continue
        r = regions[ri]
        cut = _turn_pieces(seg, r.faces, o, u, v, ang)
        if cut is None:
            continue
        parts, uc = cut
        x = np.cos(uc) * u + np.sin(uc) * v
        a = Region(r.prim, parts[0], r.sense, r.weak)
        a.facet, a.cut = r.facet, x
        regions[ri] = a
        for p in parts[1:]:
            b = Region(r.prim, p, r.sense, r.weak)
            b.facet, b.cut = r.facet, x
            labels[p] = len(regions)
            regions.append(b)
    return labels, regions


def _turn_pieces(seg, faces, o, u, v, ang):
    """(pieces, cut angle) of a region winding more than a turn, one piece a
    turn (see split_turns), or None when it doesn't come apart."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(faces)
    idx = -np.ones(len(seg.F), dtype=np.int64)
    idx[faces] = np.arange(n)
    nb = seg.nb[faces]
    I = np.repeat(np.arange(n), 3)
    J = np.where(nb.reshape(-1) >= 0, idx[np.maximum(nb.reshape(-1), 0)], -1)
    keep = J > I
    I, J = I[keep], J[keep]
    # the angle unwrapped along a spanning tree of the region's triangles
    adj = [[] for _ in range(n)]
    for i, j in zip(I.tolist(), J.tolist()):
        adj[i].append(j)
        adj[j].append(i)
    th = np.full(n, np.nan)
    for s in range(n):
        if not np.isnan(th[s]):
            continue
        th[s] = ang[s]
        stack = [s]
        while stack:
            i = stack.pop()
            for j in adj[i]:
                if np.isnan(th[j]):
                    th[j] = th[i] + _wrap(ang[j] - ang[i])
                    stack.append(j)
    # the cut: the meridian crossing the fewest triangles, preferring one that
    # leaves no sliver of a turn
    T = seg.V[seg.F[faces]] - o
    ta = np.arctan2(T @ v, T @ u)
    span = np.radians(MIN_TURN_DEG)
    best = None
    for uc in np.arange(TURN_CUTS) * (2 * np.pi / TURN_CUTS):
        rel = _wrap(ta - uc)
        cross = int(((rel.min(1) < 0) & (rel.max(1) > 0) & (np.ptp(rel, 1) < np.pi)).sum())
        turn = np.floor((th - uc) / (2 * np.pi)).astype(np.int64)
        sliver = any(np.ptp(th[turn == k]) < span for k in np.unique(turn))
        key = (sliver, cross)
        if best is None or key < best[0]:
            best = (key, turn, uc)
    _, turn, uc = best
    same = turn[I] == turn[J]
    k, comp = connected_components(coo_matrix((np.ones(int(same.sum())), (I[same], J[same])), shape=(n, n)),
                                   directed=False)
    if k < 2:
        return None
    # a sliver joins the piece it shares most edges with
    alone = set()
    for _ in range(k):
        spans = [np.ptp(th[comp == c]) if (comp == c).any() and c not in alone else np.inf for c in range(k)]
        c = int(np.argmin(spans))
        if spans[c] >= span:
            break
        across = (comp[I] != comp[J]) & ((comp[I] == c) | (comp[J] == c))
        other = np.where(comp[I[across]] == c, comp[J[across]], comp[I[across]])
        if not len(other):
            alone.add(c)        # (a piece of the region touching no other)
            continue
        comp[comp == c] = int(np.bincount(other).argmax())
    pieces = [faces[comp == c] for c in np.unique(comp)]
    return (pieces, float(uc)) if len(pieces) > 1 else None


def _plane_edges_measured(shape):
    """An edge on a plane has no curve on that face for SameParameter to measure, so nothing covers how
    far it stands off the plane, and the check then fails the face: on Rak Uni one 10 mm line, tolerance
    1e-4, stood 0.00016 mm off a 3,054 mm2 floor, which became B-spline patches for it. Its tolerance
    becomes that distance, measured, and no more: a padded tolerance hides real crossings from
    the check. An edge whose tolerance already covers the distance keeps it."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FACE

    builder = BRep_Builder()
    faces = TopExp_Explorer(shape, TopAbs_FACE)
    while faces.More():
        face = TopoDS.Face(faces.Current())
        faces.Next()
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() != GeomAbs_Plane:
            continue
        plane = surface.Plane()
        edges = TopExp_Explorer(face, TopAbs_EDGE)
        while edges.More():
            edge = TopoDS.Edge(edges.Current())
            edges.Next()
            if BRep_Tool.Degenerated_s(edge):
                continue
            curve = BRepAdaptor_Curve(edge)
            a, b = curve.FirstParameter(), curve.LastParameter()
            off = max(plane.Distance(curve.Value(a + (b - a) * i / PLANE_EDGE_SAMPLES)) for i in range(PLANE_EDGE_SAMPLES + 1))
            if off > BRep_Tool.Tolerance_s(edge):
                builder.UpdateEdge(edge, off)


def _overstated_tolerances(shape, tol):
    """SameParameter can state a short arc's tolerance at about its radius, though it lies on its faces
    (Rak N: an arc 2.3 mm long, radius 75 mm, stated 73 mm, measured under 0.05). Spread to its corners,
    that hides or makes crossings on every face those corners touch. An edge stated beyond OVERSTATED
    tolerances takes what is measured against each face it bounds, and its corners what their edges need."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.ShapeFix import ShapeFix_ShapeTolerance
    from OCP.TopAbs import TopAbs_FACE, TopAbs_VERTEX

    limit = OVERSTATED * tol
    # (BRep_Builder can only raise a tolerance)
    setter = ShapeFix_ShapeTolerance()
    worst = {}
    found = []
    faces = TopExp_Explorer(shape, TopAbs_FACE)
    while faces.More():
        face = TopoDS.Face(faces.Current())
        faces.Next()
        surface = None
        edges = TopExp_Explorer(face, TopAbs_EDGE)
        while edges.More():
            edge = TopoDS.Edge(edges.Current())
            edges.Next()
            if BRep_Tool.Tolerance_s(edge) <= limit or BRep_Tool.Degenerated_s(edge):
                continue
            if surface is None:
                surface = BRep_Tool.Surface_s(face)
            curve = BRepAdaptor_Curve(edge)
            a, b = curve.FirstParameter(), curve.LastParameter()
            off = 0.0
            for i in range(PLANE_EDGE_SAMPLES + 1):
                pr = GeomAPI_ProjectPointOnSurf(curve.Value(a + (b - a) * i / PLANE_EDGE_SAMPLES), surface)
                if pr.NbPoints():
                    off = max(off, pr.LowerDistance())
            k = next((j for j, e in enumerate(found) if e.IsSame(edge)), None)
            if k is None:
                found.append(edge)
                k = len(found) - 1
            worst[k] = max(worst.get(k, 0.0), off)
    for k, off in worst.items():
        setter.SetTolerance(found[k], max(off, 1e-7), TopAbs_EDGE)
    if not found:
        return
    # the corners of those edges: as far as any edge's end there, plus that edge's tolerance
    corners = []
    for edge in found:
        vs = TopExp_Explorer(edge, TopAbs_VERTEX)
        while vs.More():
            v = TopoDS.Vertex(vs.Current())
            vs.Next()
            if not any(c.IsSame(v) for c in corners):
                corners.append(v)
    need = [0.0] * len(corners)
    edges = TopExp_Explorer(shape, TopAbs_EDGE)
    while edges.More():
        edge = TopoDS.Edge(edges.Current())
        edges.Next()
        if BRep_Tool.Degenerated_s(edge):
            continue
        curve = BRepAdaptor_Curve(edge)
        ends = (curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter()))
        vs = TopExp_Explorer(edge, TopAbs_VERTEX)
        while vs.More():
            v = TopoDS.Vertex(vs.Current())
            vs.Next()
            k = next((j for j, c in enumerate(corners) if c.IsSame(v)), None)
            if k is not None:
                p = BRep_Tool.Pnt_s(v)
                need[k] = max(need[k], min(p.Distance(ends[0]), p.Distance(ends[1])) + BRep_Tool.Tolerance_s(edge))
    for v, t in zip(corners, need):
        setter.SetTolerance(v, max(t, 1e-7), TopAbs_VERTEX)


def _settle_tolerances(shape, tol):
    """Let OpenCASCADE measure how far each edge really is from its faces
    and raise tolerances to match (standard healing step). On some short
    arcs this over-estimates in memory; STEP files carry no tolerances, so
    the written geometry is unaffected (every result is re-read in tests)."""
    try:
        BRepLib.SameParameter_s(shape, max(tol * 0.01, 1e-7), True)
        # an edge it gives up on keeps its flag unset, which alone makes
        # its faces invalid (a printed mount: 3 of its 13 broken faces);
        # ShapeFix settles those, without raising their tolerance
        ex = TopExp_Explorer(shape, TopAbs_EDGE)
        while ex.More():
            e = TopoDS.Edge(ex.Current())
            if not BRep_Tool.SameParameter_s(e):
                ShapeFix_Edge().FixSameParameter(e)
            ex.Next()
    except Exception as fault:
        faults.caught(fault)
    # (apart: one edge OpenCASCADE can't settle, gp_Dir2d of zero length on Rak Uni, stops the above,
    # and the rest must still hold)
    try:
        _plane_edges_measured(shape)
        BRepLib.UpdateTolerances_s(shape, False)
        _overstated_tolerances(shape, tol)
    except Exception as fault:
        faults.caught(fault)


CUT_CLEARANCE = 2.0     # tolerances: a straight cut across a plane stays this far from everything else on it
READ_BOX_MARGIN = 0.05  # mm: how far a face's box may grow through a STEP file and still be that face's
PINCH_AGAIN = 3         # times build() looks for pinched regions again after the slivers have moved
CELL_SLIVER = 2.0       # tolerances: a freeform cell's mean width (2 x area / outline) at most this is a sliver
CELL_SLIVER_TRIS = 16   # triangles: only small cells count (a long narrow strip of many is a fillet)


def _handed_distance(prim, d):
    """How far a triangle stands off a surface it is being handed to (d), infinite for a
    designed plane it doesn't lie on: on Rak N-USB a sliver 0.157 mm off a clean 605 mm2 wall was
    handed to it for lack of anything nearer, the wall's hole bulged that far, and the wall read
    back valid but couldn't be meshed. Left over, such a triangle stays a freeform piece."""
    near = getattr(prim, "on_design", None)
    return np.inf if near is not None and d > near else d


def _demote(seg, labels, regions, bad, split_more=False, facet=(), strict=()):
    """Replace regions in ``bad`` by freeform B-spline cells. Tiny ones
    (about the size of the tolerance, too small for a valid face of their
    own) hand their triangles to the neighbouring surface that fits them
    best instead. Regions in ``facet`` (freeform cells that still don't
    follow the mesh) become flat faces made from their own triangles, which
    follow it exactly."""
    bad = set(bad) | set(facet)
    facet = set(facet)
    tiny = {r for r in bad if _too_small_for_face(seg, labels, regions[r].faces)}
    keep = [r for r in range(len(regions)) if r not in bad]
    new_regions = [regions[r] for r in keep]
    new_labels = np.full_like(labels, -1)
    for i, r in enumerate(keep):
        new_labels[regions[r].faces] = i
    pending = [int(f) for r in tiny for f in regions[r].faces]
    added = {}
    for _ in range(8):
        left = []
        for f in pending:
            # only a neighbour whose surface holds the triangle within the tolerance
            # (corners, edge midpoints and centre, like _absorb_isolated_fragments): the
            # nearest neighbour at any distance took a tiny plane region 0.52 mm off and
            # then a tiny freeform region 11 mm off into Rak N's spindle torus (R 0.55, r
            # 1.88), whose face then wrapped the whole tube and stood 3.4 mm off the
            # mesh, and the part was saved as flat facets. What fits no neighbour goes
            # the way of the rest below (freeform cells, and flat facets at the end).
            best, bd = -1, seg.tol
            for g in seg.nb[f]:
                rr = new_labels[g] if g >= 0 else -1
                if rr < 0 or rr == best:
                    continue
                d = _handed_distance(new_regions[rr].prim, float(seg._gap(new_regions[rr].prim, np.array([f])).max()))
                if d <= bd:
                    best, bd = rr, d
            if best >= 0:
                new_labels[f] = best
                added.setdefault(best, []).append(f)
            else:
                left.append(f)
        if len(left) == len(pending):
            break
        pending = left
    for rr, fs in added.items():
        old = new_regions[rr]
        reg = Region(old.prim, np.concatenate([old.faces, np.asarray(fs, dtype=np.int64)]), old.sense, old.weak)
        reg.facet = old.facet
        reg.cut = old.cut
        new_regions[rr] = reg
    rest = [r for r in bad if r not in tiny]
    if pending:
        rest.append(None)
    for r in rest:
        faces = regions[r].faces if r is not None else np.asarray(pending, dtype=np.int64)
        if r in facet:
            for cell in _flat_facets(seg, faces, flat=FACET_FLAT if r in strict else FACET_GROUP):
                new_labels[cell.faces] = len(new_regions)
                new_regions.append(cell)
            continue
        cells = fit_freeform_cells(seg, faces)
        if split_more and len(cells) == 1 and len(faces) > 2:
            from .freeform import _geodesic_split

            cen = seg.V[seg.F].mean(1)
            cells = []
            for p in _geodesic_split(faces, cen, seg.nb, 2):
                cells.extend(fit_freeform_cells(seg, p))
        for cell in cells:
            new_labels[cell.faces] = len(new_regions)
            new_regions.append(cell)
    return _slivers_to_neighbours(seg, new_labels, new_regions)


def _parts_cover(reg):
    """Whether a pooled region's pieces still make up exactly its faces (else nothing to go back to)."""
    if not reg.parts:
        return False
    own = np.sort(np.asarray(reg.faces, dtype=np.int64))
    theirs = np.sort(np.concatenate([np.asarray(p.faces, dtype=np.int64) for p in reg.parts]))
    return own.size == theirs.size and bool((own == theirs).all())


def _unpool(labels, regions, back):
    """The regions in `back` (pooled surfaces) replaced by the pieces they were made of, at the end of the list,
    with the labels to match. The regions before them keep their order; those after move down."""
    keep = [r for r in range(len(regions)) if r not in back]
    out = [regions[r] for r in keep]
    for r in sorted(back):
        for part in regions[r].parts:
            piece = Region(part.prim, np.asarray(part.faces, dtype=np.int64), part.sense, part.weak)
            piece.facet, piece.parts, piece.cut, piece.pooled = part.facet, part.parts, part.cut, part.pooled
            out.append(piece)
    new_labels = np.full_like(labels, -1)
    for i, reg in enumerate(out):
        new_labels[np.asarray(reg.faces, dtype=np.int64)] = i
    return new_labels, out


def _renumbered(rs, back):
    """Region numbers in `rs` after _unpool removed those in `back`: each falls by the number removed before it."""
    gone = sorted(back)
    return {r - sum(1 for g in gone if g < r) for r in rs if r not in back}


def _slivers_to_neighbours(seg, labels, regions):
    """A freeform cell thinner than CELL_SLIVER tolerances gives its triangles to the neighbours
    that fit them best. Its surface spans only those few long, narrow triangles and can't reach
    the outline it shares: on Rak N four of them made a face 0.25 x 1.3 mm with an edge wandering
    8 mm off it; OpenCASCADE raised the tolerances to 8 and 31 mm to cover that, they spread to
    the corners around, and the file read back as two faces or crashed the reader (2026-11-02)."""
    drop = [r for r, reg in enumerate(regions) if reg.kind == "freeform" and len(reg.faces) <= CELL_SLIVER_TRIS
            and _too_small_for_face(seg, labels, reg.faces, area_k=0.0, width_k=CELL_SLIVER)]
    if not drop:
        return labels, regions
    gone = set(drop)
    pending = [int(f) for r in drop for f in regions[r].faces]
    labels = labels.copy()
    labels[pending] = -1
    added = {}
    for _ in range(8):
        left = []
        for f in pending:
            best, bd = -1, np.inf
            for g in seg.nb[f]:
                rr = labels[g] if g >= 0 else -1
                if rr < 0 or rr in gone or rr == best:
                    continue
                d = _handed_distance(regions[rr].prim, float(regions[rr].prim.distance(seg.V[seg.F[f]]).max()))
                if d < bd:
                    best, bd = rr, d
            if best >= 0:
                labels[f] = best
                added.setdefault(best, []).append(f)
            else:
                left.append(f)
        if len(left) == len(pending):
            break
        pending = left
    if pending:     # (nothing around to take them: as they were)
        return labels_restored(labels, regions, drop, pending, added)
    out = []
    index = {}
    for r, reg in enumerate(regions):
        if r in gone:
            continue
        if r in added:
            new = Region(reg.prim, np.concatenate([reg.faces, np.asarray(added[r], dtype=np.int64)]), reg.sense, reg.weak)
            new.facet, new.parts, new.cut = reg.facet, reg.parts, reg.cut
            reg = new
        index[r] = len(out)
        out.append(reg)
    new_labels = np.full_like(labels, -1)
    for i, reg in enumerate(out):
        new_labels[reg.faces] = i
    return new_labels, out


def labels_restored(labels, regions, drop, pending, added):
    """_slivers_to_neighbours when some triangles had nowhere to go: every region as it came in."""
    for r in drop:
        labels[regions[r].faces] = r
    return labels, regions


def _facets_ok(seg, faces, max_tris=2000, area_k=25.0):
    """Whether a region can become flat faces from its own triangles: not
    too many of them, and on average big enough that a face of one or two
    triangles isn't a sliver (see _too_small_for_face)."""
    faces = np.asarray(faces)
    return 0 < len(faces) <= max_tris and float(seg.fa[faces].mean()) >= area_k * seg.tol ** 2


def _flat_facets(seg, faces, max_deg=0.5, flat=FACET_GROUP):
    """The triangles of ``faces`` as flat faces: neighbouring triangles that
    lie in one plane (within ``max_deg`` and a tenth of the tolerance)
    share a face. Exactly the mesh, so always a faithful last resort."""
    from .fitting import Plane

    faces = np.asarray(faces, dtype=np.int64)
    member = np.zeros(len(seg.F), dtype=bool)
    member[faces] = True
    group = np.full(len(seg.F), -1, dtype=np.int64)
    cos_max = np.cos(np.radians(max_deg))
    out = []
    for f0 in faces:
        if group[f0] >= 0:
            continue
        gid = len(out)
        group[f0] = gid
        stack, comp = [int(f0)], [int(f0)]
        n0 = seg.fn[f0]
        c0 = seg.V[seg.F[f0]].mean(0)
        while stack:
            f = stack.pop()
            for g in seg.nb[f]:
                if g < 0 or not member[g] or group[g] >= 0:
                    continue
                if seg.fn[g] @ n0 < cos_max:
                    continue
                if np.abs((seg.V[seg.F[g]] - c0) @ n0).max() > flat * seg.tol:
                    continue
                group[g] = gid
                stack.append(int(g))
                comp.append(int(g))
        comp = np.array(comp, dtype=np.int64)
        P = seg.V[np.unique(seg.F[comp])]
        n = (seg.fn[comp] * seg.fa[comp, None]).sum(0)
        cell = Region(Plane(P.mean(0), n if np.linalg.norm(n) > 0 else n0), comp, 1.0, False)
        cell.facet = True
        cell.prim.facet = True   # (made from its own triangles; the surface outlives region copies:
                                 # see build_cad_shell's second try)
        out.append(cell)
    return out


def _broken_when_saved(asm, shell=False, unmeshable=None):
    """Regions whose face is invalid once the faces are written to a STEP
    file and read back: as a loose compound, or with ``shell`` as one
    shell, as the saved file holds them (neighbours then share each edge's
    single 3D curve; a solid made of the shell reads back the same)."""
    import os
    import tempfile

    from OCP.BRep import BRep_Builder
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS_Compound, TopoDS_Shell

    from . import stepio

    rs = sorted(asm.region_faces)
    B = BRep_Builder()
    comp = TopoDS_Shell() if shell else TopoDS_Compound()
    if shell:
        B.MakeShell(comp)
    else:
        B.MakeCompound(comp)
    for r in rs:
        B.Add(comp, asm.region_faces[r])
    fd, path = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        stepio.write_step(comp, path)
        back = stepio.read_step(path)
    except Exception as fault:
        faults.caught(fault)
        return set()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    out = set()
    exp = TopExp_Explorer(back, TopAbs_FACE)
    got = []
    while exp.More():
        got.append(TopoDS.Face(exp.Current()))
        exp.Next()
    # The reader mends a face whose wire crosses itself by cutting it in pieces, so one broken
    # face used to change the count, and with the count the whole check was given up: Rak N's
    # 0.26 mm2 cone, read back as 4 faces, hid every other broken face in the file (on Rak N,
    # N-USB, SMA-USB and Solar, most builds; 2026-11-02). The pieces come where the face was, so
    # the faces are lined up in order, and a face that didn't come back as one is broken.
    grown = set()
    pieces = _read_back_pieces([asm.region_faces[r] for r in rs], got, grown=grown)
    if pieces is None:
        return set()
    owner = {}
    for k, (r, idx) in enumerate(zip(rs, pieces)):
        owner.update((j, r) for j in idx)
        if k in grown or len(idx) != 1 or not BRepCheck_Analyzer(got[idx[0]]).IsValid():
            out.add(r)
    if shell:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        box = Bnd_Box()
        BRepBndLib.Add_s(back, box)
        if box.IsVoid():
            # (nothing came back to mesh, and so nothing to say about meshing it: an exception here ended the whole CAD attempt,
            # and tc-hotend-fan-shield went to 2,001 B-splines. The file's own read-back at the end still decides.)
            return out
        diag = box.CornerMin().Distance(box.CornerMax())
        um = {owner[i] for i in _unmeshable(back, got, diag)}
        if unmeshable is not None:
            unmeshable |= um - out
        out |= um
    return out


def _cusp_crossing(asm, r):
    """The two borders of region r (each as first and last vertex) whose exact arcs cross where they meet
    tangent, read back from a file: [] if none. Two r 3.2 chamfers on a Raspberry Pi case meet at a cusp on a
    wall; each arc, worked out from its own cone, passes the corner by 1e-7 mm to the far side, so they
    truly cross 0.0006 mm from it, a 1,773 mm2 plane reads back invalid and becomes B-spline. Fitted to
    the mesh instead, as they used to be, they don't, and only these two lose exactness."""
    import os
    import tempfile

    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle
    from OCP.TopAbs import TopAbs_FACE

    from . import meshguard, stepio

    face = asm.region_faces.get(r)
    if face is None:
        return []
    fd, path = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        stepio.write_step(face, path)
        back = stepio.read_step(path)
    except Exception as fault:
        faults.caught(fault)
        return []
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    faces = TopExp_Explorer(back, TopAbs_FACE)
    if not faces.More():
        return []
    read = TopoDS.Face(faces.Current())
    for hit in meshguard.self_crossings([read], flag=True)[0]:
        # (the check died on this face: no cusp to fit)
        if hit.died:
            return []
        # (only a border with two circular arcs can hold the crossing looked for, so a crossing of other edges in
        # one border, or one that names no two edges, no longer stops the look at the face's other borders: a
        # thread's helical borders cross themselves read back from a file, f_long_d8_l12, 2026-11-08)
        if hit.e1 is None or hit.e2 is None or _circular_edges(hit.wire) < 2:
            continue
        e1, e2 = hit.e1, hit.e2
        c1, c2 = BRepAdaptor_Curve(e1), BRepAdaptor_Curve(e2)
        if c1.GetType() != GeomAbs_Circle or c2.GetType() != GeomAbs_Circle:
            return []
        ends = [[np.array([p.X(), p.Y(), p.Z()]) for p in (c.Value(c.FirstParameter()), c.Value(c.LastParameter()))] for c in (c1, c2)]
        if min(float(np.linalg.norm(a - b)) for a in ends[0] for b in ends[1]) > CUSP_NEAR:
            return []
        out = []
        for pair in ends:
            for loop in asm.rg.region_loops[r]:
                for cid, _ in loop:
                    c = asm.rg.chains[cid]
                    a, b = asm.V[c.verts[0]], asm.V[c.verts[-1]]
                    if (min(np.linalg.norm(a - pair[0]), np.linalg.norm(a - pair[1])) <= CUSP_NEAR
                            and min(np.linalg.norm(b - pair[0]), np.linalg.norm(b - pair[1])) <= CUSP_NEAR):
                        out.append((c.verts[0], c.verts[-1]))
        return out
    return []


def _circular_edges(wire):
    """How many of a wire's edges are circular arcs."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle
    from OCP.TopAbs import TopAbs_EDGE

    n = 0
    edges = TopExp_Explorer(wire, TopAbs_EDGE)
    while edges.More():
        if BRepAdaptor_Curve(TopoDS.Edge(edges.Current())).GetType() == GeomAbs_Circle:
            n += 1
        edges.Next()
    return n


def _face_box(face):
    """(min corner, max corner) of a face's geometry, its tolerances left out."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(face, box, False, False)
    a, b = box.CornerMin(), box.CornerMax()
    return np.array([a.X(), a.Y(), a.Z()]), np.array([b.X(), b.Y(), b.Z()])


def _read_back_pieces(written, read, margin=READ_BOX_MARGIN, grown=None):
    """Which faces read back from a file came from which written face, given that the reader keeps
    the order and may cut a face in pieces or leave it out: for each written face, the indices of
    its pieces in ``read``. None when the two can't be lined up that way.

    A broken face can also come back bigger than it went in (allen-key's inside-out torus, 0.6 mm
    wide written, 2 mm read back). Lined up by boxes alone, its piece then fits no face, every
    later face is left without its own, and they all count as broken until one big enough takes
    them all (33 sound faces on allen-key). So a piece that fits neither this face nor the next,
    where it covers this face's place and the piece after it fits the next face (or it is the
    last of both), is this face's, read back grown; that face's index is added to ``grown``."""
    boxes = [_face_box(f) for f in written]
    rd = [_face_box(f) for f in read]

    def inside(j, i):
        return (rd[j][0] >= boxes[i][0] - margin).all() and (rd[j][1] <= boxes[i][1] + margin).all()

    def covers(j, i):
        return (rd[j][0] <= boxes[i][0] + margin).all() and (rd[j][1] >= boxes[i][1] - margin).all()

    out = []
    j = 0
    for i in range(len(written)):
        take = []
        while j < len(read) and inside(j, i):
            # (a piece that fits this face and the next, once this one has a piece, is the next's)
            if take and i + 1 < len(written) and inside(j, i + 1):
                break
            take.append(j)
            j += 1
        if not take and j < len(read) and covers(j, i) and not (i + 1 < len(written) and inside(j, i + 1)):
            last = i + 1 == len(written) and j + 1 == len(read)
            if last or (j + 1 < len(read) and i + 1 < len(written) and inside(j + 1, i + 1)):
                take.append(j)
                j += 1
                if grown is not None:
                    grown.add(i)
        out.append(take)
    return out if j == len(read) else None


def _unmeshable(shape, faces, diag):
    """Indices into ``faces`` (the faces of ``shape``, read back from a STEP
    file) of the faces OpenCascade can't triangulate at the fineness the app's
    preview uses, even meshed on their own. Such a
    face reads back valid, so nothing else flags it, and the preview and the
    shape check mesh it more coarsely instead; a viewer or slicer that doesn't
    retry shows a hole or a lumpy patch there (toolbox Body474: a 0.54 mm2
    torus where three rounds meet, its edges 0.012 mm off its surface,
    unmeshable up to 0.055 mm at the app's quality and shown as a crumpled pink patch). The face's
    correct neighbours, a 32 mm2 sphere that meshes down to 0.002 mm, pass."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopLoc import TopLoc_Location

    def meshed(face):
        tri = BRep_Tool.Triangulation_s(face, TopLoc_Location())
        return tri is not None and tri.NbTriangles() > 0

    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp

    def area(face):
        g = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, g)
        return abs(g.Mass())

    small = {i for i, f in enumerate(faces) if area(f) < SMALL_FACE * diag * diag}
    bad = set()
    for k, only in [(k, None) for k in MESH_FINE] + ([(MESH_FINE_SMALL, small)] if small else []):
        d = max(diag * k, 1e-6)
        whole = BRepBuilderAPI_Copy(shape).Shape()
        # (a face the mesher doesn't finish counts as unmeshable, at the fineness asked for; so
        # the result's coarser figure is no use here: it is the fineness asked that matters)
        stopped = meshguard.mesh(whole, d, False, 0.35, True)
        ex = TopExp_Explorer(whole, TopAbs_FACE)
        i = 0
        while ex.More() and i < len(faces):
            if i in stopped.coarse or i in stopped.unmeshed:
                if only is None or i in only:
                    bad.add(i)
            elif i not in bad and (only is None or i in only) and not meshed(TopoDS.Face(ex.Current())):
                alone = TopoDS.Face(BRepBuilderAPI_Copy(faces[i]).Shape())
                if meshguard.mesh(alone, d, False, 0.35, True) or not meshed(alone):
                    bad.add(i)
            i += 1
            ex.Next()
    return bad


def _too_small_for_face(seg, labels, faces, area_k=25.0, width_k=6.0):
    """Whether a region is too small or too thin to become a valid face of
    its own: about the size of the tolerance, or a sliver whose sides run
    within a few tolerances of each other (their tolerance zones overlap
    and the boundary counts as self-intersecting)."""
    faces = np.asarray(faces)
    area = float(seg.fa[faces].sum())
    tol = seg.tol
    if area <= area_k * tol ** 2:
        return True
    if len(faces) > 64:
        return False
    he = (3 * faces[:, None] + np.arange(3)[None]).reshape(-1)
    twin = seg.topo.twin[he]
    lab = labels[faces[0]]
    outer = (twin < 0) | (labels[np.maximum(twin, 0) // 3] != lab)
    a = seg.V[seg.topo.he_from[he[outer]]]
    b = seg.V[seg.topo.he_to[he[outer]]]
    perim = float(np.linalg.norm(b - a, axis=1).sum())
    return perim > 0 and 2.0 * area / perim <= width_k * tol


def _components(faces, nb):
    faces = list(map(int, faces))
    member = set(faces)
    seen = set()
    out = []
    for f in faces:
        if f in seen:
            continue
        comp = [f]
        seen.add(f)
        stack = [f]
        while stack:
            x = stack.pop()
            for y in nb[x]:
                y = int(y)
                if y >= 0 and y in member and y not in seen:
                    seen.add(y)
                    comp.append(y)
                    stack.append(y)
        out.append(np.array(comp, dtype=np.int64))
    return out


def build_freeform_shell(shell, tol, report=NULL):
    """Every face a fitted B-spline cell (no analytic recognition)."""
    seg = Segmenter(shell.V, shell.F, shell.topo, tol, report)
    regions = fit_freeform_cells(seg, np.arange(len(shell.F)))
    labels = np.empty(len(shell.F), dtype=np.int64)
    for i, r in enumerate(regions):
        labels[r.faces] = i
    rg = RegionGraph(shell.V, shell.F, labels, shell.topo)
    asm = CADAssembler(shell.V, rg, regions, tol, report)
    faces = asm.build()
    sh = asm.shell_from_faces(faces)
    _settle_tolerances(sh, tol)
    return sh


def summarize(regions):
    kinds = {}
    for r in regions:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    return kinds


def convert_body_cad(B, body, tol, opts, report=NULL, segs=None, tidy=True, tidied=None):
    from .pipeline import _closed_volume, fix_shape, orient_like_mesh, sew_faces, shells_to_solid
    from . import stepio

    built, flags, all_faces = [], [], []
    failed = False
    kinds = {}
    for shell in body:
        seg = segs.get(id(shell)) if segs else None
        asm, faces, regions = build_cad_shell(shell, tol, report, detail=opts.quality, seg=seg, tidy=tidy, tidied=tidied)
        for k, v in summarize(regions).items():
            kinds[k] = kinds.get(k, 0) + v
        if asm.failed_regions:
            failed = True
            report.warn(f"{len(asm.failed_regions)} surface(s) couldn't be built and were left out, so the result may have gaps.")
        all_faces.extend(faces)
        built.append(asm.shell_from_faces(faces))
        flags.append(shell.closed)
    report.log("Found " + ", ".join(f"{v} {_PLURAL.get(k, k) if v != 1 else k}" for k, v in sorted(kinds.items())))
    if failed:
        sewn = sew_faces(all_faces, tol * 2)
        parts = shells_to_solid(sewn)
        shape = parts[0] if len(parts) == 1 else make_compound(parts)
    elif all(flags):
        from .brep import make_solid_checked

        shape = make_solid_checked(B, built, report)
    else:
        shape = make_compound(built)
    mesh_vol = _closed_volume(body)
    shape = orient_like_mesh(shape, mesh_vol)
    if not stepio.is_valid(shape):
        # (the healed shape only when it is valid and keeps the mesh's volume: healing
        # turned the repeater mount's invalid body inside out)
        shape2 = fix_shape(shape, tol, mesh_vol)
        if stepio.is_valid(shape2):
            return shape2
        report.warn("The CAD model still has small errors after automatic repair.")
        return shape
    return shape
