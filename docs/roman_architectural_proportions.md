# Roman Architectural Proportions Reference

Reference for procedural generation of historically accurate Roman buildings.
All ratios are derived from Vitruvius (*De Architectura*, c. 30-15 BCE),
archaeological survey data, and surviving structures.

Units: Romans used the *pes* (foot, ~0.296m) and *cubitus* (cubit, 1.5 pedes).
All proportions below are expressed as dimensionless ratios so they can be
scaled to any unit system.

---

## 1. Roman Temple

### Source Structures
Temple of Saturn (Rome, 497 BCE rebuilt 42 BCE), Temple of Jupiter Optimus
Maximus (Capitoline), Temple of Portunus, Maison Carree (Nimes), Temple of Mars
Ultor (Forum of Augustus).

### Column Orders -- Height-to-Diameter Ratios (Vitruvius Book 3-4)

| Order      | H:D ratio | Entablature:Column H | Capital height | Fluting |
|------------|-----------|----------------------|----------------|---------|
| Tuscan     | 7:1       | 1:4                  | 0.5 D          | none    |
| Doric      | 7:1 (Roman) to 8:1 | 1:4          | 1.0 D          | 20 flat channels |
| Ionic      | 8:1 to 9:1 | 1:5                 | 0.33 D (volute)| 24 channels |
| Corinthian | 9:1 to 10:1 | 1:5               | 1.17 D         | 24 channels |
| Composite  | 10:1      | 1:5                  | 1.17 D         | 24 channels |

Note: Greek Doric is shorter (5.5:1 to 6:1). Roman Doric is taller. Vitruvius
Book 4 Ch.1 specifies column height including capital and base for each order.

### Column Diameter (D) -- The Module

The lower column diameter D is the fundamental module. Vitruvius bases almost
every temple dimension on D (or half-D, the *modulus*).

- Column base height: 0.5 D (Attic base, used for Ionic and Corinthian)
- Column shaft taper: upper diameter = 5/6 of lower diameter (for columns 15-20 ft tall). Vitruvius Book 3, Ch.3.12 gives a diminution table by height.
- Entasis (slight swelling): at 1/3 height, diameter is roughly 1/150 D wider than a straight taper line

### Intercolumniation (Vitruvius Book 3, Ch.3)

Vitruvius names five intercolumniation types by the clear spacing between column
shafts, measured in lower diameters D:

| Name          | Spacing    | Notes |
|---------------|------------|-------|
| Pycnostyle    | 1.5 D      | columns too close; stone architraves span safely |
| Systyle       | 2.0 D      | standard tight spacing |
| Eustyle       | 2.25 D     | Vitruvius's preferred ("best"); central span 3 D |
| Diastyle      | 3.0 D      | wide; requires wooden architraves |
| Araeostyle    | 4.0 D+     | Tuscan only; very wide |

For procedural generation, use **eustyle (2.25 D)** as the default, with the
center intercolumniation widened to **3 D** (Vitruvius Book 3.3.6).

### Podium

- Podium height: typically 1/5 to 1/4 of overall column height (including base and capital).
  Archaeological median: ~1.5 m for a temple with 9m columns, so roughly **H_podium = column_height / 5 to column_height / 6**.
- Temple of Saturn podium: ~3.2m with 11m columns (ratio ~1:3.4) -- unusually tall.
- Maison Carree: podium 2.65m, columns 9.1m (ratio ~1:3.4).
- Typical range: podium = **0.2 to 0.3 x total column height**.
- Podium extends **2-3 D** beyond the outer column line on all sides.
- Front steps only (Roman convention, unlike Greek temples with steps all around). Step width ~1 pes (0.3m), step height ~0.5 pes (0.15m).

### Pediment

- Pediment pitch (Vitruvius Book 3, Ch.5): rise = **1/4.5 to 1/5 of the full entablature width** (i.e., tympanum height ~2/9 of pediment width).
- This yields a pediment angle of approximately **15 to 18 degrees** from horizontal on each side (total included angle ~144-150 degrees).
- Maison Carree measured: ~15.5 degrees.
- Raking cornice follows the pitch; horizontal cornice forms the base.
- Acroterion ornaments at apex and corners: height ~0.5 to 0.75 D.

### Cella (Naos)

