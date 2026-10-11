# HackPHS stabilization audit — iteration5

This audit covers the existing desktop application, CLI, STL/3MF import,
recommendations, heuristic and FEA infill searches, native Orca integration,
G-code exports, uploaded-model motion simulation, measurement analysis and
serial protocol. The application and optimization algorithm were retained.
There is no implemented multi-model batch mode; none was added.

## Launch and demonstrate

On this Mac, run:

```sh
cd /Users/akhil/Desktop/mainhackathon
./launch_aeroslice.command
```

Equivalent direct launch, with the already installed dependencies:

```sh
cd /Users/akhil/Desktop/mainhackathon
.venv/bin/python -m AeroSlice.main
```

1. In **Optimize**, upload an STL or use **Try an example**. The example is a
   30 mm cube. STL coordinates are interpreted as millimeters.
2. Select the actual material. For the retained demonstration runs, the printer
   was Adventurer 5M 0.4 Nozzle, the process was 0.20 mm Standard, material was
   Flashforge Generic PLA, starting infill was 15%, and **Quick · 3 designs**
   selected the slicing shortlist.
3. For the tested stiffness comparison, select **Weight saving + FEA comparison**,
   set **Minimum stiffness retained** to 95%, use support axis Z / face min,
   and stiffness-test axis Z. Coarse mesh cells were 9, tolerance 0.2. Preferences
   are retained, so check these fields rather than assuming saved settings match.
   Material constants remain uncalibrated demonstration assumptions.
4. Run **Optimize Model**. Save only after a successful result. The generated
   files are `baseline.gcode` and `optimized.gcode`, with supporting reports.
   Native 3MF projects are internal working/validation artifacts.
5. Open **Measurements → Simulation** to see both actual meshes, their computed
   motion and the height, velocity and acceleration graphs. Simulation starts
   automatically when both successful slice results are available. Both cases
   use the same editable carriage assumptions.
6. For CSV analysis, use **Measurements & sensors**. **Try simulated readings**
   is explicitly synthetic. CSV and user-entered weighing/stiffness results are
   distinct from both Orca estimates and numerical predictions.

## Bugs fixed in this pass

- New uploads now clear model-specific CSV trials, charts, summaries, measured
  inputs, manual comparisons and recommendation windows. A new optimization
  clears its prior report and estimates before slicing starts.
- CSV import/processing failures clear old trials and comparison text; analysis
  cannot silently continue using earlier valid data.
- Model validation from the upload picker runs in a worker. The UI continues
  processing events. Cancellation discards the pending model after the current
  import operation completes. Workers reject overlapping starts, and loading,
  recommendation and printer-check states describe the actual operation.
- Serial-port controls remain comboboxes when no ports exist. Refresh handles
  an empty list. Firmware errors reach the UI; duplicate START commands are
  rejected, and a failed START write does not set recording active.
- Manual recommendations work with the default FEA selection; no FEA or
  printer-ready success is invented in manual mode.
- Corrupt or wrongly typed saved preferences cannot replace required launch
  configuration values. Saved candidate counts are restored when valid.
- Incomplete simulation reports produce actionable waiting/error messages.
  An unreadable original model produces a controlled error. Render failures
  clear partial simulation results and keep the UI polling.
- Export verifies the current model and both nonempty files before copying.
  Failed copies remove partial destination folders. Failed optimization final
  exports are removed and its report contains no validated export paths.
- Empty G-code cannot enable Save. Invalid axes, rotation tuples, fractional
  design counts and nonfinite/nonpositive slicer timeouts are rejected early.
- Student-t mean intervals use the actual trial degrees of freedom through
  SciPy, replacing rounded/inexact critical values.
- The launcher checks every required runtime Python package, plus Tk support.
  Orca discovery now checks the rotation-preservation flag used by slicing.

The previously repaired blank Simulation tab remains covered by navigation and
widget-visibility tests at narrow and wide sizes.

## Verification evidence

Retained evidence is in [evidence/stabilization](../evidence/stabilization/).
`initial_tests.xml` records 110 passing pre-audit tests. `final_tests.xml` and
`summary.json` record **141 passed, zero failures/errors/skips**, in 135 seconds.
Intermediate correction suites also
have XML reports. `environment.json` records dependency and Orca discovery.

