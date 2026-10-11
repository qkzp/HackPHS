## Inspiration

Our inspiration for AeroSlice came from engineering projects we worked on in school using platforms such as WhiteBox Learning. This platform provides different engineering projects where users can create and modify their own designs. One of these projects involved designing a glider. Based on the dimensions we entered, the platform would calculate different factors such as drag, weight, and lift. However, it was still up to us to manually change the design to improve its performance.

Through this experience, we realized how useful it would be if this optimization process could be automated. Instead of spending time constantly adjusting different parts of a design, we wanted to create software that could do some of this work automatically. This led us to develop AeroSlice, a tool designed to make the process of optimizing 3D-printed objects more efficient, especially for engineering applications.

## What it does

AeroSlice is a Python desktop application that optimizes the internal infill of a 3D-printed model while preserving its external geometry. It divides the model into three adjacent regions and compares different infill percentages to find a lighter printable configuration. Our goal is to reduce material use while retaining a user-selected level of predicted stiffness.

Users can choose a weight-saving search or add a finite element analysis (FEA) comparison. In the FEA mode, AeroSlice evaluates candidate configurations on two mesh resolutions and screens them against a configurable stiffness-retention target, such as 95% of the baseline. This is a simulated stiffness ratio under assumed material and support conditions, not a guarantee of physical strength.

AeroSlice then uses OrcaSlicer to slice the baseline and shortlisted candidates with the same printer, filament, wall, and layer settings. It reads filament mass and print-time estimates directly from the generated G-code and selects the lightest candidate that passes the applicable checks. It exports both baseline and optimized G-code, verifies that the intended infill toolpaths changed, and checks that the external mesh remained unchanged. If no candidate qualifies, it reports the problem instead of producing an unsupported optimized export.

The application also compares predicted vertical climbing performance using the actual uploaded model and the two sliced mass estimates. Both models appear side by side and move according to numerically calculated trajectories. Users can view height, velocity, acceleration, and time-to-height comparisons, while imported experimental measurements remain separately labeled.

## How we built it

We built AeroSlice through a combination of software development, engineering, and hardware experimentation.

For the software, we used Python with Tkinter for the desktop interface, NumPy and SciPy for numerical calculations, and Matplotlib for 3D model visualization and comparison graphs. The importer reads the uploaded mesh directly, calculates its dimensions and bounding box, and checks for invalid geometry such as open surfaces or degenerate triangles. The analysis works with the actual geometry rather than selecting a template based on whether a model resembles a cube, cylinder, or another familiar shape.

We integrated OrcaSlicer through its command-line interface. AeroSlice creates internal native projects containing three adjacent infill modifiers, changes only the intended regional infill percentages, and generates real slicing results for each shortlisted configuration. Native 3MF files remain working artifacts; the printing outputs are G-code. Geometry fingerprints, modifier settings, and comparisons of extruding infill segments help verify that optimization changes the internal printing instructions while preserving the external model. We also reopen and reslice the selected project to check that its results are reproducible.

For stiffness assessment, we implemented an exhaustive search over the allowed regional infill combinations using a linear elastic voxel FEA model with eight-node elements. The model approximates solid walls and top/bottom layers from the selected print profiles, while effective infill stiffness follows assumed density-dependent material laws. It also represents different material properties along and across the print direction. Comparing coarse and refined meshes helps identify numerically sensitive results. This model is a computational approximation, and its material properties have not been calibrated against physical prints.

For the vertical-motion simulation, we numerically integrate Newton's second law: net force equals motor thrust minus gravity, carriage drag, and rail friction, and acceleration equals net force divided by total moving mass. That mass includes the sliced payload estimate and fixed moving hardware. Both cases share identical thrust, friction, environmental conditions, and exposed-carriage drag assumptions because the payload is enclosed in the same carriage. These inputs are editable demonstration assumptions, and the resulting motion is labeled as a physics prediction rather than a measurement.

For the hardware side, we wanted to create a physical demonstration that would show how reducing the weight of an object could affect its performance. Our idea was to build a vertical propulsion system that could compare the climbing performance of different 3D-printed objects. This would allow us to connect our software calculations to an actual engineering experiment.

Throughout the development process, we worked together to solve problems involving the software, mechanical design, and hardware.

## Challenges we ran into

