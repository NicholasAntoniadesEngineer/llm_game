# Research: Better 3D Building Generation for Roma Aeterna

## The Problem

Roma Aeterna currently uses a component-listing approach: the Urbanista agent (an LLM)
specifies architectural components (podium, colonnade, pediment, cella, etc.) with
parameters, and the renderer stacks them using a fixed priority system (foundation ->
structural -> infill -> roof -> decorative -> freestanding). The renderer handles
vertical placement automatically.

**What goes wrong:**

1. **Poor spatial reasoning by the LLM.** The model does not reason well about 3D
   proportions. It picks component heights, column counts, and dimensions that don't
   compose into a coherent building. A colonnade might be too tall for the pediment
   sitting on it, or a cella might be wider than the colonnade that's supposed to
   surround it.

2. **No inter-component constraints.** The renderer stacks components vertically but
   does not enforce that a cella must fit inside a colonnade, that a pediment's base
   width must match the entablature, or that column spacing should relate to the
   building width. Each component is dimensioned independently.

3. **Flat composition model.** Everything is stacked on a single vertical axis. There's
   no concept of nested volumes, setbacks, side wings, porticos, or courtyards that
   extend outward. A Roman domus with a peristyle garden behind an atrium is hard to
   express as a flat stack.

4. **Lack of architectural knowledge in the pipeline.** The LLM has to be both a
   historian (knowing what a Temple of Saturn looked like) and an architect (translating
   that into correct spatial parameters). These are very different skills, and the
   model is mediocre at both simultaneously.

**Core tension with the project philosophy:** The user strongly rejects pre-built
templates and wants every structure to be uniquely AI-generated. Any solution must
preserve the generative nature of the system while dramatically improving spatial
correctness.

---

## Approach 1: Shape Grammars / CGA Shape

### How It Works

Shape grammars are formal rewriting systems for generating 3D geometry. They were
invented by Stiny and Giny (1972) and formalized for architecture. The most practical
implementation is **CGA Shape** (Computer Generated Architecture), developed by Pascal
Mueller at ETH Zurich and commercialized in Esri's CityEngine.

A shape grammar works like a context-free grammar for language, but instead of
rewriting strings, it rewrites 3D volumes:

```
// Pseudocode grammar for a Roman temple
Lot --> extrude(podium_height) Podium
Podium --> split(y) { base_height : Steps | rest : Platform }
Platform --> split(z) { pronaos_depth : Pronaos | rest : Cella }
Pronaos --> columns(6, "ionic") Colonnade
Cella --> offset(-wall_thickness) { WallRing | inner : Interior }
WallRing --> extrude(wall_height) Walls
            comp(f) { top : Entablature --> Pediment }
```

The key operations in CGA Shape:

- **split(axis) { ratios : symbols }** — divide a volume along an axis into sub-volumes
- **comp(f/e/v)** — decompose into faces, edges, or vertices
- **extrude(height)** — push a 2D face into a 3D volume
- **offset(distance)** — inset or outset a face
- **repeat(direction, size)** — tile a pattern (e.g., columns along a facade)
- **i("mesh.obj")** — insert a pre-modeled asset
- **t/r/s** — translate, rotate, scale

A building starts as a 2D lot footprint. The grammar applies rules recursively,
splitting, extruding, and decorating until terminal symbols are reached. The grammar
encodes architectural knowledge: columns are evenly spaced, walls enclose volumes,
roofs sit on top of walls.

**Split grammars** (Wonka et al., 2003) are a restricted form particularly good for
facades: they split a rectangular face into rows and columns, which is perfect for
Roman insulae (apartment blocks) and basilica walls.

### How Roman Building Grammars Would Work

Each building type gets a grammar. The LLM's job changes: instead of specifying
components and dimensions, it picks a grammar and provides parameter overrides.

**Example: Prostyle Temple Grammar**

```
// Parameters (what the LLM specifies):
columns_front = 6      // number of front columns
column_style = "ionic"  // doric, ionic, corinthian
width = 15m            // overall width
depth = 25m            // overall depth
podium_height = 2.5m   // podium height
material = "travertine" // main material

// Derived values (the grammar calculates):
column_height = width * 0.6           // classical proportions
intercolumniation = width / (columns_front - 1)
entablature_height = column_height / 4
pediment_height = width * 0.15
cella_width = width - 2 * intercolumniation
cella_depth = depth * 0.6
pronaos_depth = depth - cella_depth

// Grammar rules:
Lot(width, depth)
  --> extrude(podium_height) Podium

Podium
  --> split(y) {
       step_height * 5 : Steps
     | remainder : PodiumTop
     }

Steps
  --> repeat(y, step_height) Step
Step
  --> offset(-step_inset * index) StepSurface

PodiumTop
  --> split(z) {
       pronaos_depth : Pronaos
     | cella_depth : CellaVolume
     }

Pronaos
  --> repeat(x, intercolumniation) { ColumnBay }
ColumnBay
  --> Column(column_style, column_height)

CellaVolume
  --> offset(-wall_thickness) { WallRing | interior : Interior }

WallRing
  --> extrude(wall_height) Walls
  --> comp(top) Entablature
  --> Entablature --> RoofVolume

RoofVolume
  --> roofGable(pediment_height) Roof
```

Each grammar rule is deterministic given its parameters, and the parameters are
derived from a small set of inputs using classical proportional relationships. The
grammar *encodes* the architectural knowledge that the LLM currently lacks.

**Peripteral Temple Grammar** would differ: instead of columns only at the front,
the Pronaos rule would wrap columns around all four sides with the cella inset.

**Insula Grammar** would use split grammars for the facade:

```
Lot --> extrude(story_height * num_stories) Block
Block --> split(y) { repeat(story_height) Story }
Story --> split(x) { wall | repeat(bay_width) Bay | wall }
Bay --> split(y) { sill : Wall | window_height : Window | lintel : Wall }
```

### Pros/Cons for Roma Aeterna

**Pros:**
- Buildings are **always structurally correct** — the grammar guarantees components
  compose properly. A pediment is always exactly as wide as the entablature below it.