The tests exercise live Tk widgets and resizing, real native slicing/export,
cancel/timeout/failure paths, recommendations, replacement state, CSV analysis,
the actual uploaded facets, numerical motion and FEA verification. Serial tests
use a real pyserial connection to a POSIX pseudo-terminal with test records;
this verifies protocol/software behavior, not an ESP32 or physical sensor.

`workflows.json` retains ten additional real workflows and references their
projects, logs, reports and G-code under `outputs/stabilization`. Each successful
case independently checks that:

- Original file bytes and external geometry fingerprints remain unchanged.
- Exported modifier densities match the selected candidate.
- Non-target settings remain unchanged and positive-extrusion infill XYZ
  segments differ from the baseline.
- Mass values exactly match the Orca G-code headers, not mesh-volume estimates.
- The selected native project reopens/reslices reproducibly.
- Motion uses the actual oriented mesh and completed baseline/selected masses.

| STL | Weight-only baseline → selected | 95% FEA comparison baseline → selected | Lower predicted stiffness ratio |
| --- | --- | --- | --- |
| Cube | 9.80 → 8.55 g | 9.80 → 9.37 g | 96.80% |
| Triangular prism | 5.88 → 5.32 g | 5.88 → 5.45 g | 95.70% |
| Cylinder | 7.51 → 6.53 g | 7.51 → 7.23 g | 97.23% |
| Faceted sphere | 5.04 → 4.44 g | No qualifying export at default mesh | — |
| Concave irregular prism | 6.97 → 6.30 g | 6.97 → 6.47 g | 96.00% |

Nine workflows generated validated exports. The sphere's default FEA case was
correctly rejected by its mesh check. Additional coarse meshes of 12 and 14 cells
passed numerical checks, but only the unchanged baseline met 95% stiffness in
the existing density grid; there was no lighter qualifying candidate. See
`sphere_mesh_checks.json`. The target/tolerance were not relaxed to force success.

## Physics checks and limits

The motion equations use total moving mass in kg, shared constant thrust and
rail friction in N, gravity and opposing quadratic carriage drag. RK45 integrates
height and velocity; acceleration is computed from net force. A bottom stop
keeps a case stationary when it cannot lift. Target misses have no invented
arrival time or improvement percentage. Analytic constant-acceleration and
quadratic-drag arrival tests pass, and equal masses/assumptions give identical
trajectories across different uploaded shapes.

FEA tests verify affine strain, an axial bar against its closed-form result,
load/modulus/density scaling, force/moment balance, beam refinement, invalid
supports/disconnected geometry, and screening/search behavior. These establish
numerical behavior under the model, not real printed stiffness. The relative
target is compared against the uniform baseline on both meshes.

Sources checked: [NASA drag equation](https://www1.grc.nasa.gov/beginners-guide-to-aeronautics/drag-equation/)
and [SciPy Student-t percentiles](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html).

Remaining limits:

- Physical printing, printer acceptance of exported files, material calibration,
  rig operation and ESP32 hardware have not been tested. Arduino CLI is not
  installed; firmware compilation was not performed.
- A 95% FEA ratio is an unvalidated prediction, not an experimental guarantee
  or a 5% bound on numerical/model error. FEA approximates shell/core infill and
  voxel geometry with assumed material laws and full-face clamping. Buckling,
  fatigue, creep and thermal effects are excluded. Some shapes/settings have
  no qualifying lighter candidate.
- Import limits are 250 MB / one million facets and closed manifold geometry.
  FEA has a 20,000-facet limit. Optional projected area is omitted above 50,000
  facets. Detailed full-mesh simulation rendering can still be expensive.
- Only the supported single-object/plate 3MF layouts and the configured
  Adventurer 5M profiles are verified; arbitrary printer profiles, arc fitting
  and post-processing scripts are rejected. Other Orca versions require checks.
- The existing virtual environment and launcher are tested on this Mac.
  First-time environment installation and other operating systems are untested.

## Files changed for this audit

Runtime: `gui.py`, `analysis.py`, `structural.py`, `model.py`, `slicer.py`,
`optimizer.py`, `motion_simulation.py`, `motion_ui.py`, `sensor.py`, and
`../launch_aeroslice.command`.

Tests: updated `tests/test_gui.py`; added `tests/test_stabilization.py` and
`tests/test_sensor.py`. Documentation/evidence: this report and the retained
`../evidence/stabilization/` files, including the reproducible workflow runner.
Prior iteration5 work remains in the branch. No commit or push was made.