- Cella interior width: equal to the **distance between the inner row of columns** (if pseudoperipteral) or the interior peristyle span.
- Cella length-to-width ratio: typically **2:1** (Vitruvius Book 4, Ch.4).
- Cella wall thickness: ~2.5-3 pedes (0.75-0.9m).
- Cella door: width = 0.5 x cella interior width at the threshold (Vitruvius Book 4, Ch.6). Door height = door width x 2.5 (Ionic/Corinthian) or door width x 2.33 (Doric).

### Overall Plan -- Pseudoperipteral Hexastyle (most common Roman type)

- Front: 6 columns (hexastyle).
- Side columns engaged into cella wall (pseudoperipteral) -- standard Roman form.
- Flank column count: typically 11 (for 6-front temple) following the rule: **side columns = 2 x front columns - 1**.
- Stylobate width:length ratio approximately **1:2** for hexastyle temples.

### Materials and Color

- Structure: *opus quadratum* (large ashlar blocks), or concrete core with travertine/marble veneer.
- Columns: white Luna marble (Carrara) or travertine (warm cream/honey color, #F5E6C8).
- Podium: tufa or travertine; sometimes stuccoed. Color: warm grey-tan (#C8B89A).
- Entablature and pediment: originally painted. Traces show deep red (#8B1A1A), blue (#1E3A5F), gold leaf on moldings.
- Roof tiles: terracotta (orange-red, #C45A3C) or gilt bronze.
- Cella interior walls: painted stucco, marble revetment.
- Floor: *opus sectile* (marble inlay) or mosaic in polychrome geometric patterns.

---

## 2. Basilica

### Source Structures
Basilica Ulpia (Trajan's Forum), Basilica Aemilia (Roman Forum), Basilica of
Maxentius, Basilica Julia. Vitruvius Book 5, Ch.1.

### Nave and Aisle Proportions

- Nave width: **1/3 of total interior width** (Vitruvius). With two flanking aisles, each aisle = 1/3 of nave width (so aisle = 1/9 of total). In practice, double-aisled basilicas: nave = center 1/3, inner aisle = roughly half the nave width each side.
- If galleries (upper story above aisles): gallery height = **3/4 of lower aisle column height** (Vitruvius Book 5.1.5).
- Total interior height of nave (ground to coffered ceiling): **equal to the combined width of nave + one aisle** (Vitruvius).

### Specific Proportions (Vitruvius's own basilica at Fanum)

- Overall length-to-width: **3:1** (120 x 60 pedes in his example).
- Nave columns: Corinthian, 50 pedes tall (H:D = 10:1, so D = 5 pedes).
- Aisle columns (upper order): 20 pedes, supporting the gallery.
- Intercolumniation: approximately 2 D (systyle).

### Basilica of Maxentius (late imperial, vaulted type)

- Nave: 25m wide, 35m tall (groin-vaulted, not timber-roofed).
- Side bays: 3 barrel-vaulted bays per side, each ~23m x 17m.
- Apse: semicircular, diameter = nave width.

### Apse

- Apse is semicircular in plan; diameter = **nave width or slightly less**.
- Apse half-dome height = apse radius (producing a perfect hemisphere).
- Apse raised on a low platform (~0.5-1m above nave floor).

### Column Spacing

- Interior columns: systyle (2 D) or eustyle (2.25 D).
- Two-story colonnades: lower Ionic or Corinthian; upper Corinthian or Composite.
- Upper columns rest on a continuous entablature above lower columns.

### Clerestory

- Clerestory windows above aisle roofline, in the upper nave wall.
- Window height: **1/3 to 1/2 of the clerestory wall height** (measured from aisle roof to nave ceiling spring).
- Window spacing follows column bay rhythm.

### Materials and Color

- Columns: polychrome marble (Phrygian purple = #6B3A6B, Numidian yellow = #D4A017, grey granite = #808080, green cipollino = #4A7A5B).
- Walls: brick-faced concrete (*opus testaceum*), often with marble veneer.
- Floor: polychrome *opus sectile* in large geometric panels.
- Ceiling: coffered timber (gilded, painted blue = #1E3A5F for sky representation) or concrete vaulting.
- Brick color: warm orange-red (#B85C3A).

---

## 3. Insula (Apartment Block)

### Source Structures
Ostia Antica insulae (best preserved examples: Casa di Diana, Casa dei Dipinti,
Insula dell'Ara Coeli), literary sources (Juvenal, Martial, Strabo).

### Height Regulations

- Augustus limited building height to **70 pedes (~20.7m)**.
- Trajan lowered the limit to **60 pedes (~17.8m)**.
- In practice: **4-5 stories typical**, occasionally 6-7 in the late Republic before regulation.
- Ground floor (*taberna*) height: **3.5-4m** (taller, for commercial use).
- Upper story height: **2.7-3.0m** (decreasing slightly with each floor).
- Top story sometimes only **2.2-2.5m** (low-rent garrets).

### Footprint

- Standard insula footprint: **12-18m wide x 20-40m deep** (varies greatly).
- Ostia examples: commonly 300-600 m2 ground area.
- Built around a central light well or courtyard (~3-5m wide) in larger insulae.
- Smaller insulae had no courtyard: rooms lit only from street windows.

### Facade Proportions

- Ground floor: wide arched openings for *tabernae* (shops). Arch span: **2.5-3.5m**, pier width: **0.5-0.8m** (~1/5 of arch span).
- Mezzanine (*pergula*) above shops: ~2m height, small square windows.
- Upper stories: rectangular windows, **0.8-1.2m wide, 1.0-1.5m tall** (width:height roughly 3:4 to 2:3).
- Window-to-wall ratio on upper facades: **~25-35%** (regular rhythm, spaced ~1.5-2m center-to-center).
- Balconies (*maeniana*): projecting **0.8-1.5m** from facade, timber or masonry brackets. Common on 1st and 2nd upper stories. Not on every insula.

### Wall Construction

- *Opus reticulatum* (diagonal diamond pattern of tufa blocks) through 1st century CE.
- *Opus testaceum* (brick-faced concrete) standard from Trajanic period onward.
- Wall thickness: **0.45-0.6m** (opus testaceum). Ground floor bearing walls sometimes 0.6-0.8m.
- Party walls shared between adjacent insulae.

### Materials and Color

- Walls: exposed brick (*opus testaceum*), warm orange (#C07040) to deep red-brown (#8B4513).
- Sometimes stuccoed and painted: white (#F0EAD6), cream (#FAEBD7), or ochre (#CC7722).
- Window frames: timber (dark brown, #4A3728).
- Balconies: timber (weathered grey-brown, #6B5B4E).
- Roof tiles: terracotta (#C45A3C), low-pitched (~15-20 degrees).
- Ground floor *taberna* threshold: travertine or basalt.

---

## 4. Domus (Private House)

### Source Structures
House of the Faun, House of the Vettii, House of the Surgeon (Pompeii),
literary descriptions (Vitruvius Book 6, Ch.3-7).

### Atrium Proportions (Vitruvius Book 6, Ch.3)

Vitruvius gives three canonical atrium proportions (width W to length L):

| Type        | W:L ratio   |
|-------------|-------------|
| First       | 3:5         |
| Second      | 2:3         |
| Third       | 1:sqrt(2) (~1:1.414) |

The atrium is the central hall. Its **height to the underside of the roof beam
= 3/4 of its width** (Vitruvius Book 6.3.1).

### Compluvium and Impluvium

- Compluvium (roof opening): width = **1/4 to 3/7 of atrium width**; length proportional.
- Impluvium (rainwater basin in floor): directly below compluvium, same plan dimensions or slightly larger (~0.3m border around compluvium projection).
- Impluvium depth: ~0.3-0.5m.
- Impluvium is centered in the atrium.

### Room Arrangement (Vitruvius Book 6, Ch.3-5)

Axial plan along the *fauces* (entrance corridor) -> atrium -> tablinum -> peristyle:

1. **Fauces** (entrance passage): width = 1/2 to 2/3 of tablinum width; length ~3-5m.
2. **Atrium**: as above. Side rooms (*alae*, *cubicula*) open off it.
3. **Alae** (side recesses): width = **1/3 of atrium width** if atrium width is 30-40 pedes. Length = same as width (square plan) or slightly deeper.
4. **Tablinum** (master's office, at rear of atrium): width = **2/3 of atrium width** if atrium width is 20 pedes; **1/2 of atrium width** if wider. Height = width + 1/8 width (Vitruvius Book 6.3.5).
5. **Cubicula** (bedrooms): typically **3m x 4m to 4m x 5m**. Height equal to half the sum of width + length.
6. **Triclinium** (dining room): length = **2x width** (for three couches in a U-shape). Width typically 4-6m.

### Peristyle Garden

- Behind the tablinum, a colonnaded garden courtyard.
- Peristyle length (along the main axis) = **1/3 greater than its width** (Vitruvius Book 6.3.7). So L:W roughly 4:3.
- Column height: typically Ionic or Corinthian, H:D = 8:1 to 9:1.
- Intercolumniation: eustyle (2.25 D) or diastyle (3 D) for gardens (wider for views).
- Garden area: 40-60% of total domus footprint in wealthier homes.

### Overall Domus Footprint

- Modest domus: ~300-500 m2.
- Wealthy domus: 800-3000 m2 (House of the Faun: ~2970 m2).
- Typical plot width along street: 10-20m.
- Depth: 25-60m (deep narrow plots common).
- Single story plus possible upper partial story (over rear rooms).

### Materials and Color

- Walls: *opus incertum* or *opus reticulatum* (tufa) in earlier periods; *opus testaceum* later.
- Interior walls: painted plaster in the four Pompeian styles.
  - Pompeian Red: #8E2323 (the famous deep red).
  - Pompeian Black: #1A1110.
  - Pompeian Yellow: #CEAC5E.
  - Pompeian Green: #355E3B.
  - Pompeian Blue (Egyptian blue): #1034A6.
- Floors: *opus signinum* (red-pink morite, #C48882) with tesserae, or black-and-white mosaic, or polychrome mosaic.
- Columns: plastered and painted, or marble (in wealthy homes).
- Impluvium: marble-lined (white #F0F0F0 or colored marble).
- Roof: terracotta tiles (#C45A3C).
- Timber: dark wood (#3B2716) for door frames, ceiling beams.

---

## 5. Thermae (Public Baths)

### Source Structures
Baths of Caracalla (216 CE), Baths of Diocletian (305 CE), Baths of Trajan,
Stabian Baths (Pompeii), Forum Baths (Pompeii). Vitruvius Book 5, Ch.10.

### Room Sequence

The canonical bathing sequence progresses through increasing heat:

1. **Apodyterium** (changing room) -- entrance.
2. **Palaestra** (exercise yard) -- open courtyard.
3. **Frigidarium** (cold room) -- unheated, with cold plunge pool.
4. **Tepidarium** (warm room) -- moderately heated.
5. **Caldarium** (hot room) -- heavily heated, with hot plunge (*alveus*) and basin (*labrum*).

This sequence is usually arranged so tepidarium is between frigidarium and
caldarium, sharing a wall with the *hypocaust* furnace system.

### Proportions by Room

#### Frigidarium
- Largest room in the bath complex.
- Baths of Caracalla frigidarium: **56m x 24m, 30m tall** (triple groin vault).
- Plan ratio: roughly **2:1 to 2.5:1** length-to-width.
- Central cold plunge pool: circular or rectangular, **8-15m diameter**, depth ~1.2-1.5m.
- Ceiling: groin vault or cross vault; height = **1.25 to 1.5 x room width**.

#### Tepidarium
- Medium room, transitional.
- Typically **1/2 to 2/3 the area of the frigidarium**.
- Plan: rectangular or square, ~1.5:1.
- Barrel vaulted ceiling, height = width.
- Vitruvius: "the tepidarium should be well lit by a large window on the west side."

#### Caldarium
- Oriented south or southwest to capture solar heat.
- Circular or apsidal plan common.
- Vitruvius (Book 5.10.1): "the caldarium and tepidarium should receive light from the west."
- Plan: rectangular with a large apse at one end for the *alveus* (hot pool).
  Apse diameter = room width.
- Room width:length = roughly **1:1.5**.
- Dome over circular caldaria: hemisphere, diameter = room width.
  Baths of Caracalla caldarium: 35m diameter dome.
- *Alveus* (hot plunge): semicircular, 3-5m diameter, depth ~0.8-1m, set into the apse.
- *Labrum* (cold-water basin): circular, ~1.5-2m diameter, on a pedestal at the opposite end.

### Vaulting

- Roman baths pioneered large-span concrete vaults.
- Barrel vault height = 1/2 span (semicircular profile) -- the standard.
- Groin vault: intersection of two barrel vaults at 90 degrees, allowing clerestory windows at lunettes.
- Dome: hemispherical; inner diameter = room width. Oculus at top = ~1/9 of diameter.
- Vault thickness: decreases from ~2m at the springing to ~1m at the crown (for large spans).
- Coffering: reduces weight, provides decoration. Coffers are typically square or octagonal, depth ~0.3-0.5m.

### Overall Complex

- Large imperial thermae: **300m x 300m** total precinct (including gardens, libraries, shops).
- Bathing block alone: ~200m x 120m (Baths of Caracalla).
- Symmetrical plan along a central axis.
- Palaestra: open courtyard, ~50m x 50m on each side of the bathing block.
- *Natatio* (open-air swimming pool): ~50m x 25m, depth ~1-1.5m. At the north end of the complex.

### Materials and Color

- Structure: Roman concrete (*opus caementicium*), brick-faced.
- Exterior: brick (#B85C3A) or stucco, relatively plain.
- Interior: lavish marble revetment on all walls to ~3m height.
  - White Carrara marble: #F0F0F0.
  - Yellow Numidian (giallo antico): #D4A017.
  - Purple Phrygian (pavonazzetto): #6B3A6B with white veining.
  - Green cipollino: #4A7A5B.
  - Red porphyry: #6D1A36.
  - Grey granite (columns): #808080.
- Vault surfaces: mosaic (glass tesserae in blue #1E3A5F and gold #DAA520).
- Floors: black and white mosaic (geometric patterns, marine creatures) or polychrome opus sectile.
- Pool interiors: waterproof *opus signinum* (pink-red, #C48882) or marble-lined.

---

## 6. Amphitheater

### Source Structures
Colosseum (Flavian Amphitheater, 72-80 CE), Amphitheater at Pompeii (oldest
stone amphitheater, 70 BCE), Amphitheater at Verona, Amphitheater at Nimes,
Amphitheater at El Djem.

### Arena Shape

- Elliptical plan. The Colosseum: major axis **188m**, minor axis **156m** (ratio ~1.2:1).
- Arena floor: major axis **87m**, minor axis **55m** (ratio ~1.58:1).
- Typical arena ellipse ratio (major:minor): **1.4:1 to 1.7:1** for the arena itself.
- Overall building ellipse ratio is always closer to circular than the arena, typically **1.15:1 to 1.25:1**, because the seating (*cavea*) adds more depth on the short axis.

### Cavea (Seating Tiers)

- Seating divided into three or four tiers (*maeniana*):
  1. **Ima cavea** (lowest): ~10-12 rows, for senators and elite. Rake angle ~28-30 degrees.
  2. **Media cavea** (middle): ~16-20 rows, for equestrians and citizens. Rake angle ~30-35 degrees.
  3. **Summa cavea** (upper): ~15-20 rows, for plebeians. Rake angle ~35-40 degrees.
  4. **Maenianum summum in ligneis** (wooden top gallery, Colosseum): steepest, ~45 degrees.

- Seat depth (front to back): **0.7-0.8m** (~2.5 pedes).
- Seat height (rise per row): **0.4-0.45m** (~1.5 pedes).
- Each tier separated by a horizontal walkway (*praecinctio*): ~1.2-1.5m wide.
- Podium wall height (arena to first seats): **3.5-4.5m** (to protect spectators).

### Facade Articulation (Colosseum model)

- Four stories, each ~10.5m tall. Total height: **48.5m**.
- Lower three stories: **80 arches each**, framed by engaged columns/pilasters.
  - Story 1: Tuscan (Doric) half-columns.
  - Story 2: Ionic half-columns.
  - Story 3: Corinthian half-columns.
  - Story 4: Corinthian pilasters with small square windows (alternating).
- Arch dimensions (stories 1-3): opening **4.2m wide x 7.05m tall** (W:H roughly 3:5).
- Pier width between arches: **2.4m** (pier:arch-width ratio roughly **4:7**).
- Column diameter: ~0.9m at ground level.

### Arcade Rhythm

- Consistent bay width: ~6.5m center-to-center around the entire ellipse.
- This produces the 80-bay rhythm for an ~530m perimeter (Colosseum).
- For procedural generation: compute perimeter of ellipse, divide by ~6.5m for bay count.

### Proportional Rules for Scaling

- Arena area = roughly **30-40%** of total building footprint area.
- Cavea depth (plan) = roughly equal on all sides, but slightly deeper on the short axis.
- Height = roughly **0.25 x major axis** for monumental amphitheaters.

### Materials and Color

- Structural core: Roman concrete (*opus caementicium*) with tufa and brick.
- Facade: travertine blocks (#F5E6C8, warm cream).
- Interior arcades: tufa and brick (#B85C3A).
- Seating: marble or travertine, sometimes painted (white #F0F0F0 or grey #C0C0C0).
- *Velarium* (retractable awning) poles at crown: 240 mast sockets at Colosseum.
- Arena floor: wooden planks covered with sand (sand color: #E0D4B0).

---

## 7. Aqueduct

### Source Structures
Pont du Gard (Nimes, c. 19 BCE), Aqua Claudia (Rome), Aqua Marcia (Rome),
Aqueduct of Segovia, Aqueduct at Caesarea.

### Channel (*Specus*) Dimensions

- Channel cross-section: typically **0.6-1.0m wide x 1.0-1.8m tall** (rectangular or slightly trapezoidal, narrowing at top).
- Pont du Gard specus: 1.2m wide x 1.8m tall.
- Lined with *opus signinum* (waterproof cement, pinkish #C48882).
- Covered with stone slabs or a low vault to prevent contamination.
- Gradient: very gentle, typically **0.1-0.3%** (1-3m drop per km). Pont du Gard: 0.04% (only 17m drop over 50km).

### Single-Tier Arcade Proportions

- Arch: semicircular. Span = **3-6m** typical for single-tier aqueducts.
- Pier width = **1/3 to 1/4 of arch span** (Vitruvius recommendation and observed).
  Typical: 1.2-2.0m wide for a 4-5m arch.
- Pier depth (in the direction of water flow): same as pier width (square cross-section) or slightly more (1:1 to 1:1.2 width:depth).
- Pier height varies with terrain; arches can be from 5m to 50m+.
- Arch height (from springing to keystone) = **1/2 of arch span** (semicircular).

### Multi-Tier Arcade (Pont du Gard model, 3 tiers)

| Tier | Arch count | Arch span | Pier width | Tier height |
|------|-----------|-----------|------------|-------------|
| 1 (bottom) | 6 | ~15-24m | ~6m | ~22m |
| 2 (middle) | 11 | ~4.5m  | ~3m | ~20m |
| 3 (top)    | 35 | ~3m    | ~1.8m | ~7m  |

- Each tier steps inward slightly from the one below (by ~0.5-1m on each side).
- Bottom tier piers may incorporate triangular *cutwaters* (pointed upstream faces) for river crossings.
- Total height of Pont du Gard: ~49m.

### Proportional Rules for Scaling

- Pier width = **1/4 of arch span** is the baseline rule.
- In multi-tier bridges: upper tier arch span = roughly **1/3 to 1/2 of lower tier span**.
- Upper tier pier width = **2/3 of lower tier pier width**.
- Piers of upper tiers align with piers (or crown of arches) below.

### Materials and Color

- Structure: *opus quadratum* (large dressed stone blocks without mortar, Pont du Gard) or brick-faced concrete.
- Stone color varies by region:
  - Roman tufa: warm golden-tan (#C8B070).
  - Travertine: cream (#F5E6C8).
  - Local limestone (Pont du Gard): honey-gold (#D4A850).
  - Granite (Segovia): cool grey (#909090).
- Specus lining: *opus signinum* (pink-red #C48882).
- No applied decoration on utilitarian aqueducts; sometimes rusticated stonework.

---

## 8. Triumphal Arch / Gate

### Source Structures
Arch of Titus (81 CE), Arch of Septimius Severus (203 CE), Arch of Constantine
(315 CE), Arch of Trajan at Benevento (114 CE).

### Single-Bay Arch (Arch of Titus model)

- Overall proportions: width:height roughly **1:1.35 to 1:1.5** (taller than wide).
- Arch of Titus: 13.5m wide x 15.4m tall x 4.75m deep (W:H = 1:1.14, but this is
  unusually wide; Benevento is 1:1.5).
- Central archway: **width = ~1/2 of total width** of the monument.
  Arch of Titus: opening 5.36m wide x 8.3m tall (W:H ~1:1.55).
- Archway is semicircular: height to crown = pier height + 1/2 arch span.
  Pier height (from ground to arch springing): ~1.5 x archway width.
- Piers flanking the arch: **width = ~1/4 of total monument width** on each side.
- Engaged columns or pilasters on piers: Composite order (Arch of Titus), standing on high pedestals.
- Entablature height: **~1/4 of column height** or ~1/8 of total monument height.
- Attic (the rectangular block above the entablature, carrying the inscription): height = **roughly 1/3 of the height below the attic** (from ground to top of entablature). Arch of Titus attic: ~3.3m of 15.4m total.

### Triple-Bay Arch (Arch of Constantine model)

- Arch of Constantine: 21m wide x 25.7m tall x 7.4m deep.
- Central archway: **11.5m tall x 6.5m wide** (W:H ~1:1.77).
- Side archways: **7.4m tall x 3.36m wide** each (W:H ~1:2.2; narrower and shorter).
- Side archway height = roughly **2/3 of central archway height**.
- Side archway width = roughly **1/2 of central archway width**.
- Pier widths between arches: ~2.5-3m (housing engaged Corinthian columns).
- Outer piers: ~3-3.5m.

### Proportional Rules for Procedural Generation

For a single-bay arch scaled by total width W:
- Total height: **1.3W to 1.5W**.
- Archway opening width: **W / 2**.
- Archway opening height: **W * 0.75 to W * 0.85** (semicircular: pier height + half-span).
- Pier width (each side): **W / 4**.
- Attic height: **W * 0.25 to W * 0.35**.
- Depth: **W * 0.3 to W * 0.4**.
- Column height on piers: from top of pedestal to entablature, roughly **0.55 to 0.6 x total height** (minus attic and pedestal).

For a triple-bay arch scaled by total width W:
- Total height: **1.2W to 1.3W**.
- Central archway width: **W * 0.31**.
- Side archway width: **W * 0.16** each.
- Pier width: **W * 0.12** (between arches).
- Outer pier width: **W * 0.14**.
- Attic height: **0.2 x total height**.

### Relief and Ornament Zones

- Spandrels (triangular zones flanking the arch): typically contain winged Victories or river gods. Spandrel figures inscribed in the triangle formed by the arch extrados and the entablature.
- Attic: large inscription panel, centered. Panel width ~80% of attic width.
- Pier faces: relief panels or engaged columns with pedestals.
- Archway vault: coffered (square coffers with rosettes) or decorated with relief.
- Keystone: often a projecting carved figure or console.

### Materials and Color

- Structure: Roman concrete core with marble cladding, or solid marble blocks.
- Primary facing: Pentelic or Luna (Carrara) marble, white (#F0F0F0) weathering to warm cream (#F5E6C8).
- Columns: colored marble. Arch of Constantine reuses Numidian yellow (#D4A017) and Phrygian purple (#6B3A6B) columns from Trajan's forum.
- Bronze: letters on attic inscription originally inlaid bronze (now lost), gilt bronze quadriga (chariot group) on top.
- Relief panels: originally painted and gilded (traces suggest the same palette as temple polychromy).

---

## Appendix A: Common Roman Molding Profiles

Used across all building types in cornices, bases, and entablatures:

| Molding        | Profile shape         | Typical height |
|----------------|-----------------------|----------------|
| *Torus*        | Convex semicircle     | 0.3-0.5 D      |
| *Scotia*       | Concave curve         | 0.25 D         |
| *Ovolo*        | Convex quarter-round  | 0.15 D         |
| *Cavetto*      | Concave quarter-round | 0.15 D         |
| *Cyma recta*   | S-curve (concave top) | 0.2 D          |
| *Cyma reversa* | S-curve (convex top)  | 0.2 D          |
| *Fascia*       | Flat band             | 0.15-0.3 D     |
| *Fillet*       | Small flat band       | 0.05-0.1 D     |

Attic column base (standard for Ionic/Corinthian): upper torus + scotia + lower torus, total height = 0.5 D.

---

## Appendix B: Color Palette Summary

A consolidated hex-color reference for the procedural renderer:

### Stone and Masonry
| Material              | Hex       | Description |
|-----------------------|-----------|-------------|
| Carrara/Luna marble   | #F0F0F0   | Cool white  |
| Travertine            | #F5E6C8   | Warm cream  |
| Tufa                  | #C8B070   | Golden tan  |
| Brick (*opus test.*)  | #B85C3A   | Orange-red  |
| Basalt paving         | #3A3A3A   | Near black  |
| Stuccoed wall         | #F0EAD6   | Off-white   |
| Concrete (exposed)    | #A09880   | Grey-brown  |

### Colored Marbles
| Material              | Hex       | Description |
|-----------------------|-----------|-------------|
| Numidian yellow       | #D4A017   | Rich gold   |
| Phrygian purple       | #6B3A6B   | Deep mauve  |
| Red porphyry          | #6D1A36   | Imperial red|
| Cipollino green       | #4A7A5B   | Olive green |
| Grey granite          | #808080   | Neutral grey|
| Africano              | #5C4033   | Dark brown with veins |

### Painted Surfaces (Pompeian palette)
| Color                 | Hex       | Use case |
|-----------------------|-----------|----------|
| Pompeian Red          | #8E2323   | Wall panels, temple trim |
| Pompeian Black        | #1A1110   | Wall panels (Style III) |
| Pompeian Yellow       | #CEAC5E   | Wall panels |
| Pompeian Green        | #355E3B   | Wall panels, garden walls |
| Egyptian Blue         | #1034A6   | Ceilings, trim |
| Gold leaf             | #DAA520   | Molding highlights, coffers |

### Functional Surfaces
| Material              | Hex       | Use case |
|-----------------------|-----------|----------|
| Terracotta tile       | #C45A3C   | Roof tiles |
| Opus signinum         | #C48882   | Waterproof floors, pools |
| Sand                  | #E0D4B0   | Arena floor, unpaved ground |
| Timber (aged)         | #4A3728   | Doors, frames, balconies |
| Bronze (weathered)    | #6B8E6B   | Fittings, letters |
| Bronze (polished)     | #CD7F32   | New fittings, statuary |

---

## Appendix C: Vitruvius Quick-Reference by Book

Key chapters in *De Architectura* relevant to each building type:

| Building      | Book.Chapter | Topic |
|---------------|-------------|-------|
| Temple        | 3.1-3.5     | Symmetry, column orders, intercolumniation |
| Temple        | 4.1-4.8     | Doric/Ionic/Corinthian details, doors, altars |
| Basilica      | 5.1         | Forum and basilica proportions |
| Thermae       | 5.10        | Baths orientation, room arrangement |
| Theater       | 5.3-5.9     | Theater design (relevant to amphitheater) |
| Domus         | 6.3-6.7     | Atrium types, room proportions, Greek houses |
| Materials     | 2.1-2.10    | Brick, sand, lime, pozzolana, stone, timber |
| Orders        | 3.3, 4.1    | Tuscan, Doric, Ionic, Corinthian proportions |

Note: Vitruvius does not describe amphitheaters or triumphal arches (these
postdate or are outside his scope). Proportions for those types are derived from
surviving archaeological evidence, principally the Colosseum and Arch of Titus.

---

## Appendix D: Procedural Generation Notes

### Deriving Absolute Dimensions from Ratios

For the shape-list renderer (box, cylinder, cone, sphere, torus primitives),
start with a single seed dimension and derive everything else:

**Temple example** (Corinthian hexastyle):
1. Choose column lower diameter D (e.g., D = 1.0m for a modest temple, 1.5m for a large one).
2. Column height = 9.5 D (Corinthian).
3. Intercolumniation = 2.25 D (eustyle). Central span = 3 D.
4. Stylobate width = 5 spans + 6 D + 2 * 2 D (edge overhang) = 5(2.25D) + 6D + 4D = 21.25 D.
5. Stylobate length = 10 spans + 11 D + 4 D = 10(2.25D) + 11D + 4D = 37.5 D.
6. Podium height = column height / 5 = 1.9 D.
7. Entablature height = column height / 5 = 1.9 D.
8. Pediment rise = stylobate width / 5 = 4.25 D.
9. Cella interior width = 3 spans + 2 D = 3(2.25D) + 2D = 8.75 D.
10. Cella interior length = 2 x cella width = 17.5 D.

**Insula example**:
1. Choose story height H = 3.0m (upper floor).
2. Ground floor = 1.2 H.
3. Number of stories: 5 (total ~17.4m, within Trajanic 60-pes limit).
4. Facade width: 15m. Window rhythm: 2m center-to-center = 7 bays.
5. Window size: 0.9m wide x 1.2m tall per opening.
6. Ground floor arch rhythm: 3m arch + 0.7m pier, repeated.

### Primitive Mapping

| Architectural Element | Primitive | Notes |
|----------------------|-----------|-------|
| Column shaft         | Cylinder  | Taper upper diameter to 5/6 of lower |
| Column base (torus)  | Torus     | Major radius = D/2, tube radius = 0.15D |
| Wall section         | Box       | Width = length, height = wall height, depth = wall thickness |
| Arch                 | Series of boxes or custom curve | Approximate with ~8-12 rotated boxes forming a semicircle |
| Pediment             | Triangular prism (2 boxes angled) or cone section |
| Dome                 | Sphere (half) | Cut at equator |
| Barrel vault         | Cylinder (half) | Cut along length |
| Roof tile surface    | Box (thin, angled) | Pitched at 15-20 degrees |
| Capital (Doric)      | Cylinder (echinus) + box (abacus) |
| Capital (Corinthian) | Cylinder (bell) + box (abacus), decorated conceptually |
| Podium               | Box | Single block or stacked boxes for moldings |
| Steps                | Series of thin boxes | Stacked with offset |