- The LLM's job becomes **much simpler**: pick a grammar, set 5-10 parameters. No
  spatial reasoning needed.
- **Infinite variation** from parameter changes. A 6-column Ionic temple and an
  8-column Corinthian temple look completely different but are both correct.
- **Proportional relationships** are baked in. Column height relates to width relates
  to entablature height, exactly as Vitruvius prescribed.
- Grammars are **composable**: a forum grammar can place temple grammars, basilica
  grammars, and taberna grammars around a central space.
- CGA Shape is **proven at scale** — CityEngine generates entire cities.

**Cons:**
- **Implementation complexity is significant.** A full grammar interpreter with split,
  repeat, comp, offset, extrude operations is a serious piece of software. Probably
  1500-3000 lines of JavaScript.
- **Grammar authoring is hard.** Each building type needs a carefully designed grammar.
  Roman architecture has at least 15-20 distinct building types. Writing and debugging
  these grammars is time-consuming.
- **Tension with "no templates" philosophy.** Grammars are, fundamentally, encoded
  templates. They are parametric and produce variation, but the structure is fixed.
  A temple grammar always produces a temple shape. The counterargument: this is not
  the same as pre-built meshes. The geometry is generated fresh each time, and
  parameter ranges produce genuinely different buildings.
- **Rigidity.** A grammar for a standard prostyle temple cannot produce an unusual
  building like the circular Temple of Vesta or the Pantheon without a separate
  grammar. Edge cases need their own rules.

**Implementation complexity:** HIGH. 3-4 weeks for a robust grammar engine plus
initial grammar library. But the payoff is very high.

### Temple of Saturn Example

LLM output:
```json
{
  "grammar": "prostyle_temple",
  "params": {
    "columns_front": 8,
    "column_style": "ionic",
    "material": "grey_granite",
    "podium_material": "travertine",
    "podium_height": 3.0,
    "width": 17,
    "depth": 28
  }
}
```

The grammar derives: column_height=10.2m, intercolumniation=2.43m,
entablature_height=2.55m, pediment_height=2.55m, cella_width=12.14m,
cella_depth=16.8m, pronaos_depth=11.2m. Then recursively generates geometry.

---

## Approach 2: Parametric Architecture Templates

### How It Works

This is the simplest approach and the most common in games. Instead of a grammar
interpreter, you write a dedicated generation function for each building type. The
function takes parameters and produces geometry directly.

```javascript
function generateTemple(params) {
    const {
        columnsAcross = 6,
        columnsDeep = 0,   // 0 = prostyle, >0 = peripteral
        columnStyle = "ionic",
        width = 15,
        depth = 25,
        podiumHeight = 2.5,
        material = "travertine"
    } = params;

    // Derive proportions from Vitruvian rules
    const columnDiameter = width / (columnsAcross * 2 + 1);
    const columnHeight = columnDiameter * ORDER_RATIOS[columnStyle].heightRatio;
    const entablatureHeight = columnHeight * ORDER_RATIOS[columnStyle].entablatureRatio;
    // ... etc

    // Build geometry procedurally
    const group = new THREE.Group();
    buildPodium(group, width, depth, podiumHeight, material);
    buildColonnade(group, positions, columnStyle, columnHeight, columnDiameter);
    buildEntablature(group, width, depth, entablatureHeight, podiumHeight + columnHeight);
    buildPediment(group, width, entablatureHeight, pedimentHeight);
    buildCella(group, cellaWidth, cellaDepth, cellaHeight, cellaOffset);
    return group;
}
```

The key difference from the current system: the template function handles all spatial
relationships internally. The LLM does not specify component heights — the function
calculates them from classical proportional rules. The LLM only specifies high-level
parameters that a historian would know: "8 Ionic columns, grey granite, high podium."

### Classical Proportional Systems

Roman architecture followed specific proportional rules documented by Vitruvius
(*De Architectura*, c. 30-15 BC). These are the basis for correct buildings:

**Column Orders (Vitruvius, Books III-IV):**
- **Doric:** Column height = 7x lower diameter. No base. Frieze has triglyphs and
  metopes. Entablature = 1/4 column height. Capital is simple echinus + abacus.
- **Ionic:** Column height = 8-9x lower diameter. Has torus-and-scotia base. Volute
  capitals. Continuous frieze. Entablature = 1/5 column height.
- **Corinthian:** Column height = 10x lower diameter. Elaborate acanthus capitals.
  Entablature = 1/5 column height. Same base as Ionic.
- **Tuscan (Roman addition):** Column height = 7x diameter. Simplified Doric with base.
  Plain capital. Used for utilitarian buildings.
- **Composite (Roman addition):** Column height = 10x diameter. Combines Ionic volutes
  with Corinthian acanthus. Used on triumphal arches.

**Intercolumniation (spacing between columns, Vitruvius Book III):**
- Pycnostyle: 1.5 diameters apart
- Systyle: 2 diameters
- Eustyle: 2.25 diameters (considered ideal)
- Diastyle: 3 diameters
- Araeostyle: 4+ diameters (only with wooden architraves)

**Temple plans (Vitruvius Book III):**
- Prostyle: columns only at front
- Amphiprostyle: columns at front and back
- Peripteral: columns surrounding all four sides
- Pseudoperipteral: engaged half-columns on sides (like Maison Carree)
- Dipteral: double ring of columns (rare, like Temple of Olympian Zeus)
- Circular (tholos): like Temple of Vesta

**Building-specific proportions:**
- Podium height: typically 1/5 to 1/3 of column height for temples
- Pediment slope: approximately 1:4.5 to 1:5 (Roman preference, shallower than Greek)
- Cella width: typically 60-70% of total width for peripteral temples
- Intercolumniation at center entrance: often wider (Vitruvian "eustyle" rule)

### Building Types Needed

For Roma Aeterna, the minimum set of parametric templates:

1. **Temple** (prostyle, peripteral, circular variants)
2. **Basilica** (nave + aisles + apse pattern)
3. **Insula** (apartment block: stories, windows, balconies, tabernae at ground floor)
4. **Domus** (atrium house: vestibulum -> atrium -> tablinum -> peristyle)
5. **Thermae** (bathing complex: frigidarium, tepidarium, caldarium sequence)
6. **Amphitheater** (elliptical tiered seating, arcade facade)
7. **Circus** (elongated track with spina, curved end)
8. **Triumphal Arch** (single/triple bay with attic)
9. **Aqueduct** (repeated arched bays on piers)
10. **Forum** (open plaza with surrounding portico)
11. **Taberna/Market** (single-room shop with wide opening)
12. **City Wall / Gate** (curtain wall with towers, arched gate)
13. **Bridge** (arched spans over water)
14. **Monument/Column** (commemorative column on pedestal)
15. **Warehouse/Horrea** (large storage building, regular bays)

### Pros/Cons for Roma Aeterna

**Pros:**
- **Fastest to implement.** Each template is a standalone function. You can start with
  3-4 key types and add more incrementally. A temple template might be 100-150 lines.
- **Guaranteed correctness.** Proportions are hardcoded from Vitruvian rules.
- **Easy to debug.** Each template is a normal function you can step through.
- **Natural fit for the existing renderer.** The current renderer already builds
  geometry in JavaScript; templates are a direct evolution of the current approach.
- **LLM's job is minimal.** Just specify type + 5-10 parameters.

**Cons:**
- **Each building type is a separate function.** 15 building types x 100-200 lines =
  1500-3000 lines of template code. Not terrible, but it's a lot of hand-authored
  geometry.
- **Limited compositional flexibility.** A temple template always makes a temple.
  You can't easily combine a temple front with a basilica body. Grammars handle
  this better.
- **Tension with "no templates" philosophy.** This is literally parametric templates.
  However: the output is unique each time (different dimensions, column counts,
  materials, proportions). Two temples from the same template look distinct. The
  template is the *knowledge* of how Roman temples work, not pre-built geometry.
- **Variant explosion.** A basilica with an apse is structurally different from one
  without. A temple in antis vs. prostyle vs. peripteral — each needs code paths.
  Templates can become complex internally.

**Implementation complexity:** MEDIUM. 2-3 weeks for the full set. Individual
templates can be built and tested independently.

### Temple of Saturn Example

LLM output:
```json
{
  "type": "temple",
  "plan": "prostyle",
  "columns_front": 8,
  "order": "ionic",
  "width_m": 17,
  "depth_m": 28,
  "podium_height_m": 3.0,
  "column_material": "grey_granite",
  "podium_material": "travertine",
  "cella_material": "tufa_with_stucco"
}
```

The temple template function:
1. Calculates column diameter from width and column count using eustyle spacing
2. Derives column height (8.5x diameter for Ionic)
3. Derives entablature height (1/5 column height)
4. Derives pediment height (width / 5)
5. Places podium with steps
6. Places 8 columns across the front with bases and Ionic capitals
7. Places entablature beam
8. Builds triangular pediment
9. Builds cella behind the colonnade, offset inward
10. All dimensions are proportionally correct by construction

---

## Approach 3: Constraint-Based Assembly

### How It Works

This approach keeps the current component-listing architecture but adds a
**constraint solver** that fixes up the LLM's output before rendering. The LLM still
lists components, but the renderer validates and adjusts them to be spatially correct.

Think of it as an "autocorrect" layer between the LLM and the renderer.

**Types of constraints:**

1. **Containment:** A cella must fit inside a colonnade. If the LLM specifies a cella
   wider than the colonnade, the solver shrinks it to 65% of colonnade width.

2. **Support:** A pediment must be exactly as wide as the entablature below it. The
   solver forces width matching.

3. **Proportion:** Column height must relate to column diameter by the order's ratio.
   If the LLM says height=0.7 but the diameter implies height=0.5 for Doric, the
   solver adjusts.

4. **Spacing:** Columns must be evenly distributed across the width with appropriate
   intercolumniation. The solver recalculates positions.

5. **Stacking:** Components that the LLM placed incorrectly vertically (e.g., roof
   below walls) get reordered. (The current renderer already does this.)

6. **Coverage:** A roof must span the full width and depth of the building below it.
   The solver expands undersized roofs.

7. **Ground contact:** Doors must be at ground level on exterior walls. Steps must
   connect ground to podium top.

**Implementation as a post-processing pass:**

```javascript
function enforceConstraints(components, buildingType, footprintW, footprintD) {
    // 1. Find the colonnade (if any) and derive column proportions
    const colonnade = components.find(c => c.type === "colonnade");
    if (colonnade) {
        const order = ORDERS[colonnade.style || "ionic"];
        const diameter = footprintW / (colonnade.columns * 2 + 1);
        colonnade.height = diameter * order.heightRatio;
        colonnade.radius = diameter / 2;
    }

    // 2. Force cella to fit inside colonnade
    const cella = components.find(c => c.type === "cella");
    if (cella && colonnade) {
        cella.width = Math.min(cella.width || 0.6, footprintW * 0.65);
        cella.depth = Math.min(cella.depth || 0.7, footprintD * 0.7);
        cella.height = colonnade.height * 0.85; // don't exceed columns
    }

    // 3. Force pediment width to match entablature
    const pediment = components.find(c => c.type === "pediment");
    if (pediment && colonnade) {
        pediment.height = footprintW * 0.15; // Roman shallow pitch
    }

    // 4. Force podium steps to reasonable height
    const podium = components.find(c => c.type === "podium");
    if (podium && colonnade) {
        podium.height = Math.max(podium.height || 0.12,
                                  colonnade.height * 0.25);
    }

    return components;
}
```

### Pros/Cons for Roma Aeterna

**Pros:**
- **Minimal disruption to current architecture.** The LLM still outputs component
  lists. The constraint solver is inserted between LLM output and rendering. Could
  be done in an afternoon for basic constraints.
- **Preserves LLM creativity.** The LLM can still specify unusual combinations; the
  solver only corrects what's geometrically wrong.
- **Incremental adoption.** Start with 5 basic constraints (proportion, containment,
  coverage, support, ground contact) and add more as needed.
