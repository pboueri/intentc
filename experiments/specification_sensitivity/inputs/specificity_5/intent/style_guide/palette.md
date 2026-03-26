# Zoo Builder Color Palette

Complete color reference for the Zoo Builder game. All colors listed as hex values with their CSS custom property names.

## Ground Colors

| Name | Hex | CSS Variable | Usage |
|------|-----|-------------|-------|
| Grass Primary | `#4a8c3f` | `--color-grass` | Default ground tile fill |
| Grass Alt | `#5a9e4f` | `--color-grass-alt` | Alternating grass tile (checkerboard pattern) |
| Grass Dark | `#3a7a2f` | `--color-grass-dark` | Grass tile under enclosure shadow |
| Dirt Path | `#c4a265` | `--color-dirt` | Unpaved walkway tiles |
| Stone Path | `#9e9e9e` | `--color-path` | Paved visitor walkways |
| Stone Path Light | `#b0b0b0` | `--color-path-light` | Path tile highlight edge |
| Water | `#3d85c6` | `--color-water` | Pond and water feature tiles |
| Water Deep | `#2a6aa0` | `--color-water-deep` | Center of large water bodies |
| Water Edge | `#6aafe0` | `--color-water-edge` | Shore/transition tiles |
| Entrance Gate | `#8b6914` | `--color-entrance` | Gate structure fill |

## Biome Accent Colors

| Biome | Fill | Border | Highlight | CSS Prefix |
|-------|------|--------|-----------|------------|
| Savanna | `#c8b060` | `#a08830` | `#e0d080` | `--biome-savanna-*` |
| Aquatic | `#5ba0d0` | `#3080b0` | `#80c0e8` | `--biome-aquatic-*` |
| Forest | `#3a7a3a` | `#2a5a2a` | `#5a9a5a` | `--biome-forest-*` |
| Arctic | `#e0e8f0` | `#b0c0d0` | `#f0f4fa` | `--biome-arctic-*` |
| Aviary | `#f0e8a0` | `#d0c870` | `#f8f0c0` | `--biome-aviary-*` |

## UI Colors

| Name | Hex | CSS Variable | Usage |
|------|-----|-------------|-------|
| HUD Background | `#1a1a2ecc` | `--color-hud-bg` | Top bar background (80% opacity) |
| Sidebar Background | `#1a1a2eee` | `--color-sidebar-bg` | Right panel background (93% opacity) |
| Panel Background | `#252540` | `--color-panel-bg` | Nested panels within sidebar |
| Text Primary | `#ffffff` | `--color-text` | Main UI text |
| Text Secondary | `#b0b0cc` | `--color-text-secondary` | Descriptions, labels |
| Text Muted | `#707090` | `--color-text-muted` | Disabled text, timestamps |
| Button Normal | `#3a3a5c` | `--color-btn` | Default button fill |
| Button Hover | `#5a5a8c` | `--color-btn-hover` | Mouse-over button fill |
| Button Active | `#6a6a9c` | `--color-btn-active` | Pressed button fill |
| Button Disabled | `#2a2a3c` | `--color-btn-disabled` | Grayed-out button fill |
| Button Text | `#ffffff` | `--color-btn-text` | Button label color |
| Button Border | `#5050780` | `--color-btn-border` | 1px button outline |
| Tab Active | `#5a5a8c` | `--color-tab-active` | Selected tab indicator |
| Tab Inactive | `#3a3a5c` | `--color-tab-inactive` | Unselected tab background |
| Tooltip BG | `#1a1a2ef0` | `--color-tooltip-bg` | Tooltip background (94% opacity) |
| Tooltip Border | `#5050780` | `--color-tooltip-border` | Tooltip 1px outline |
| Toast BG | `#1a1a2ee0` | `--color-toast-bg` | Notification toast background |

## Status Colors

| Name | Hex | CSS Variable | Usage |
|------|-----|-------------|-------|
| Health Full | `#5cb85c` | `--color-health-full` | 100% health bar |
| Health High | `#8cc63f` | `--color-health-high` | 75%+ health |
| Health Mid | `#f0ad4e` | `--color-health-mid` | 50% health |
| Health Low | `#e87530` | `--color-health-low` | 25% health |
| Health Critical | `#d9534f` | `--color-health-critical` | <10% health, pulsing |
| Happiness High | `#5cb85c` | `--color-happy-high` | 80+ happiness |
| Happiness Mid | `#f0ad4e` | `--color-happy-mid` | 40-79 happiness |
| Happiness Low | `#d9534f` | `--color-happy-low` | <40 happiness |
| Hunger Satisfied | `#5cb85c` | `--color-hunger-ok` | Hunger < 30 |
| Hunger Warning | `#f0ad4e` | `--color-hunger-warn` | Hunger 30-70 |
| Hunger Starving | `#d9534f` | `--color-hunger-danger` | Hunger > 70 |
| Money Positive | `#ffd700` | `--color-money` | Positive income |
| Money Negative | `#d9534f` | `--color-money-negative` | Negative net income |
| Positive Accent | `#5cb85c` | `--color-positive` | Generic success/good |
| Warning Accent | `#f0ad4e` | `--color-warning` | Generic caution |
| Danger Accent | `#d9534f` | `--color-danger` | Generic error/bad |

## Health Bar Gradient Function

```js
function healthBarColor(healthPercent) {
  if (healthPercent >= 75) return '#5cb85c';
  if (healthPercent >= 50) return '#8cc63f';
  if (healthPercent >= 25) return '#f0ad4e';
  if (healthPercent >= 10) return '#e87530';
  return '#d9534f';
}
```

## Colorblind Safety Notes

- Red/green pairs are never used as the sole differentiator. All status bars include a numeric percentage label beside the color.
- The primary contrast pair is **blue (#3d85c6) vs orange (#f0ad4e)** for accessible distinction.
- Enclosure biomes use distinct lightness levels in addition to hue: savanna is mid-light, aquatic is mid, forest is dark, arctic is very light, aviary is light-warm.
- Visitor satisfaction icons use shape (smile/neutral/frown) in addition to color.