We faced multiple challenges throughout the hackathon. One of our biggest challenges involved our motor. Initially, we planned on creating a wind turbine that could efficiently convert wind energy into electricity. However, we eventually decided to move in a different direction and develop AeroSlice.

Although our project could be demonstrated entirely through software, we also wanted to create a physical demonstration to show the impact of our optimizations. The problem was that our motors were not powerful enough to generate the thrust we needed. We experimented with different power configurations and tried to improve the setup, but getting the hardware to perform consistently was more difficult than we originally expected.

Another major challenge was our 3D printer. Our demonstration depended on printing both the original and optimized models. Unfortunately, when we tried setting up our printer, we discovered that one of its cables had been damaged during transportation. We attempted to repair the issue, but we were unable to get the printer working properly. Luckily, some of the other teams were kind enough to help us print models, which allowed us to continue working on our project.

On the software side, a major challenge was verifying that a lower infill percentage actually produced different toolpaths and less filament use without changing the model's shape. We also had to handle STL unit assumptions, native slicer settings, models that failed structural screening, and outdated results when users uploaded a new model. Keeping the interface responsive during mesh importing and slicing required background workers and careful management of calculation states.

These challenges forced us to constantly adjust our plans and find alternative solutions throughout the hackathon.

## Accomplishments that we're proud of

One of our biggest accomplishments was being able to develop a working version of AeroSlice within the limited time we had. We built an end-to-end workflow that imports a model, evaluates regional infill configurations, compares authentic OrcaSlicer estimates, and exports verified baseline and optimized G-code. The software also displays the actual uploaded geometry in its motion simulation and clears previous calculations when the model changes.

During our stabilization pass, 141 automated tests passed, covering numerical calculations, mesh importing, interface behavior, error handling, and real slicing workflows. We also retained ten additional OrcaSlicer runs using cubes, triangular prisms, cylinders, a faceted sphere, and an irregular model. Nine produced verified optimized exports; the sphere's FEA case correctly withheld an export when its default mesh checks failed. These tests verify software and numerical behavior, while printed stiffness and full rig performance remain unvalidated.

We are also proud that we were able to connect different areas of engineering into one project. Instead of focusing only on software, we wanted to explore how our optimizations could affect a physical system.

Even with the problems we faced with our printer and motor, we continued developing our project and found ways to demonstrate our work.

## What we learned

One of the biggest things we learned from this hackathon was how complicated engineering optimization can be. Initially, we thought reducing the weight of a 3D-printed object would be relatively straightforward. However, we quickly realized that reducing material does not necessarily mean that the object will perform better.

We had to consider other factors, including stiffness, printing settings, material behavior, and how the object would be supported and loaded. We learned to distinguish three types of evidence: a slicer's filament estimates, a numerical model's structural predictions, and measurements from a physical experiment. Agreement within a simulation does not establish that a real printed part will behave the same way.

We also learned that optimization depends on the configurations and assumptions being tested. AeroSlice identifies the lightest passing design among the candidates it actually slices; it does not claim to find a global optimum. A 95% predicted stiffness target allows up to 5% stiffness loss in the model, but it does not mean the simulation itself is accurate to within 5%.

We also learned how important it is to adapt when things don't go according to plan. Between the issues with our motor and 3D printer, we had to come up with different solutions while working under a limited amount of time.

Overall, this project helped us better understand how software and mechanical engineering can work together to solve real problems.

## What's next for AeroSlice

In the future, we want to improve AeroSlice's structural predictions by calibrating material and infill properties against physical bending or tensile tests. We also want to improve how the model represents thin walls, small features, mounting conditions, and the effects of print orientation. Measuring printed mass and deflection would help us assess how closely the slicer estimates and FEA predictions match real objects.

One of our main goals is to go beyond optimizing internal infill and explore controlled changes to external geometry. A future aerodynamic stage could use computational fluid dynamics (CFD) to compare drag under defined airflow conditions while preserving required mounting features and dimensions. Proposed shape changes would then need separate structural and printing checks. The current version preserves external geometry and does not perform aerodynamic shape optimization.

We also want to test baseline and optimized prints under comparable conditions and measure the thrust, friction, and moving mass of our vertical apparatus. These measurements would let us replace demonstration inputs with experimental values and assess the accuracy of our climbing predictions.

Ultimately, our goal is to develop AeroSlice into a tool that makes engineering optimization more accessible and efficient, allowing people to spend less time manually changing designs and more time developing new ideas.
