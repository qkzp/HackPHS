# Iteration 0 engineering notes

The initial application was developed on `iteration0`. The beginner interface update is on `iteration1`.

Automated slicing is implemented and executed on this macOS machine using OrcaSlicer
2.4.2, the bundled Adventurer 5M 0.4 nozzle profile, 0.20 mm Standard process, and
Flashforge Generic PLA. Profile inheritance is resolved from installed files;
no profile parameters or fake G-code are invented.

The generated cube and synthetic sensor CSV are demonstrations. Their dimensions,
climb speeds and comparative behavior are not measurements of the future rig.
The model hash, selected profiles, settings, candidate metrics, geometry checks,
regional toolpath checks, and project reopening are recorded per run.

Scope decisions:
- Normalize supported uploaded geometry into an Orca-generated native project.
  Preserve original files; explicitly replace uploaded project's settings/modifiers.
- Use three nonoverlapping adjacent box modifiers and deterministic discrete search.
- Rank feasible sliced candidates honestly; uniform controls may beat adaptive candidates.
  A baseline winner yields no improved optimized export.
- Require authentic sparse-infill geometry differences and verified project reopening.
- Provide a semiautomated fallback with honest statuses and no invented sliced files.
- Use occupancy load-path scores only under declared full-face axial assumptions.
  Other loads use user-protected regions or explicitly equal demand.
- Live serial is implemented but physical ESP32/sensor integration and firmware
  compilation are NOT TESTED. The ultrasonic sensor model is unknown.
- Experimental stiffness is optional, supplied from measured force and deflection.
  No infill-to-stiffness relationship or FEA is asserted.
- Mass-matching uses the nearest available sliced uniform control, not an exact target.
- Adaptive Cubic comparison is optional in the specification and is not implemented
  as a separate automated control in this iteration.
- GUI imports are synchronous; slicing and occupancy/search calculations run in
  workers. GUI updates stay on the Tk main thread. Background slicing can be cancelled.
- Raw and processed sensor data retain distinct labels. Independent/paired
  exploratory bootstrap comparisons require at least five relevant trials.
- No center-of-mass calculation, motor activation, printer upload, or physical
  structural certification is included.

Known external environment issue: Apple command-line tools are missing. The
launcher uses a working Python/Tk installation; repository operations use Homebrew
Git. Installing or changing Apple's toolchain was unnecessary for this task.

Executed demo result: a generated cube baseline was estimated by Orca at 12.34 g;
the selected 20/10/10% candidate was estimated at 10.72 g (13.128% reduction).
This is a slicer estimate for the example, not a physical payload measurement.
Real tapered-mesh, native-project reimport, X-axis modifier and GUI upload/optimize/
export tests also ran. Sensor demonstration results are explicitly synthetic.

## Iteration 1 interface

- Guided upload/setup/comparison/export layout with persistent main action buttons.
- Infill, loading assumptions, file profiles, live sensor setup and stiffness fields
  are available through collapsed sections rather than required first-use controls.
- Quick/Standard/Detailed change only candidate count, preserving density,
  protection and heuristic constraints.
- Basic PLA/PETG selections map to actually installed Flashforge profiles; runtime
  compatibility checks still execute before slicing. Existing custom profiles stay
  selected until the user explicitly changes them.
- Estimates are presented as readable metric cards and minute-based print times.
- Complete diagnostic data remains accessible in technical details and run reports.
- Sensor results remain explicitly measured/user-supplied or synthetic. No hardware
  validation or stiffness claims were added by this interface change.

## Iteration 2 G-code exports

STL input can be optimized directly into `optimized.gcode`, with `baseline.gcode`
for comparison. Native 3MF projects remain internal slicing/validation artifacts.
The Save G-code action copies only validated G-code and comparison reports,
excluding all intermediate project, model, setup and log files. Original inputs
are preserved; unit interpretation and structural limitations are unchanged.

## Iteration 3 simulation

Optional linear-static voxel FEA and exhaustive regional-density search.
Material inputs, density power laws and shell/core mixing are unvalidated
assumptions. Full-face clamping and opposite-face distributed force are explicit.
Both meshes must pass screening limits and sensitivity checks; no heuristic
fallback is allowed. G-code selection remains limited to the sliced shortlist.
See FEA.md in AeroSlice for numerical verification and model limitations.

Weight-first scope: no actual thrust input is required. Optional FEA comparison
uses baseline/candidate compliance ratios under a normalized reference load.
Its stiffness-retention target is an adjustable assumption, not a physical
strength guarantee. Absolute strength limits are exclusive to advanced load mode.