- **Respects "no templates" philosophy.** The solver doesn't generate buildings — it
  fixes them. Every building is still LLM-designed.

**Cons:**
- **Doesn't solve the fundamental problem.** The LLM still has to understand what
  components a Roman temple needs. If it forgets the entablature, or adds a dome to
  a basilica, constraints can't fix missing knowledge.
- **Constraint interactions are tricky.** Fixing one constraint can violate another.
  Column height adjustment changes entablature position, which changes pediment
  position. Need iterative solving or careful ordering.
- **Limited geometric correction.** Can adjust sizes and positions, but can't fix
  fundamentally wrong component choices. If the LLM says a domus has a pediment
  and no atrium, constraints can't add the missing atrium.
- **Still requires the LLM to do spatial reasoning** — just with a safety net.
  The buildings will be "not wrong" rather than "correct and beautiful."

**Implementation complexity:** LOW. Basic constraint system is 200-400 lines. Could
be a first step while building a more complete solution.

### Temple of Saturn Example

LLM outputs (with errors):
```json
{
  "components": [
    {"type": "podium", "steps": 3, "height": 0.08, "color": "#c8b88a"},
    {"type": "colonnade", "columns": 8, "style": "ionic", "height": 0.7, "color": "#a0968a"},
    {"type": "cella", "height": 0.5, "width": 0.8, "color": "#d6cdb7"},
    {"type": "pediment", "height": 0.25, "color": "#d4a373"}
  ]
}
```

Problems: podium too low for 8 Ionic columns, cella (0.8) wider than building (0.9),
pediment too tall.

After constraint solving:
```json
{
  "components": [
    {"type": "podium", "steps": 5, "height": 0.14, "color": "#c8b88a"},
    {"type": "colonnade", "columns": 8, "style": "ionic", "height": 0.56, "color": "#a0968a"},
    {"type": "cella", "height": 0.47, "width": 0.55, "depth": 0.6, "color": "#d6cdb7"},
    {"type": "pediment", "height": 0.13, "color": "#d4a373"}
  ]
}
```

The solver: raised podium to 25% of column height, recalculated column height from
diameter-to-height ratio, shrunk cella to 65% of width, adjusted pediment to 1/5
slope ratio.

---

## Approach 4: Reference-Based Generation (Archaeological Data)

### How It Works

Instead of asking the LLM to imagine dimensions, we provide it with actual
archaeological measurements. The system includes a database of known Roman building
dimensions, and the LLM maps a building request to the closest real-world reference.

This is not about copying buildings wholesale — it's about grounding the generation
in real measurements so proportions are correct.

### What We Actually Know: Key Roman Buildings

Archaeological evidence gives us remarkably precise measurements for many Roman
buildings. Key sources:

**Temples:**

- **Temple of Saturn** (rebuilt 42 BC, current remains 4th century AD): 8 Ionic columns
  of grey granite, 11m tall, on a high concrete podium faced with travertine. Front
  columns span ~24m. Podium ~40m x 22.5m, ~9m high (unusually tall because it was
  built into the slope of the Capitoline). Source: Claridge, *Rome: An Oxford
  Archaeological Guide*, 2010.

- **Temple of Castor and Pollux** (rebuilt by Tiberius, 6 AD): 3 surviving Corinthian
  columns, 12.5m tall, of Pentelic marble. Podium ~32m x 50m. Columns had a lower
  diameter of 1.39m, height-to-diameter ratio of ~9:1. Source: LTUR (Lexicon
  Topographicum Urbis Romae).

- **Pantheon** (rebuilt by Hadrian, c. 126 AD): Circular cella, internal diameter 43.3m,
  height to oculus = 43.3m (perfect hemisphere). Portico: 8 Corinthian columns across
  front (12.5m tall, 1.5m diameter), 4 columns deep. Transitional block between
  portico and rotunda. Concrete dome with coffers, 5 rows of 28 coffers. Oculus
  diameter 8.2m. Wall thickness 6.4m at base, tapering upward. Source: Mark and
  Hutchinson, *On the Structure of the Roman Pantheon*, 1986.

- **Maison Carree** (Nimes, c. 4-7 AD): Pseudoperipteral temple. 6 Corinthian columns
  at front, 26.42m x 13.54m overall, podium 2.85m high. Columns 9m tall. One of the
  best-preserved Roman temples. Source: Amy and Gros, *La Maison Carree de Nimes*, 1979.

- **Temple of Vesta** (Forum Romanum): Circular tholos, ~15m diameter, 20 Corinthian
  columns. Multiple rebuilds. Source: LTUR.

**Public Buildings:**

- **Basilica of Maxentius/Constantine** (c. 308-312 AD): Nave 80m x 25m, 35m high.
  Three cross-vaulted bays. Side aisles with barrel vaults at 24.5m height. The
  largest building in the Forum. Source: Claridge 2010.

- **Colosseum** (80 AD): Elliptical, 188m x 156m outer dimensions. 48.5m tall
  (4 stories). 80 arched bays per story. Story heights: ground 10.5m (Tuscan
  half-columns), second 11.1m (Ionic), third 11.5m (Corinthian), fourth 13.6m
  (Composite pilasters, solid wall). Arena 87m x 55m. Source: Hopkins and Beard,
  *The Colosseum*, 2005.

- **Circus Maximus** (rebuilt multiple times): ~621m x 118m. Seating capacity
  ~150,000-250,000. Track width ~79m. Spina (central barrier) ~340m long.
  Source: Humphrey, *Roman Circuses*, 1986.

**Residential:**

- **Insula (Ostia examples):** Typical insula 15-20m wide, 3-5 stories (12-20m tall).
  Roman law under Augustus limited height to ~20m (about 70 Roman feet), reduced by
  Trajan to ~18m. Ground floor tabernae ~4m wide, 4-5m deep, with mezzanine.
  Upper story ceiling height ~3m. Source: Packer, *The Insulae of Imperial Ostia*, 1971.

