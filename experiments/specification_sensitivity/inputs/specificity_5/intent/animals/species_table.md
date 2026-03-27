# Zoo Builder Species Table

Complete reference for all 10 animal species. All numeric values are used directly in game logic.

| Species | Biome | Cost ($) | Food/Tick ($) | Space Required | Base Happiness | Breed Cooldown (ticks) | Visitor Appeal | Color | Icon |
|---------|-------|----------|---------------|----------------|----------------|----------------------|----------------|-------|------|
| Lion | Savanna | 800 | 0.8 | 2 | 70 | 60 | 1.5x | `#d4a017` | L |
| Elephant | Savanna | 1200 | 1.2 | 3 | 65 | 80 | 1.8x | `#808080` | E |
| Penguin | Arctic | 400 | 0.3 | 1 | 75 | 40 | 1.2x | `#1a1a2e` | P |
| Monkey | Forest | 500 | 0.4 | 1 | 80 | 45 | 1.3x | `#8b4513` | M |
| Giraffe | Savanna | 1000 | 0.9 | 2 | 60 | 70 | 1.6x | `#daa520` | G |
| Polar Bear | Arctic | 1100 | 1.0 | 3 | 55 | 90 | 1.7x | `#f0f0ff` | B |
| Crocodile | Aquatic | 700 | 0.6 | 2 | 65 | 55 | 1.4x | `#2e8b57` | C |
| Parrot | Aviary | 300 | 0.2 | 1 | 85 | 35 | 1.0x | `#ff4500` | R |
| Dolphin | Aquatic | 1500 | 1.1 | 3 | 70 | 75 | 2.0x | `#4682b4` | D |
| Eagle | Aviary | 600 | 0.5 | 2 | 60 | 50 | 1.3x | `#654321` | A |

## Column Definitions

- **Cost**: One-time purchase price deducted from the player's balance.
- **Food/Tick**: Amount deducted from balance each simulation tick to feed the animal. If the balance cannot cover this, the animal goes unfed.
- **Space Required**: Number of capacity units the animal occupies in its enclosure. A level-1 enclosure has capacity based on its size; each animal uses this many units.
- **Base Happiness**: Starting happiness value (0-100) when the animal is first purchased or born. Actual happiness is modified by biome match, crowding, hunger, and enclosure condition.
- **Breed Cooldown**: Number of ticks after a successful breeding event before the animal can breed again.
- **Visitor Appeal**: Multiplier on visitor satisfaction when viewing this species. Higher values mean visitors gain satisfaction faster when watching this animal.
- **Color**: Hex color used for the procedural rendering circle.
- **Icon**: Single character displayed on the animal circle.

## Biome Distribution

| Biome | Species | Count |
|-------|---------|-------|
| Savanna | Lion, Elephant, Giraffe | 3 |
| Arctic | Penguin, Polar Bear | 2 |
| Forest | Monkey | 1 |
| Aquatic | Crocodile, Dolphin | 2 |
| Aviary | Parrot, Eagle | 2 |

## Balancing Notes

- **Cheapest species (Parrot, $300)**: Low cost, low food, high base happiness, fast breed — ideal starter animal.
- **Most expensive (Dolphin, $1500)**: Highest visitor appeal (2.0x) but expensive to feed. Late-game investment.
- **Fastest breeder (Parrot, 35 ticks)**: Combined with low space requirement, parrots can multiply quickly in a small aviary.
- **Slowest breeder (Polar Bear, 90 ticks)**: High visitor appeal offsets the slow reproduction rate.
- **Highest food cost (Elephant, $1.2/tick)**: Requires strong economy to sustain multiple elephants.
