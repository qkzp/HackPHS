# AeroSlice — Into the Skies

A working Python/Tkinter desktop application for searching **real regional infill
configurations** with OrcaSlicer and the Flashforge Adventurer 5M. Only the separate
removable payload is optimized. The app never operates the printer, motor or propeller.

## A simpler everyday interface (iteration1)

The main screen follows **Add model → Choose setup → Compare results → Save files**.
Use **Try an example** if you do not have a model yet. The main action and Save
button remain visible on smaller screens; long settings panels scroll.

Choose the material actually on your spool from available installed PLA/PETG
profiles, then choose Quick (up to 3 designs), Standard (8), or Detailed (12).
These choices change search effort only; they do not certify strength or relax
protected regions. Custom material profiles remain available in printer setup.
The output folder, printer setup and search count are remembered after a run.

Advanced infill percentages, support directions and density constraints are
collapsed by default. Initial assumptions still treat the object as resting on
its lower face; change these in **Advanced: infill & engineering settings** for
other support conditions. **Printer setup & troubleshooting** contains profile
files, the example-model setup check, candidate previews and manual mode.

After real slicing, the main screen shows original/selected estimated grams,
material savings and print time in minutes. **Details: compare all designs**
shows the comparison table. **Your results** explains the next printing steps;
**View technical details…** retains complete settings, errors and validation data.
Replacing a model clears the previous on-screen print estimates.

**Test & measure (optional)** starts with CSV import and explicitly simulated
readings. Live ESP32 configuration and processing settings are expandable.
The results page keeps real weighing and separate stiffness tests optional.
Synthetic labels, unknown strength and genuine-file requirements are unchanged.

## Launch on this Mac

Double-click **`launch_aeroslice.command`** in Finder, or run from this repository:

```sh
./launch_aeroslice.command
```

The configured virtual environment uses Python 3.12 with Tk 8.6. The launcher
creates an environment on first use if one is absent. Python 3.10+ with Tkinter
is required; the python.org macOS installer includes Tk. This Mac's `/usr/bin/python3`
and `/usr/bin/git` require missing Apple command-line tools; the working Python is
`/opt/anaconda3/bin/python3`, and Git is `/opt/homebrew/opt/git/bin/git`.