- **Domus (House of the Faun, Pompeii):** ~3000 sq m (one of the largest). Typical
  domus layout: fauces (entrance) 1.5-2m wide -> atrium 10-12m x 8-10m with impluvium
  ~3m x 4m -> tablinum 4-5m wide -> peristyle garden 15-20m x 10-15m surrounded
  by columns. Single story, ~4-5m ceiling height. Source: Clarke, *The Houses of
  Roman Italy*, 1991.

- **Standard Pompeian domus:** Typically 200-600 sq m. Atrium type: Tuscan (no
  columns), tetrastyle (4 columns), or Corinthian (many columns) around the
  compluvium opening. Source: Wallace-Hadrill, *Houses and Society in Pompeii and
  Herculaneum*, 1994.

**Baths:**

- **Baths of Caracalla** (216 AD): Main block ~220m x 114m. Frigidarium 58m x 24m
  with three groin vaults at 30m height. Caldarium: circular, 35m diameter, domed.
  Tepidarium between them. Natatio (open-air pool) ~50m x 20m. Source: DeLaine,
  *The Baths of Caracalla*, 1997.

- **Baths of Diocletian** (306 AD): Even larger, ~356m x 316m for the entire precinct.
  Frigidarium converted to Santa Maria degli Angeli by Michelangelo — still standing
  at original dimensions. Source: Yegul, *Bathing in the Roman World*, 2010.

**Infrastructure:**

- **Pont du Gard** (aqueduct bridge, 1st century AD): 48.8m tall, 275m long. Three
  tiers of arches. Lower: 6 arches, 22m tall, piers 6m wide. Middle: 11 arches, 20m
  tall. Upper: 35 small arches, 7.4m tall, carrying the water channel. Source:
  Hauck, *The Aqueducts of Ancient Rome*, 2002.

- **Standard Roman aqueduct arcade:** Pier width 2-3m, arch span 4-6m, total height
  per tier 6-10m. Source: Hodge, *Roman Aqueducts and Water Supply*, 2002.

### How to Use This Data

The reference database would be structured as:

```javascript
const ROMAN_REFERENCES = {
    temple_prostyle: {
        exemplars: [
            {
                name: "Temple of Saturn",
                period: "Late Republic / Late Empire",
                dimensions: { width: 22.5, depth: 40, podium_height: 9 },
                columns: { count: 8, order: "ionic", height: 11, diameter: 1.2 },
                materials: ["grey_granite", "travertine", "concrete"],
                notes: "Unusually high podium due to slope. One of oldest in Forum."
            },
            {
                name: "Maison Carree",
                period: "Augustan",
                dimensions: { width: 13.54, depth: 26.42, podium_height: 2.85 },
                columns: { count: 6, order: "corinthian", height: 9, diameter: 0.9 },
                materials: ["limestone"],
                notes: "Pseudoperipteral. Best-preserved example of this form."
            }
        ],
        proportions: {
            podium_to_column_height: [0.25, 0.82], // range from Maison Carree to Saturn
            column_height_to_width: [0.5, 0.7],
            depth_to_width: [1.5, 2.0],
            pediment_slope: [0.18, 0.22] // height/width
        }
    }
};
```

The LLM would say: "Build a Temple of Saturn-style prostyle temple, 8 Ionic columns."
The system looks up the reference, scales it to the available tile footprint, and
generates geometry with archaeologically-grounded proportions.

### Pros/Cons for Roma Aeterna

**Pros:**
- **Archaeological accuracy.** Buildings look correct because they're based on real
  measurements, not LLM guesswork.
- **Rich historical data available.** Roman architecture is extremely well-documented.
  Vitruvius + archaeological surveys provide detailed measurements for hundreds of
  buildings.
- **Natural fit for the Historicus agent.** The fact-checker agent can reference the
  same database to verify accuracy.
- **Educationally valuable.** The generated buildings teach users about real Roman
  architecture.

**Cons:**
- **Database curation effort.** Collecting, verifying, and structuring archaeological
  measurements for all building types is research-intensive.
- **Only works for known types.** Unusual or unique buildings (a fictional senator's
  villa) have no reference. Need fallback to proportional rules.
- **Not generative in itself.** This is a data source, not a generation method. It
  must be combined with another approach (templates, grammars, or constraints) to
  actually produce geometry.
- **Scaling issues.** Real buildings are 15-40m wide; Roma Aeterna tiles are 10m each.
  A Temple of Saturn would be 2-4 tiles wide. Need careful scale mapping.

**Implementation complexity:** LOW-MEDIUM for the database itself. The database is
just a JSON file. The challenge is integrating it with whatever generation approach
is chosen.

---

## Approach 5: Hybrid Approach for Browser/Three.js

### The Recommended Architecture

After analyzing all approaches, the strongest design for Roma Aeterna combines
elements of all four into a layered system. Here is the recommended architecture,
ordered from highest-impact to lowest:

#### Layer 1: Parametric Type Templates (Primary Generator)

Write dedicated generation functions for the 10-15 core Roman building types. Each
template:

- Takes ~5-10 parameters from the LLM (column count, order, materials, etc.)
- Encodes Vitruvian proportional rules to derive all spatial dimensions
- Generates Three.js geometry directly (no intermediate representation)
- Produces a unique building every time via parameter variation

This is the **core generator**. It replaces the current flat component-stacking system.

Why templates over grammars: A full CGA Shape grammar engine is over-engineered for
15-20 building types in a browser. Templates are simpler, faster to write, easier to
debug, and produce the same results for a finite type set. Grammars make sense when
you have hundreds of building types or need users to author new types at runtime —
neither applies here.

Why this doesn't violate "no templates": The templates encode *architectural knowledge*
(Vitruvian proportions, Roman construction methods), not pre-built geometry. The LLM
still controls what gets built and how it varies. Two temples from the same template
will look as different as two real Roman temples do — because real temples also follow
the same proportional rules with different parameters.

#### Layer 2: Constraint Solver (Safety Net)

Add a lightweight constraint layer that runs on the template output before rendering.
Even templates can produce edge-case geometry problems (e.g., a cella that's too
tall at extreme parameter values). The constraint solver:

