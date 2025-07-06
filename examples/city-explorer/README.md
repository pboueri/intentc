# City Explorer - IntentC Example Project

A procedurally generated 3D city explorer built with React, Three.js, and IntentC. This example demonstrates all features of IntentC including dependencies, validations, and iterative refinement.

## Overview

City Explorer creates infinite, explorable cities with multiple architectural styles, dynamic lighting, and smooth navigation. It showcases:

- Complex feature dependencies
- Multi-target builds
- Comprehensive validations
- Iterative refinement workflow
- Performance optimization

## Building with IntentC

### Initial Setup

```bash
# Initialize IntentC project
intentc init

# View project structure
intentc status
```

### Building the Application

```bash
# Build all targets
intentc build all

# Or build specific features
intentc build app-setup
intentc build city-generator
intentc build navigation-controls
```

### Validation

```bash
# Run all validations
intentc validate all

# Run specific validation suites
intentc validate core
intentc validate performance
intentc validate features
```

### Running the Application

After successful build:

```bash
cd generated
npm install
npm run dev
```

Open http://localhost:5173 to explore the city!

## Features

### 🏙️ Procedural City Generation
- **Manhattan Style**: Grid-based layout with skyscrapers
- **European Style**: Organic streets with historic centers
- **Modern Style**: Planned districts with green spaces
- **Suburban Style**: Residential neighborhoods

### 🎮 Navigation Modes
- **Orbit Controls**: Rotate around points of interest
- **First Person**: Walk through streets with WASD
- **Fly Mode**: Free movement through the city
- **Mobile Support**: Touch gestures and virtual joystick

### 💡 Dynamic Lighting
- Day/night cycle with realistic sun movement
- Weather effects (clear, cloudy, rain, fog)
- Street lights and building illumination
- Atmospheric rendering with volumetric fog

### 🎨 User Interface
- Responsive design for all devices
- Real-time minimap
- Performance statistics
- City generation controls
- Settings and preferences

## Refinement Examples

IntentC's refinement feature allows iterative improvements:

### Adding Weather Effects

```bash
intentc refine lighting-system
```

In the REPL:
```
> show src/three/Weather.tsx
> refine Add rain particle effects with realistic physics
> validate
> refine Make rain interact with camera for immersion
> commit
```

### Implementing Day/Night Cycle

```bash
intentc refine lighting-system
```

```
> refine Add automatic day/night cycle with configurable speed
> show src/three/Lighting/TimeOfDay.ts
> refine Add sunrise/sunset color transitions
> validate
> commit
```

### Adding Pedestrians

```bash
intentc refine city-generator
```

```
> refine Add animated pedestrians walking on sidewalks
> refine Use instancing for performance with 100+ pedestrians
> validate performance
> commit
```

### Performance Optimization

```bash
intentc refine building-system
```

```
> show src/three/Objects/Buildings.tsx
> refine Implement aggressive LOD with impostor sprites
> refine Add occlusion culling for buildings
> validate performance
> diff
> commit
```

## Project Structure

```
intent/
├── project/             # Project-level setup
│   ├── setup.ic
│   └── setup.icv
└── features/            # Feature-specific intents
    ├── city-generation/
    │   ├── city-generation.ic
    │   └── city-generation.icv
    ├── navigation/
    │   ├── navigation.ic
    │   └── navigation.icv
    ├── buildings/
    │   ├── buildings.ic
    │   └── buildings.icv
    ├── lighting/
    │   ├── lighting.ic
    │   └── lighting.icv
    └── ui-controls/
        ├── ui-controls.ic
        └── ui-controls.icv
```

Each feature is self-contained with its intent and validation colocated, making it easy to understand and modify individual features.

## Performance Tips

1. **Chunk Loading**: The city loads in chunks as you explore
2. **LOD System**: Buildings simplify with distance
3. **Instancing**: Repeated elements share geometry
4. **Culling**: Only visible objects are rendered
5. **Quality Settings**: Adjust for your hardware

## Extending the Project

### Adding New City Styles

1. Create new intent:
```markdown
# Feature: Desert City Style

## Dependencies
- city-generation

## Target: desert-style
...
```

2. Build and refine:
```bash
intentc build desert-style
intentc refine desert-style
```

### Adding Transportation

1. Add to existing intent or create new one
2. Define vehicle types and routes
3. Implement with proper LOD
4. Validate performance impact

## Troubleshooting

### Build Failures
- Ensure git working tree is clean
- Check Claude CLI is authenticated
- Verify all dependencies in intents

### Performance Issues
- Lower quality settings
- Reduce chunk size
- Enable aggressive LOD
- Check browser console for warnings

### Validation Failures
- Read detailed error messages
- Use `intentc refine` to fix issues
- Re-run specific validations

## Learning Resources

- Study intent files for best practices
- Examine validation rules for quality standards
- Use refinement for exploratory development
- Check `.intentc/generations/` for history

## Contributing

This example is designed to showcase IntentC capabilities. Feel free to:
- Add new features via intents
- Improve validations
- Optimize performance
- Share refinement workflows

---

Built with ❤️ using IntentC - The Compiler of Intent