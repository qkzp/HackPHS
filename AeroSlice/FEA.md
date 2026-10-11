# Experimental FEA direct search — iteration3

AeroSlice can screen regional infill combinations with a linear static finite
element solver before producing G-code. It predicts response under explicitly
specified conditions. It does not establish physical strength or safe service.

## Use in the app

The primary objective is **lower estimated print weight with the same outer shape**.
No actual thrust measurement is required for the weight-first workflow.

1. Import a closed, connected STL or supported 3MF and confirm its millimeter size.
2. Leave the default **Weight saving** mode for the original infill-rule search,
   or choose **Weight saving + FEA comparison** for simulation-assisted screening.
3. For the FEA comparison, choose a relative-stiffness retention target in
   **Simulation: material, loads & limits**. The default 75% is an editable
   demonstration comparison rule, not a validated safety threshold. The same
   normalized 1 N load is used for every candidate and the baseline; no real
   thrust value is needed or inferred. The comparison represents only the chosen
   load direction and full-face clamping arrangement.
4. Material numbers are demonstration assumptions, not calibrated PLA/PETG data.
   You can edit them, print rotation, support axis/face and comparison direction.
   Filament selection does not calibrate the simulation.
5. Run **Find a lighter print**. All allowed density triples are simulated on two
   meshes. Design count controls the subsequently sliced shortlist. The app
   chooses the lightest slice-validated candidate in that shortlist and checks
   that the original exterior shape is preserved.
6. Review estimated material savings and relative stiffness, then save G-code.
   Comparison reports are included; 3MFs remain internal working artifacts.

For engineering experiments with specified loads, the separate **FEA direct
search (experimental)** mode accepts actual force and absolute displacement/stress
screening limits. These limits do not control selection in relative-comparison
mode. Neither mode verifies physical strength or real thruster performance.

If no candidate meets the selected assessment and mesh checks, generation stops
and preserves the search report. No heuristic fallback is silently used. The
baseline is always sliced as a reference, but is ineligible to win when it fails
the selected assessment.

## Headless demonstration

```sh
.venv/bin/python -m AeroSlice.main \
  --model AeroSlice/examples/demo_payload.stl \
  --fea-config AeroSlice/examples/fea_demo.json \
  --output outputs/iteration3_demo
```

The example JSON selects relative-stiffness comparison and is deliberately labeled as uncalibrated demonstration data.
All dimensions are mm, forces N, and moduli/stresses MPa (= N/mm²). Positive
force acts along the positive selected global print axis; negative reverses it.
Only this supplied total force is applied. Self-weight, acceleration, hardware
mass and other loads are not added automatically.

## Numerical method

- Voxelize the actual triangle mesh with inside/outside ray parity. Reject empty
  or disconnected voxel geometry and meshes over 20,000 triangles.
- Use 8-node trilinear hexahedral elements and full 2×2×2 Gauss integration.
  Align voxel boundaries with the three infill regions. Refine every grid axis
  for the second solve; actual grid dimensions and spacings are reported.
- Build a positive-definite transversely isotropic compliance matrix with solid
  `E_xy`, `E_z`, `nu_xy`, `nu_xz`, and `G_xz`. The material axes follow print Z,
  after rotating the geometry. Reciprocity is enforced; `G_xy` is derived from
  `E_xy / (2 (1 + nu_xy))`.
- Approximate shell thickness from resolved wall loops, extrusion line widths,
  layer heights and top/bottom shell settings. Within boundary voxels, blend the
  solid shell and homogenized core using a fractional shell volume `a`.
  This is a Voigt-style mixture, not a resolved wall/filament mesh.
- For infill fraction `rho`, use the assumed elasticity law
  `C_effective = [a + (1-a) rho^p] C_solid`.
- Assemble sparse global stiffness and solve `K u = f` with the clamped face's
  three displacement components fixed. Verify free-node residual, total force
  balance and moment balance to relative tolerance 1e-6.
- Recover element Gauss-point strains/stresses. Report displacement, von Mises
  stress and a **screening utilization**: maximum absolute normal/shear stress
  divided by its directional allowable input, scaled with
  `[a + (1-a) rho^q]`, multiplied by the requested screening safety factor.
  Von Mises stress is visualization only; it is not an anisotropic failure law.
- In relative-comparison mode, calculate load compliance `c = fᵀu / F²` and
  relative stiffness `c_baseline / c_candidate`. Require the requested retention
  ratio on both meshes. With linear elasticity, the ratio is independent of
  force magnitude. A normalized 1 N reference load is used and the absolute
  stress/displacement/strength limits are not selection constraints. This is
  comparative stiffness for one assumed fixture/direction, not failure strength.
- In actual-load mode, require utilization ≤1 and maximum displacement ≤ the
  user limit on both meshes. Also require maximum strain ≤1% and displacement
  ≤2% of the smallest model bounding dimension to screen obvious violations of
  linear assumptions.
- Reject candidates whose displacement, utilization or voxel volume changes
  between meshes exceed the requested relative tolerance. Relative mode checks
  displacement and compliance instead of stress utilization, and also checks
  baseline compliance stability. Both meshes' voxel
  volume errors versus the STL signed volume must also meet that tolerance.
  This is a two-mesh sensitivity check, not established mesh convergence.
- Enumerate all positive density combinations within the density range and
  protected minima (maximum 1,000 combinations). Heuristic demand coverage is
  not used as a strength constraint in FEA mode. Rank passing candidates by
  predicted material volume, then slice the lowest-volume shortlist with Orca.
- Select the lowest Orca-estimated mass among candidates in that shortlist that
  also pass geometry/settings/toolpath validation; time breaks ties. Reopen and
  reslice the winner as before. This is a discrete, shortlist-limited result,
  not a global optimization or physical strength guarantee.

## Artifacts

`fea_search.json` contains inputs, assumptions, both mesh definitions, all
candidate responses, checks and shortlist scope. `fea_search.csv` is a tabular
comparison. `fea_selected.png` shows homogenized stress and displacement for the
selected candidate. `fea_selected_fields.npz` preserves arrays in the internal
working directory for technical investigation; it is not copied by Save G-code.
All previous authentic slicer reports and G-code checks remain enabled.

## Verification and limits

Tests verify constant-strain reproduction, the analytical axial-bar solution,
load/modulus/density scaling, force/moment balance, cantilever refinement toward
beam theory, exhaustive enumeration, rejection of exceeded limits, cancellation,
disconnected meshes and actual Orca/GUI G-code exports. These are numerical and
integration checks, **not validation of printed material or part strength**.

Voxel boundaries approximate surfaces, holes and fillets. Small features may be
missed even when the volume check passes. Boundary shell mixing can overestimate
stiffness; density power laws are not tied to an actual infill pattern. Material
inputs and stress screening require calibration before engineering reliance.
Clamped-boundary stresses may be sensitive to mesh resolution. There is no
buckling, fatigue, impact, creep, plasticity, delamination or thermal model.

References:
- [SciPy sparse solver documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.linalg.spsolve.html)
- [Linear static analysis assumptions](https://help.autodesk.com/view/fusion360/ENU/?contextId=SIM-STATIC-STRESS-ANALYSIS)
- [NIST polymer AM mechanical-testing guidance](https://www.nist.gov/publications/materials-testing-standards-additive-manufacturing-polymer-materials-state-art-and)