- Verifies all components fit within the footprint
- Enforces minimum/maximum proportional ranges from archaeological data
- Ensures structural plausibility (supports, spans, clearances)
- Clamps extreme values to historically attested ranges

This is 200-300 lines and catches the long tail of geometry bugs.

#### Layer 3: Archaeological Reference Database (Knowledge Base)

Maintain a JSON database of real Roman building dimensions. This serves two purposes:

1. **LLM grounding.** Include relevant reference data in the Urbanista prompt so the
   LLM makes better parameter choices. "The Temple of Saturn had 8 Ionic columns of
   grey granite, 11m tall, on a 9m podium" gives the LLM concrete numbers to work from.

2. **Constraint calibration.** The solver's acceptable ranges come from archaeological
   data, not arbitrary limits.

3. **Historicus validation.** The fact-checker agent can compare generated buildings
   against known references.

#### Layer 4: Grammar-Like Composition (For Complex Types Only)

A few building types benefit from a simplified split-grammar approach:

- **Insula facades:** Split the facade into stories, split each story into bays, fill
  bays with windows/doors/shops. This is a 3-level grammar that's easy to implement
  as nested loops.

- **Amphitheater facades:** Repeat arched bays around an ellipse, with different orders
  per story. Again, nested loops with parametric variation.

- **Forum layouts:** Split the surrounding portico into bays, place tabernae behind
  the portico, place a temple at one end. This is compositional grammar at the urban
  scale.

You don't need a general-purpose grammar engine — just the split-repeat pattern
implemented directly in the template functions for building types that use it.

### Performance Considerations for Browser/Three.js

**Geometry budget:** A complex scene with 50-100 buildings, each made of 50-200
meshes (columns, walls, roof faces, decorations) is 2,500-20,000 meshes. This is
fine for modern browsers with Three.js, especially with:

- **Material caching** (already implemented in the current renderer)
- **InstancedMesh for columns** — a temple with 40 columns can use a single
  InstancedMesh with 40 transforms instead of 40 separate meshes. This is a major
  performance win. Each colonnade goes from 40 draw calls to 1.
- **LOD (Level of Detail)** — buildings far from the camera can use simplified geometry
  (single textured box instead of full column detail). Three.js supports LOD natively.
- **Geometry merging** — merge all static geometry per building into a single
  BufferGeometry using `BufferGeometryUtils.mergeGeometries()`. Reduces draw calls
  dramatically.

**Memory budget:** Three.js geometry is stored in Float32Arrays. A building with
200 meshes might use 500KB of geometry data. 100 buildings = 50MB. Comfortable for
modern browsers.

**Texture budget:** The current renderer uses flat colors (MeshStandardMaterial with
a color). For more visual richness without textures:
- Procedural noise on materials (vertex color variation)
- Subtle geometry variation (slightly irregular column spacing, worn edges)
- Ambient occlusion baked into vertex colors

If textures are added later, use a texture atlas (one 2048x2048 texture with all
Roman materials: marble, travertine, brick, tufa, granite, etc.).

### Simplified LLM Interface

The LLM's output format would change from listing components to specifying building
parameters:

```json
{
  "building_type": "temple",
  "variant": "prostyle",
  "params": {
    "columns_front": 8,
    "order": "ionic",
    "width_tiles": 3,
    "depth_tiles": 4,
    "podium_height": "high",
    "column_material": "grey_granite",
    "wall_material": "travertine",
    "condition": "partial_ruins"
  },
  "reference": "Temple of Saturn, Forum Romanum",
  "unique_features": ["unusually tall podium due to slope", "reconstructed in 4th century"]
}
```

The "unique_features" field allows the LLM to request building-specific modifications
that the template applies as post-processing: add weathering, remove some columns
for ruins, add a specific inscription, etc.

The enum values ("high", "medium", "low" for podium_height; specific order names;
specific material names) constrain the LLM to valid choices without requiring spatial
reasoning.

---

## Approach 6: What Games and Projects Do Well

### Caesar III (1998) and Caesar IV (2006)

**Approach:** Pure tile-based with pre-modeled 3D assets. Each building type has 1-3
fixed models. Buildings snap to a grid. No procedural generation of individual
buildings.

**What works:** The city feels coherent because every building follows the same visual
language and scale. Roads automatically connect. Services have radii of effect.

**What doesn't apply:** Zero procedural architecture. Every model was hand-built.
This is the opposite of what Roma Aeterna needs.

**Relevant lesson:** The *urban planning* rules (road connectivity, service coverage,
desirability mechanics) create emergent city layouts that look Roman. The lesson is
that good city-scale composition can compensate for simple individual buildings.

### Anno 1800 / Anno 1404

**Approach:** Modular prefab buildings on a grid. Buildings are hand-modeled but have
multiple visual variants (3-5 skins per building type) selected randomly. Higher-tier
buildings upgrade visually. Construction animations with scaffolding.

**What works:** The visual variety from random skin selection is surprisingly effective.
5 variants of an insula create the illusion of unique architecture.

**Relevant lesson:** Even modest parametric variation (5 color/detail variants per
building type) produces a convincing cityscape. This suggests that Roma Aeterna's
templates don't need infinite variation — 10-20 meaningfully different parameter
combinations per building type would be enough.

### Townscaper (2021, Oskar Stalberg)

**Approach:** Algorithmic architecture using **wave function collapse** (WFC) with
custom modifications. The system uses a grid of voxels, and each voxel's
configuration is determined by its neighbors through constraint propagation.

**How it works in detail:**
1. The world is a hex-based grid of voxels.
2. Each voxel can be one of ~70 pre-made modules (wall piece, corner, roof edge,
   arch, window, stair, etc.).
3. When you place or remove a block, WFC propagates constraints to all neighbors.
   A wall piece above ground must have a floor or another wall below it. A roof piece
   requires walls below and open air above. Corner pieces connect perpendicular walls.
4. The system resolves all constraints simultaneously, choosing modules that satisfy
   all neighbor requirements.
5. Color is deterministic from grid position (each column of voxels has a fixed color).

