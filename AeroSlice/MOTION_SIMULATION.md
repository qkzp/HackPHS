# Original vs. optimized vertical motion (iteration5)

Run `.venv/bin/python -m AeroSlice.main`. Upload your STL in **Optimize**, complete
optimization, then open **Measurements → Simulation**. The existing CSV/sensor
analysis remains in **Measurements & sensors**. The same light/blue theme is used.

The app automatically retrieves the baseline and selected candidate's
`metrics.mass_g` from the completed OrcaSlicer report. Both must have passed
external-geometry validation, and the report's input SHA-256 must match the current
uploaded model. Missing, failed, stale and unsliced reports display a waiting
state. Uploading a replacement clears animation, charts and motion metrics; job
generations prevent a late result from repopulating the previous model.

Both cases show all triangles from the actual uploaded mesh in the reported print
orientation. Their common visualization transform centers and scales the geometry
for readability; it is never used in the physical calculations. The animation
samples each numerical trajectory at the same simulation time, holds an arrived
case at its target, and can be paused/replayed. Long trajectories replay faster
for viewing. Graphs use actual simulated seconds, not playback wall time.

Editable shared defaults illustrate a small guided carriage; they are not
validated apparatus data:

| Assumption | Default |
| --- | --- |
| Constant motor thrust | 1.5 N |
| Fixed moving hardware (excluding printed payload) | 100 g |
| Constant opposing rail friction | 0.05 N |
| Exposed carriage frontal area | 0.01 m² |
| Carriage drag coefficient | 1.0 |
| Air density | 1.225 kg/m³ |
| Target height | 1 m |
| Maximum simulated duration | 30 s |
| Gravity | 9.80665 m/s² |

Total moving mass is `(sliced_payload_g + fixed_hardware_g) / 1000` kg. In still
air, `F_drag = 0.5 * air_density * Cd * assembly_area * v * abs(v)`. Rising motion
uses `a = (thrust - m*g - F_drag - friction) / m`, `dh/dt = v`, `dv/dt = a`.
SciPy's adaptive RK45 integration starts at rest and uses an event to find the
first target-height crossing. A bottom stop holds a case at rest if thrust cannot
overcome gravity and friction. If a target is unreachable or not reached within
the duration, the time and percentage comparison are explicitly unavailable.

Both cases use the same apparatus assumptions. The STL is enclosed in the same
carriage: its shape does not change the drag coefficient or exposed carriage area.
Changing a sliced payload's mass can change motion; equal payload masses under
equal assumptions produce equal trajectories regardless of STL shape. A positive
percentage means `(original_time - optimized_time) / original_time * 100`.

The report's orientation, dimensions and bounds come from the full oriented mesh.
Payload frontal area is computed from the union of triangle projections along Z
using two scanline resolutions (128/256), preserving concave silhouettes and
counting overlapping projected facets once. This is a numerical approximation,
not an exact area or validated error bound, and is descriptive only. Above 50,000
facets this optional diagnostic reports unavailable; motion still uses the supplied
carriage area. No mesh volume is substituted for sliced mass.

The optimization algorithm is unchanged. The slicer seed now explicitly disables
placement rotation with `--allow-rotations=0`, preserving the selected orientation
of asymmetric models while still arranging them on the bed. This was verified
against installed OrcaSlicer; the flag is listed in the
[official CLI miscellaneous options](https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_misc).

Verification covers uploaded cubes, triangular prisms, cylinders, a faceted sphere
and a concave L-shaped prism; constant-acceleration and quadratic-drag analytic
solutions; total mass, friction, drag, stall and duration handling; identical mass
cases; stale/missing mass and geometry validation; actual animated vertex positions
and automatic input/model updates. Live OrcaSlicer tests exercise varied shapes,
native geometry preservation and G-code generation. The GUI integration test runs
an irregular STL through optimization, simulation and exported G-code.

Existing STL import/file-size/manifold restrictions and FEA complexity/screening
limits remain. A valid mesh does not guarantee the existing optimizer finds a
qualifying lighter print; without a completed pair the simulator waits. Dense
meshes take more rendering time and memory because the actual facets are retained.
No physical hardware validation, variable-thrust motor model, wind, buckling,
thermal effects or measured performance is claimed. Motion predictions remain
separate from CSV measurements and FEA stiffness predictions.

Executed verification: **108 tests passed, zero skips** across the engine and
live GUI suites. Reports and rendered motion/trajectory plots are retained in
`evidence/iteration5/`. One real concave-model run used sliced payload masses
6.97 g and 6.30 g and predicted 0.73743 s and 0.72921 s to 1 m (1.1146% time
reduction) under the default apparatus assumptions. These are simulated times,
not measured climb results.
