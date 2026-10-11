# Desktop UI redesign (iteration4)

Run from the repository root:

```sh
.venv/bin/python -m AeroSlice.main
```

The existing Tkinter application, Matplotlib interaction, queue-based workers,
STL/3MF readers, OrcaSlicer integration, internal native projects and G-code export
are retained. No dependencies were added. Optimization calculations are unchanged.

The Optimize workspace allocates approximately 44% to settings and 56% to the
viewer. On desktop widths, only overflowing settings scroll; the viewer and bottom
actions stay visible. Below 1060 content pixels, the columns stack and one page
scrollbar replaces settings scrolling. Scrollbars disappear when unnecessary.
Engineering forms stack their fields when narrow. Advanced infill, support,
material, absolute-load, profile and manual-workflow controls remain accessible.

Model upload shows filename, dimensions, format and readiness. Hover over the
filename for its full path. The actual mesh is centered in a larger neutral viewer;
drag rotates, the wheel zooms, and Front, Top, 3D and Fit provide camera controls.
Preview decimation remains visual only. Calculations use the original full mesh.

Four summary cards share live values between Optimize and Results. The stiffness
card is explicitly an FEA prediction: the existing voxel finite-element model uses
homogenized infill, assumed material properties, a clamped support face and two
mesh resolutions. It has not been physically validated. Weight-only mode displays
“Not assessed”; it does not invent a stiffness number.

Optimize Model and Save Optimized G-code remain visible in the bottom action bar.
Progress appears only during a running task. Export is enabled only after a
successful report and existence checks for both generated G-code files. Saved
exports and working folders use the original export implementation. Measurements,
sensor recording, CSV analysis, comparison charts and measured stiffness remain.

Implementation files: `gui.py`, `ui_components.py`, `tests/test_gui.py`.
The layout regression tests check 1280×800, 1440×900, 1664×919, 1920×1080 and
800×700, field bounds, camera reset, scrolling, fixed actions and progress state.
Existing GUI integration tests execute real OrcaSlicer optimization and export.

Verification artifacts are under `evidence/ui_redesign/` at the repository root:
`layout_checks.json`, `model_preview.png` and `tests.xml`.
All 72 tests passed: 63 engine/import/analysis/slicer tests and 9 GUI tests,
including actual optimization and G-code export. All inspected layouts had no
horizontal widget overflow.

macOS desktop screen capture was unavailable (“could not create image from
display”); full-window visual screenshots could not be reviewed. Live Tk widget
geometry was inspected across all pages, and the actual rendered preview was
captured and inspected. Physical sensor hardware and mechanical stiffness remain
unverified; simulated and measured data retain their original provenance labels.
