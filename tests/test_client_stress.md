# Client-Side Stress Test Procedures and Findings

## Issues Found and Fixed

### 1. Shared Material Disposal (FIXED)
**File:** `static/renderer3d.js` line 165
**Severity:** High -- causes visual corruption

**Problem:** When `_buildFromSpec` replaces a building at an existing tile, it calls
`c.material.dispose()` on the old group's meshes. However, materials are shared across
all buildings via `_matCache` (keyed by color+roughness). Disposing a shared material
corrupts the GPU resources for every other building using that same color.

**Symptoms:** Buildings randomly turn black or disappear after a tile is replaced.
Most visible when the Civis agent updates a tile with `scene` data, triggering a
re-render of the first tile in a placed set.

**Fix:** Removed `c.material.dispose()` from the traversal. Only geometries are
disposed (they are unique per building). Cached materials persist for the session
lifetime -- they are small and reused heavily.

### 2. Hover/Click Raycasting Performance (FIXED)
**File:** `static/renderer3d.js` `_updateHover()` and `_onClick()`
**Severity:** Medium -- causes jank at scale

**Problem:** On every mouse move, `_updateHover` rebuilds a flat array of ALL meshes
from ALL building groups by calling `forEach` + `traverse`. With 1600 buildings
(40x40 grid) averaging 20 shapes each, this creates a 32,000-element array 60 times
per second.

**Fix:** Added throttling (50ms minimum between hover checks) and a cached mesh list
(`_meshListCache`) that is only rebuilt when buildings change (flagged via
`_meshListDirty` in `_buildFromSpec`). Click handler reuses the same cache.

### 3. Animate Loop Iterates All Groups Every Frame (FIXED)
**File:** `static/renderer3d.js` `_animate()`
**Severity:** Medium -- causes frame drops at scale

**Problem:** The animation loop calls `forEach` on all `buildingGroups` and `traverse`
on each one every frame, even though only a few groups are actively animating (drop-in
animation) and only water tiles need per-frame updates.

**Fix:** Introduced `_animatingGroups` and `_waterGroups` Sets. Only groups with
active animations or water tiles are iterated per frame. Groups are added to these
sets in `_buildFromSpec` and removed when animation completes or the building is
replaced.

### 4. Expansion Tiles Rejected by Client (FIXED)
**File:** `static/renderer3d.js` `updateTiles()`
**Severity:** High -- blocks infinite expansion entirely

**Problem:** `updateTiles()` checks `tile.x < this.width && tile.y < this.height`
before rendering. Any tile from expansion districts beyond the initial 40x40 grid
is silently dropped. The 3D scene has no inherent boundary, but the bounds check
prevents rendering.

**Fix:** The bounds check now only gates the 2D grid array update (for backward
compatibility with the minimap). Building rendering via `_buildFromSpec` always
proceeds regardless of grid bounds.

---

## Manual Stress Test Procedures

### Test 1: Memory Growth Over Time
**Goal:** Verify geometry/material/texture memory doesn't grow unboundedly.

**Procedure:**
1. Open Chrome DevTools > Memory tab
2. Start the application and let it build through all districts
3. Take a heap snapshot after each district completes
4. Watch for:
   - Total JS heap should stabilize after initial build
   - `THREE.BufferGeometry` count should match active building count (old ones disposed)
   - `THREE.MeshStandardMaterial` count should grow slowly (one per unique color, cached)
5. After all districts, trigger a world reset
6. Take another heap snapshot -- memory should return near initial levels

**Expected:** Geometry objects are properly disposed on replacement. Material count
grows with unique colors but is bounded by the palette size (~50 colors max).

### Test 2: Large Scene Rendering
**Goal:** Test frame rate with maximum building density.

**Procedure:**
1. Let the full 40x40 grid fill (1600 tiles)
2. Open Chrome DevTools > Performance tab
3. Record 5 seconds of interaction (orbit camera, hover over buildings)
4. Check frame timing:
   - Target: 60fps on modern hardware
   - Acceptable: 30fps sustained
   - Failing: Below 20fps or frequent dropped frames

**Key metrics:**
- `requestAnimationFrame` callback time should be <16ms
- Raycasting on hover should be <5ms (with the caching fix)
- No GC pauses >50ms