To install on another Mac:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r AeroSlice/requirements.txt
.venv/bin/python -m AeroSlice.main
```

Install OrcaSlicer separately and configure Adventurer 5M. Browse to its executable
and profile directory in the app if installation paths differ. Select the actual
nozzle, filament, and compatible process profiles through the GUI. The defaults
are the installed **Adventurer 5M 0.4 Nozzle**, **0.20 mm Standard**, and
**Flashforge Generic PLA** profiles; they are software test settings, not a claim
about the filament loaded in your printer. Preferences are saved under
`~/Library/Application Support/AeroSlice/config.json`.

## Use the desktop application

1. **Add 3D model…**: choose an STL or compatible 3MF anywhere on your Mac.
   No copying into the repository or uploading to an AI assistant is needed.
   Originals are read-only and hashed. Replace the payload without restarting.
2. Configure baseline/range, candidate count, print rotation, platform contact
   axis/face, loading axis, expected axial acceleration, three protected minima,
   optional custom demand scores, and heuristic coverage. The contact assumption
   is the whole selected bounding-box face. If this does not represent your rig,
   supply conservative custom demand/protected zones or use the manual workflow.
3. Choose printer/filament/process profiles and an output folder. **Check my printer setup** executes an independent example-payload slicing test; it does
   not replace the uploaded model or its optimization results.
4. **Preview design options** previews density assignments without claiming slicing.
   **Find a lighter print** runs real OrcaSlicer processes in a background worker.
   **Cancel** terminates the current subprocess. Each run has its own folder.
5. Compare authentic slicer mass, filament and time estimates. **Save G-code…** saves
   `optimized.gcode`, `baseline.gcode` and comparison reports to a chosen folder.
   Review the optimized G-code preview and actual printer/material configuration
   before printing. The original STL is preserved; no optimized STL is needed
   because the changes affect internal infill instructions, not the surface mesh.

Print exports contain `baseline.gcode`, `optimized.gcode`, `report.json`,
`comparison.csv`, `compatibility_report.json`, `validation_report.txt`,
`infill_heatmap.png`, `README.md`, and `NOTES.md`. No 3MF files are exported.
OrcaSlicer uses native 3MF projects internally to apply regional infill settings
and verify geometry, settings and reproducible slicing. Those projects and logs
remain in the working folder for diagnosis and are excluded from **Save G-code…**.
**Open saved folder** opens the exported folder after saving. G-code
is the output format actually generated by the installed AD5M Klipper profile;
physical printer transfer and acceptance have not been tested.

Statuses are distinct:

| Status | Meaning |
| --- | --- |
| AUTOMATED SUCCESS | Actual projects and G-code generated, settings/mesh/toolpaths validated, selected project reopened and resliced. |
| SEMIAUTOMATED | Recommendations/manual instructions or imported authentic estimates; manual slicing/verification remains. |
| FAILED | Automatic processing failed. Exact errors are reported, and validated export is disabled. |
| NOT TESTED | A particular compatibility or hardware check has not been executed. |

When Orca is unavailable, choose **Prepare suggestions for manual slicing instead** to prepare recommendations,
a heatmap and step-by-step instructions. Import authentic baseline/candidate G-code
with **Import a manually sliced comparison…**. Imported metrics do not establish
that the files correspond to the selected model or have verified regional settings.
A failed automated run offers the same manual instructions without relabeling its
failure as automatic success.

## What the optimizer actually does

A native Orca project is first exported from validated payload geometry with the
selected profiles. The app inserts three adjacent box `modifier_part` volumes in
the parent object's coordinate frame, with per-part `sparse_infill_density` values.
Every candidate, including the uniform baseline, uses the same modifier boundaries.
The external printable mesh is unchanged apart from the configured rigid orientation
and placement on the print bed. No extrusion-flow or E-value edits are made.

A deterministic search enumerates the configurable density grid (default
10/15/20/25/30%), enforces region bounds/protected minima, and requires a selected
fraction of the baseline demand-weighted coverage. It orders candidates by an
occupied-volume-weighted density proxy and load alignment, then **ranks all feasible sliced
candidates by authentic Orca estimated mass**, with time/densities as
deterministic tie breakers. The baseline and lightest feasible uniform configuration
are controls. If the uniform lightweight control wins, the result states this;
if the baseline wins, no improved optimized export is claimed. The nearest *available* uniform mass comparison is recorded;
exact mass matching and a global mathematical optimum are not claimed.

Each sliced candidate is checked for preserved mesh, exact modifier densities,
unchanged effective process/printer/filament settings, and unchanged non-target part
settings. Positive-extrusion sparse-infill **XYZ segments**, separated by region,
are compared to the baseline; differences in file size or extrusion amounts alone
do not pass. Exterior toolpath bounds must remain within 0.05 mm of the baseline.
The selected project is reopened and sliced again; its infill geometry and mass
must reproduce. Geometry fingerprints allow Orca's float representation and rigid
translation. These checks do not establish structural strength or actual print quality.

### Structural-demand model

`AeroSlice/structural.py` documents the assumptions. A deterministic ray-parity
occupancy grid estimates volume fractions for the three slabs. For full-face,
axis-aligned distributed self-weight/inertial loading, the demand proxy is the
occupied volume beyond each slab center toward the unsupported face. Reversing
the support face reverses the load path. Transverse loading uses equal demand;
custom scores override the proxy. For meshes above 20,000 triangles, equal volume
fractions are used and the approximation is reported. Acceleration scales the
axial load proxy; it does not establish a strength requirement.

**Structural-demand analysis is a heuristic and has not been validated as a full
stress simulation.** Coverage is a user-selected heuristic constraint, not a
validated stiffness or safety threshold. Protected regions are density floors,
not automatically solid material. Regional redistribution may change mass balance;
center of mass is unavailable without resolved printed-material distribution.

## Model support and limitations

- Binary and ASCII STL; dimensions assume millimeters.
- Geometry 3MF with one build instance, one mesh and valid component transforms.
- Single-object/single-plate Orca 3MF; positive printable geometry is imported.
  Existing modifiers and settings are **replaced by the explicitly selected
  AeroSlice profiles**. Originals are preserved. Do not expect prior custom
  modifiers or project settings to survive this normalization.
- Multi-object, multi-part, multi-plate and negative-volume projects are rejected
  with an explanation; export the final separate payload STL from Orca first.
- Open/nonmanifold meshes, zero-area facets, invalid references, malformed packages,
  oversized files and unsafe XML entities are rejected.
- Arbitrary overlapping modifiers, arc-fitted toolpaths, profile compatibility
  expressions and post-processing scripts are unsupported and rejected.
- Native modifier generation was executed against installed OrcaSlicer 2.4.2.
  Other versions must pass local compatibility and runtime checks.
- Tiny/solid models with no sparse infill cannot verify regional optimization and
  produce a failure instead of fabricated printer-ready output.

## Vertical rig experiments

The second tab accepts `timestamp_ms,distance_mm,trial_id,variant` CSV files through
a native file picker. Optional `source`, `error` and `raw_distance` columns preserve
provenance and invalid raw distance tokens. Raw measurements are retained and can
be exported. Duplicate/error/out-of-range readings are excluded from analysis;
irregular sampling uses actual timestamps. Potential outliers are flagged, not
silently removed. Optional five-sample median smoothing is explicitly labeled.

Displacement is `d(0) − d(t)` and upward velocity is `−d′(t)`. Enter start/end sensor
distances and a maximum interpolation-gap limit to compare exactly the same climb
interval across trials. The app does not bridge long sensor gaps. It reports mean
climb times, sample standard deviations, mean speeds, Student-t mean intervals
under their stated assumptions, and exploratory bootstrap improvement intervals.
Select independent trials or pairing by matched trial IDs. Fewer than three trials
is insufficient; five or more is recommended. Synthetic data and measured inputs
are never pooled into the same comparison.

For live serial, connect an ESP32, select trial/variant, verify the example GPIOs,
configure the measurement interval, and start/stop recording. Sensor range and model
remain **NOT YET VERIFIED** until supplied. Firmware commands are sensor-only.
The separate Arduino sketch is in `AeroSlice/firmware/`; compilation and physical
hardware have not been tested. Read its wiring/level-shifting notes before use.

The dashboard accepts actual weighed payload masses and fixed moving-hardware
mass, and optional measured force/deflection/mass with support conditions.
Stiffness is `force / deflection` in N/mm; stiffness-to-mass uses grams. Compare
only equivalent test conditions. These user-supplied results remain separate from
slicer estimates. A guided vertical carriage is not free flight. Lower mass can
improve acceleration if thrust/friction/drag remain comparable; it does not prove
lower drag or higher stiffness.

## Reproducible demonstration and tests

In the GUI, use **Try an example**, run **Find a lighter print**, then use **Try simulated readings** in the sensor tab. The example is a deliberately generated 30 mm cube;
it is not a claimed final payload or rig dimension. Synthetic sensor rows and graphs
are always labeled **SYNTHETIC DEMONSTRATION DATA**. Slicer results are real.

```sh
.venv/bin/python -m AeroSlice.main --demo --output outputs
.venv/bin/python -m AeroSlice.main --check-compatibility --output outputs
.venv/bin/python -m AeroSlice.main --demo --semiautomated --output outputs
.venv/bin/python -m pytest -q
```

Unit tests without Orca or a graphical session:

```sh
.venv/bin/python -m pytest -q -m 'not integration and not gui'
```

Real slicer tests are marked `integration`; missing Orca causes **NOT TESTED** skips.
GUI tests similarly require Tk/display. Process timeout/cancellation fixtures test
process control only and are not represented as slicer tests. Hardware remains
NOT TESTED. See `compatibility_report.json`, `evidence/test_results.xml` and the real
run retained under `evidence/final_demo/` for this environment's executed evidence.

## Sources used for compatibility implementation

- [Orca CLI actions](https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_actions)
- [Orca sparse infill](https://github.com/OrcaSlicer/OrcaSlicer/wiki/strength_settings_infill)
- [Orca 2.4.2 native 3MF implementation](https://github.com/OrcaSlicer/OrcaSlicer/blob/v2.4.2/src/libslic3r/Format/bbs_3mf.cpp)
- [Orca 2.4.2 model volume types](https://github.com/OrcaSlicer/OrcaSlicer/blob/v2.4.2/src/libslic3r/Model.cpp)
- [Adventurer 5M manufacturer page](https://www.flashforge.com/products/adventurer-5m-3d-printer)

Installed executable `--help`, exported native files and actual slicing tests take
precedence over documentation examples for runtime flag/schema verification.

## Iteration 3: experimental simulation-guided search

The default **Weight saving** workflow reduces estimated print weight while
preserving the outer shape, without requiring thrust data. Optional **Weight
saving + FEA comparison** evaluates all allowed regional infill combinations
on two meshes using a normalized reference load, then slices a shortlist meeting
an editable relative-stiffness target. No actual thrust value is required.
Material defaults and the retention target are uncalibrated demonstration
assumptions. The separate advanced **FEA direct search (experimental)** mode
supports actual loads and absolute screening limits. Neither establishes
physical strength or actual thruster performance.

See [FEA setup, equations, reports and limitations](AeroSlice/FEA.md).