**What works brilliantly:** Every building looks plausible despite the player having
zero architectural knowledge. The constraint system guarantees that walls support
roofs, arches form where walls meet at ground level, stairs appear at height
transitions. The buildings have a charming, organic quality.

**What doesn't apply directly:** Townscaper's architectural language is generic
Mediterranean, not specifically Roman. The module library is hand-crafted. WFC is
compute-intensive for real-time updates.

**Relevant lesson:** **Neighbor-based constraints** are extremely powerful for making
buildings look right. If component A is above component B, and component C is next
to component A, then there's a rule about what C can be. This is applicable to
Roma Aeterna as a supplement to templates — after the template generates the base
building, a constraint pass could add contextual details (stairs where a building
meets a road, wall connections between adjacent buildings, etc.).

### Foundation (2019, Polymorph Games)

**Approach:** Zone-based organic city growth. Players paint zones (residential,
monument, etc.) and buildings grow procedurally within them. Buildings are assembled
from modular pieces with randomized variation.

**How it works:**
1. Player paints a residential zone.
2. The game's procedural system determines building footprints within the zone using
   lot subdivision (similar to CGA Shape's lot split).
3. Each lot generates a building by stacking modular pieces: foundation, walls, floor,
   roof. Pieces are selected from a library with random variation.
4. As the settlement grows, buildings upgrade: taller, more detailed, better materials.

**What works:** The organic, unplanned look is perfect for medieval villages (the
game's setting). Buildings cluster naturally along roads.

**What doesn't apply:** Roman cities were *planned*, not organic. The cardo-decumanus
grid system means Roma Aeterna needs more geometric precision than Foundation's
organic growth.

**Relevant lesson:** **Zone-based generation** is a useful abstraction. Instead of
placing individual buildings, the LLM could define zones ("residential insula quarter
here, forum complex there") and the system generates appropriate buildings to fill
each zone. This maps well to how Cartographus currently defines districts.

### CityEngine (Esri)

**Approach:** Full CGA Shape grammar implementation. Users write grammar rules that
generate buildings from lot footprints. The primary application is modern urban
planning, but there are Roman archaeology projects:

- **Rome Reborn** project (UCLA/Univ. of Virginia) used CityEngine to reconstruct
  Rome at the time of Constantine (320 AD). They wrote CGA grammars for Roman
  building types based on archaeological data.
- **Procedural Pompeii** projects have used CityEngine to reconstruct Pompeii from
  excavation data.

**How Rome Reborn works:**
1. GIS data provides the footprint of every known building in ancient Rome.
2. CGA grammars transform footprints into 3D buildings based on building type.
3. Key buildings (Colosseum, Pantheon, etc.) use manually-modeled meshes.
4. Everything else (insulae, domus, tabernae) uses parametric grammars.

**What works:** The scale is staggering — entire districts generated procedurally.
The grammars ensure consistency while allowing variation.

**What doesn't apply directly:** CityEngine is a desktop application with its own
runtime. The grammar interpreter is proprietary. Can't run in a browser.

**Relevant lesson:** The Rome Reborn team's approach of **grammars for common
buildings + manual models for landmarks** is pragmatic and effective. Roma Aeterna
could do similar: detailed parametric templates for standard types + the LLM has
more creative freedom for unique landmarks.

### Oikumene (indie Ancient Greek/Roman city builder, in development)

**Approach:** Uses a combination of procedural placement rules and pre-modeled
building variants. Buildings have functional requirements (temples need open space
in front, houses cluster along roads, markets need road access) that drive placement.

**Relevant lesson:** Functional placement constraints create more realistic cities
than pure aesthetic rules.

### Summary of Game Lessons

| Technique | Used By | Applicable to Roma Aeterna? |
|---|---|---|
| Pre-modeled assets | Caesar III, Anno | No (violates generative principle) |
| Random skin variants | Anno | Partially (parameter variation achieves same goal) |
| WFC neighbor constraints | Townscaper | Yes, for contextual details and building connections |
| Zone-based generation | Foundation | Yes, maps to current district system |
| CGA Shape grammars | CityEngine | Yes, in simplified form within templates |
| Modular stacking | Foundation | Already in use, needs improvement |
| Functional placement | Oikumene | Yes, for urban-scale realism |

---

## Implementation Recommendation

### Phase 1–2: Generative constraints and tradition (current)

Cross-part numeric limits come only from optional **spec.proportion_rules** (and the renderer applies only keys the model sends). **spec.tradition** is a free-form string from the agents. No Vitruvian defaults in code and no static reference JSON.

### Phase 3: Parametric / generative composition (implemented direction)

The live client uses a **role-based stack** (foundation → structural → infill → roof → decorative → freestanding) with optional **stack_role** / **stack_priority** overrides, plus **type `procedural`** (primitive parts: box, cylinder, sphere, cone, torus, plane) for novel massing. **spec.tradition** and **proportion_rules** are fully generative from agents — no static JSON catalogs. Unknown component types **fail validation** on the server (nothing stripped).

### Phase 3 (parametric templates) — implemented

`static/parametric_templates.js` includes **`open`** (culture-agnostic: `params.components` plus optional `ref_w`/`ref_d` scaling) and optional **shortcuts** named after common Greco-Roman forms (`temple`, `basilica`, …) — shortcuts are convenience only; **any** culture should use **`open`** or raw **`spec.components`** with **`procedural`** parts where named types do not fit. The anchor spec may set **`spec.template`: `{ "id", "params" }`** instead of **`spec.components`** (mutually exclusive; validated in `orchestration/validation.py`). Expansion yields the same component arrays; the renderer still runs `_buildComponents`.

### Phase 4: Contextual Polish (implemented)

Neighbor-aware post-pass in `static/renderer3d.js` (`spec.phase4` optional overrides; validated in `orchestration/validation.py`):

- Steps and street fascia where a footprint faces **roads**; sloped **awnings** and small **sign** boards on road fronts
- **Party walls** where another building abuts
- **Mooring posts** at **water**, **hedges** at garden/forum edges
- **Ruin ivy** (density from `ruin_overgrowth` or `building_type`)

Frustum culling adds a small height margin when Phase 4 is active so façade extras do not pop out of view.

**Next (optional) roadmap item:** Deeper template libraries (more parameters, non-Roman typologies via procedural emphasis) or Vitruvian reference data — see appendix.

### What NOT to Build

- **Full CGA Shape grammar engine.** Over-engineered for 15-20 building types. The
  simplified split-repeat pattern inside templates gives 90% of the benefit.
- **Wave Function Collapse.** Cool technology, wrong fit. Roma Aeterna's buildings
  are designed by an AI agent, not generated by constraint propagation. WFC is for
  emergent, unplanned architecture.
- **Texture atlases or PBR materials.** Nice-to-have but not the bottleneck. Flat
  colors with proper geometry look better than textured garbage geometry.
- **Custom 3D model imports.** Pre-modeled assets violate the generative principle.

### Estimated Total Timeline

- Phase 1 (constraints): 1 week
- Phase 2 (reference data): 1 week (parallel)
- Phase 3 (templates): 2-3 weeks
- Phase 4 (contextual): 1 week
- **Total: 4-5 weeks** to go from "buildings look wrong" to "buildings look like
  plausible Roman architecture with unique variation."

### Key Architectural Decision: Where Does Knowledge Live?

The current system puts all architectural knowledge in the LLM (via the Urbanista
prompt). This fails because LLMs are bad at spatial reasoning.

The recommended system distributes knowledge across three layers:

| Knowledge | Where It Lives | Example |
|---|---|---|
| What to build | LLM (Urbanista) | "Build a prostyle Ionic temple" |
| How it should look | Reference database | `data/architectural_reference.json` — curated ranges; `orchestration/reference_db.py` injects the best match into Urbanista by city/year/building_type |
| How parts fit together | Template functions + stack | Column height = diameter × order ratio; `parametric_templates.js` optional |

The LLM does what LLMs are good at (language, history, creative decisions). The code
does what code is good at (spatial math, proportional relationships, geometric
assembly). The database does what databases are good at (storing measurements).

**Functional placement:** `orchestration/placement.py` checks survey `master_plan` for (1) road adjacency for commerce, (2) water adjacency for bridges, (3) ceremonial buildings (`temple`, `monument`, `basilica`) not stranded far from roads/plazas. Warnings are logged, broadcast as `placement_warnings` to the client, and summarized in chat. Survey prompt (`CARTOGRAPHUS_SURVEY`) encodes the same rules for Cartographus.

**Reference data file:** `data/architectural_reference.json` (v2+) holds typology entries with `match` filters and `proportion_rules_hints`. `orchestration/reference_db.py` resolves the best match by city/year/building_type; `format_reference_for_historicus` feeds Historicus, `format_reference_for_prompt` feeds Urbanista (after golden example scaling).

---

## Appendix: Vitruvian Proportional System (Quick Reference)

For implementation in template functions.

### Column Orders

| Property | Tuscan | Doric | Ionic | Corinthian | Composite |
|---|---|---|---|---|---|
| Height/Diameter | 7 | 7 | 8-9 | 10 | 10 |
| Has base | Yes | No | Yes | Yes | Yes |
| Capital height | 0.5D | 1D | 0.33D | 1.16D | 1.16D |
| Entablature/Column | 1/4 | 1/4 | 1/5 | 1/5 | 1/5 |
| Typical use | Utilitarian | Military, early | Religious, civic | Grand temples | Arches |

### Temple Proportions

| Ratio | Value | Source |
|---|---|---|
| Podium height / Column height | 0.25-0.35 | Measured from multiple examples |
| Pediment height / Width | 0.15-0.22 | Vitruvius III.5.12 (Roman shallower than Greek) |
| Cella width / Total width (peripteral) | 0.55-0.65 | Standard peripteral plan |
| Depth / Width | 1.5-2.0 | Vitruvius III.4 |
| Intercolumniation (eustyle) | 2.25 diameters | Vitruvius III.3.6 |
| Column taper (top/bottom diameter) | 0.83 | Vitruvius III.3.12 |
| Entasis (midpoint swelling) | +1/150 of height | Vitruvius III.3.13 |

### Insula Proportions (from Ostia excavations)

| Property | Value |
|---|---|
| Story height | 3.0-3.5m |
| Max stories | 5-6 (limited by law to ~20m) |
| Ground floor taberna width | 3.5-4.5m |
| Ground floor taberna depth | 4-6m |
| Window height | 1.0-1.2m |
| Window width | 0.6-0.8m |
| Wall thickness (ground floor) | 0.45-0.60m |
| Balcony projection (where present) | 0.6-1.0m |

### Domus Proportions (from Pompeii)

| Property | Value |
|---|---|
| Atrium width | 7-12m |
| Atrium depth | 8-14m |
| Impluvium | ~1/3 of atrium width |
| Ceiling height | 4-6m |
| Peristyle columns | 8-20, typically 2.5-3.5m tall |
| Tablinum width | 3-5m |
| Fauces (entrance) width | 1.5-2.5m |

### Common Roman Materials (for color mapping)

| Material | Color Hex | Use |
|---|---|---|
| Travertine | #c8b88a | Podiums, walls, general construction |
| Carrara marble | #e8e0d0 | Temples, prestigious columns |
| Pentelic marble | #f0e8d8 | Imported Greek marble, rare temples |
| Grey Egyptian granite | #a0968a | Columns (Temple of Saturn) |
| Red Egyptian granite | #b86b5a | Prestige columns |
| Tufa (cappellaccio) | #a89070 | Early construction, foundations |
| Roman brick (opus testaceum) | #b5651d | Insulae, imperial buildings |
| Opus reticulatum (tufa net) | #b8a880 | Late Republic walls |
| Concrete (opus caementicium) | #9a9080 | Cores of walls, domes, vaults |
| Terracotta (roof tiles) | #c45a3c | Tegulae and imbrices roofing |
| Bronze (weathered) | #6b8e5a | Doors, decorative elements (green patina) |
| Lead | #8a8a8a | Pipe clamps, roof flashings |
| Stucco (painted) | #e8d8c0 | Wall finishes, available in many colors |