### Test 3: WebSocket Message Flooding
**Goal:** Verify the client handles rapid message bursts gracefully.

**Procedure:**
1. Open the browser console
2. Simulate rapid tile updates:
```javascript
// Simulate 100 rapid tile updates
for (let i = 0; i < 100; i++) {
    handleMessage({
        type: "tile_update",
        tiles: [{
            x: i % 40, y: Math.floor(i / 40),
            terrain: "building",
            building_name: `Stress_${i}`,
            color: `#${(i * 2741 % 0xFFFFFF).toString(16).padStart(6, '0')}`,
            spec: {shapes: [{type: "box", pos: [0, 0.5, 0], size: [0.8, 1.0, 0.8]}]}
        }],
        turn: i
    });
}
```
3. All 100 buildings should appear without errors or frame drops
4. Memory should not spike dramatically

### Test 4: Camera at Extreme Distances
**Goal:** Test for floating-point precision issues at the coordinate boundary.

**Procedure:**
1. Using browser console, move camera to extreme coordinates:
```javascript
renderer.cameraTarget.set(10000, 0, 10000);
renderer.cameraDistance = 100;
renderer._updateCamera();
```
2. Check for:
   - Z-fighting artifacts (flickering surfaces)
   - Camera jitter when orbiting
   - Correct rendering of buildings placed at large coordinates

**Expected:** THREE.js uses Float32 internally. At coordinates >10000, precision
drops to ~0.001 units, which may cause visible z-fighting. The camera near/far
planes (0.1 to 200) should be adjusted if expansion reaches these scales.

### Test 5: Reconnection Replay
**Goal:** Verify that reconnecting clients get a consistent state without overload.

**Procedure:**
1. Let the server build 5+ districts
2. Open a new browser tab connecting to the same server
3. Measure:
   - Time to receive and render initial `world_state`
   - Time to replay all chat history
   - Total data transferred over WebSocket
4. With the chat_history cap fix (500 max, pruned to 300), replay should complete
   in <2 seconds

### Test 6: Building Replacement Stress
**Goal:** Verify that rapidly replacing buildings at the same tile doesn't leak geometry.

**Procedure:**
```javascript
// Replace the same tile 1000 times rapidly
const tile = {x: 20, y: 20, terrain: "building", building_name: "Test"};
for (let i = 0; i < 1000; i++) {
    tile.spec = {shapes: [
        {type: "box", pos: [0, 0.5, 0], size: [0.8, Math.random() + 0.5, 0.8],
         color: `#${(i * 12345 % 0xFFFFFF).toString(16).padStart(6, '0')}`}
    ]};
    renderer._buildFromSpec(tile, false);
}
```
After running, check:
- `renderer.buildingGroups.size` should be stable (still the same number as before)
- No WebGL context loss warnings
- Memory tab should show disposed BufferGeometry objects being collected

---

## Known Limitations Not Yet Fixed

### Fixed Grid Size (WorldState)
The `WorldState` uses a fixed `width x height` 2D array. For true infinite expansion,
this needs to be replaced with a sparse dict-based storage (see
`TestSparseWorldState` in `test_infinite_expansion.py` for the validated prototype).
The current bounds check in `place_tile` silently drops any tile outside the grid.

### No Level-of-Detail (LOD)
All buildings render at full detail regardless of distance from camera. For 1000+
buildings, implementing LOD (replacing distant buildings with simple boxes or
removing them entirely) would significantly improve performance.

### No Tile Unloading
There is no mechanism to unload distant tiles from the Three.js scene. For infinite
expansion, a viewport-based culling system should add/remove building groups based
on camera distance to their position.

### Ground Plane is Fixed Size
The ground mesh is created once in `init()` at the initial grid dimensions. Expansion
tiles beyond the grid would float over void. The ground would need to grow
dynamically or be replaced with chunked terrain tiles.

### WebSocket Full State Size
`world.to_dict()` serializes every cell in the grid (including empty ones) as a
nested 2D array. For a 40x40 grid this is ~1600 cells. For infinite expansion, the
initial state message needs to send only non-empty tiles, not the full grid.
