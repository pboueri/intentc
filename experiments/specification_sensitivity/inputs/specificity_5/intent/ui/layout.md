# Zoo Builder UI Layout Specification

Canvas dimensions: 1280 x 720 pixels.

## Master Layout (ASCII)

```
+=========================[1280px]============================+
|  HUD Bar: (0,0) -> (1280,40)                 height: 40px  |
|  $Money (net) | Animals: N | Visitors: N | 0:00 | [1x][2x][3x] [PAUSE] |
+==[1040px]================+======[240px]======================+
|                          |  Sidebar: (1040,40) -> (1280,720) |
|  Main Viewport           |  +--[Tab Bar: 44->76]----------+  |
|  (0,40) -> (1040,680)    |  | [Build] [Shop] [Research]   |  |
|                          |  +--[Content: 80->716]----------+  |
|  Zoo Map (scrollable)    |  |  Button 1: Savanna  $500     |  |
|  Camera: smooth scroll   |  |  Button 2: Aquatic  $600     |  |
|  Map: 40x30 tiles        |  |  Button 3: Forest   $450     |  |
|  Tile: 32x32 px          |  |  Button 4: Arctic   $700     |  |
|                          |  |  Button 5: Aviary   $400     |  |
|                          |  |  ...                         |  |
|                          |  +------------------------------+  |
|  [Mini-map: 80x60]       |                                   |
|  (bottom-right of VP)    |                                   |
+==========================+===================================+
|  Info Bar: (0,680) -> (1040,720)             height: 40px   |
|  Selected: Lion "Simba" | Happiness: 85% | Hunger: 20% | Health: 95% |
+=============================================================+
```

## Exact Pixel Coordinates

### HUD Bar
| Element | X | Y | Width | Height |
|---------|---|---|-------|--------|
| HUD background | 0 | 0 | 1280 | 40 |
| Money label | 16 | 12 | auto | 16 |
| Net income | 180 | 14 | auto | 14 |
| Animals count | 340 | 12 | auto | 16 |
| Visitors count | 500 | 12 | auto | 16 |
| Time display | 660 | 12 | auto | 16 |
| Speed btn 1x | 780 | 6 | 40 | 28 |
| Speed btn 2x | 824 | 6 | 40 | 28 |
| Speed btn 3x | 868 | 6 | 40 | 28 |
| Pause button | 930 | 6 | 60 | 28 |
| Bankruptcy warn | right-aligned at 1264 | 14 | auto | 14 |

### Sidebar
| Element | X | Y | Width | Height |
|---------|---|---|-------|--------|
| Sidebar background | 1040 | 40 | 240 | 680 |
| Tab: Build | 1043 | 44 | 78 | 32 |
| Tab: Shop | 1124 | 44 | 78 | 32 |
| Tab: Research | 1205 | 44 | 72 | 32 |
| Content area | 1044 | 80 | 232 | 636 |
| Content title | 1052 | 88 | auto | 16 |
| Button 1 | 1048 | 116 | 220 | 36 |
| Button 2 | 1048 | 156 | 220 | 36 |
| Button 3 | 1048 | 196 | 220 | 36 |
| Button 4 | 1048 | 236 | 220 | 36 |
| Button 5 | 1048 | 276 | 220 | 36 |

Button layout per row:
```
+--[220px]---------------------------+
| [24x24 swatch] [Label]   [$cost]  |
+------------------------------------+
  10px pad       42px from left
```

### Main Viewport
| Element | X | Y | Width | Height |
|---------|---|---|-------|--------|
| Viewport area | 0 | 40 | 1040 | 640 |
| Mini-map | 950 | 650 | 80 | 60 |
| Mini-map border | 948 | 648 | 84 | 64 |

### Info Bar
| Element | X | Y | Width | Height |
|---------|---|---|-------|--------|
| Info background | 0 | 680 | 1040 | 40 |
| Entity name | 16 | 692 | auto | 14 |
| Happiness bar | 250 | 688 | 60 | 12 |
| Happiness text | 320 | 692 | auto | 14 |
| Hunger text | 480 | 692 | auto | 14 |
| Health bar | 620 | 688 | 60 | 12 |
| Health text | 690 | 692 | auto | 14 |
| Sick indicator | 840 | 692 | auto | 14 |

### Tooltip
| Property | Value |
|----------|-------|
| Max width | 200px |
| Padding | 8px all sides |
| Corner radius | 4px |
| Background | `#1a1a2ef0` |
| Border | 1px `#505078` |
| Title font | bold 14px |
| Body font | 12px |
| Line height | 16px |
| Position | Right of hovered tile + 4px offset |

### Toast Notification
| Property | Value |
|----------|-------|
| Width | 280px |
| Height | 36px |
| X position | 380 (centered in viewport) |
| Initial Y | -36 (above screen) |
| Target Y | 50 (below HUD) |
| Slide-in | 200ms ease-out cubic |
| Hold time | 3000ms |
| Fade-out | 300ms linear opacity |
| Stack spacing | 40px (36 + 4 gap) |
| Max visible | 5 toasts |
| Background | `#1a1a2ee0` |
| Text | bold 14px, white, centered |

### Menu Screen
```
+=====================[1280x720]======================+
|                                                      |
|                                                      |
|              ZOO BUILDER                             |
|              (32px bold, centered at 640, 200)       |
|                                                      |
|              [  New Game  ]                          |
|              (200x48 btn, centered at 640, 340)      |
|                                                      |
|              [ Load Game  ]                          |
|              (200x48 btn, centered at 640, 400)      |
|                                                      |
|              [  Credits   ]                          |
|              (200x48 btn, centered at 640, 460)      |
|                                                      |
+======================================================+
```

### Pause Overlay
```
+=====================[1280x720]======================+
|  [entire screen dimmed 50% with #00000080]           |
|                                                      |
|              PAUSED                                  |
|              (32px bold, centered at 640, 240)       |
|                                                      |
|              [  Resume  ]  (200x48 at 640, 340)      |
|              [   Save   ]  (200x48 at 640, 400)      |
|              [ Settings ]  (200x48 at 640, 460)      |
|              [   Quit   ]  (200x48 at 640, 520)      |
|                                                      |
+======================================================+
```

### Game Over / Score Screen
```
+=====================[1280x720]======================+
|  [entire screen dimmed 70% with #000000b3]           |
|                                                      |
|              GAME OVER                               |
|              (32px bold red, centered at 640, 160)   |
|                                                      |
|              Animals: 47     (28px gold)             |
|              Peak: 52        (16px secondary)        |
|              Visitors: 1,203 (16px secondary)        |
|              Revenue: $45,678(16px secondary)        |
|              Time: 12:34     (16px secondary)        |
|              (all centered, starting at y=240)       |
|                                                      |
|              [ Restart ]  (200x48 at 640, 500)       |
|              [  Menu   ]  (200x48 at 640, 560)       |
|                                                      |
+======================================================+
```
