---
name: Obsidian Protocol
colors:
  surface: '#131313'
  surface-dim: '#131313'
  surface-bright: '#393939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1b1b1b'
  surface-container: '#1f1f1f'
  surface-container-high: '#2a2a2a'
  surface-container-highest: '#353535'
  on-surface: '#e2e2e2'
  on-surface-variant: '#c4c7c8'
  inverse-surface: '#e2e2e2'
  inverse-on-surface: '#303030'
  outline: '#8e9192'
  outline-variant: '#444748'
  surface-tint: '#c6c6c7'
  primary: '#ffffff'
  on-primary: '#2f3131'
  primary-container: '#e2e2e2'
  on-primary-container: '#636565'
  inverse-primary: '#5d5f5f'
  secondary: '#c6c6cf'
  on-secondary: '#2f3037'
  secondary-container: '#45464e'
  on-secondary-container: '#b4b4bd'
  tertiary: '#ffffff'
  on-tertiary: '#303037'
  tertiary-container: '#e3e1ea'
  on-tertiary-container: '#64646b'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e2e2e2'
  primary-fixed-dim: '#c6c6c7'
  on-primary-fixed: '#1a1c1c'
  on-primary-fixed-variant: '#454747'
  secondary-fixed: '#e2e1eb'
  secondary-fixed-dim: '#c6c6cf'
  on-secondary-fixed: '#1a1b22'
  on-secondary-fixed-variant: '#45464e'
  tertiary-fixed: '#e3e1ea'
  tertiary-fixed-dim: '#c7c5ce'
  on-tertiary-fixed: '#1b1b21'
  on-tertiary-fixed-variant: '#46464d'
  background: '#131313'
  on-background: '#e2e2e2'
  surface-variant: '#353535'
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0em
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  code-lg:
    fontFamily: JetBrains Mono
    fontSize: 16px
    fontWeight: '500'
    lineHeight: 24px
    letterSpacing: 0em
  code-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
    letterSpacing: 0.05em
  label-caps:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '700'
    lineHeight: 12px
    letterSpacing: 0.1em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  gutter: 16px
  margin-mobile: 16px
  margin-desktop: 32px
  container-max: 1440px
---

## Brand & Style

This design system embodies a **Neo-Noir Tech** aesthetic—a fusion of high-end cyberpunk and minimalist developer tooling. The personality is precise, premium, and clandestine. It is designed for power users who value technical density presented through a sophisticated, cinematic lens.

The visual language utilizes **Dark-Mode Glassmorphism** and **Technical Brutalism**. Interfaces should feel like light projected onto obsidian glass. Every element is intentional, stripping away unnecessary decoration in favor of structural integrity, background blurs, and monochromatic luminosity. The emotional response is one of calm authority and deep focus.

## Colors

The palette is strictly monochromatic, relying on luminosity and transparency rather than hue to establish hierarchy.

- **Base Surface:** Pure `#000000` black. This provides the "void" from which the UI emerges.
- **Glass Panels:** `#1A1A1A` with an opacity range of 40% to 70%. These must utilize `backdrop-filter: blur(12px)` to create a sense of physical depth.
- **Accents:** Pure `#FFFFFF` white is reserved for high-priority actions, critical data, and active states.
- **Grayscale Scale:** Silver grays (`#A1A1AA`) and deep charcoals (`#3F3F46`) manage secondary information and inactive states.
- **Glow Effects:** Active elements utilize a white outer glow (`box-shadow: 0 0 10px rgba(255, 255, 255, 0.3)`) to simulate light emission.

## Typography

The typography strategy contrasts the approachability of **Inter** with the technical rigor of **JetBrains Mono**. 

- **UI & Content:** Use Inter for all primary reading experiences and navigation. It provides clarity in a low-light environment.
- **Technical Data:** Use JetBrains Mono for data points, status indicators, code blocks, and metadata.
- **Caps Labels:** Small, uppercase monospaced labels should be used for section headers and "micro-copy" to reinforce the developer-tool aesthetic.
- **Contrast:** Maintain high contrast for body text (White at 90% opacity) and lower contrast for secondary labels (Silver gray at 60% opacity).

## Layout & Spacing

This design system uses a **4px baseline grid** to ensure mathematical precision. 

- **Grid Model:** A 12-column fluid grid for desktop, transitioning to a 4-column grid for mobile.
- **Glass Containers:** Content is grouped into semi-transparent panels. These panels should have consistent padding (typically 24px) to allow the background blur to be visible behind the content.
- **Negative Space:** Use generous margins between panels to emphasize the "black void" of the base surface. 
- **Borders:** Panels are separated by 1px solid borders. Use `rgba(255, 255, 255, 0.1)` for standard containers and `rgba(255, 255, 255, 0.4)` for highlighted sections.

## Elevation & Depth

Elevation is not conveyed through traditional drop shadows but through **optical transparency and light occlusion**.

1.  **Level 0 (Base):** Pure black `#000000`.
2.  **Level 1 (Surface):** Glass panels with `backdrop-filter: blur(12px)` and a subtle 1px border.
3.  **Level 2 (Interaction):** Hovered or active states gain a "inner glow" or a slight increase in background opacity (up to 80%).
4.  **Level 3 (Overlay):** Modals and dropdowns feature a thicker 2px border and a subtle white outer glow to appear as if they are floating closer to the user.

**Mesh Gradients:** Subtle, low-opacity grayscale mesh gradients may be used behind glass panels to create "light leaks" that give the dark interface a cinematic feel.

## Shapes

The shape language is strictly geometric and architectural. 

- **Corner Radius:** Standard elements use a **4px** radius (`rounded-lg`). Very small elements like tags or checkboxes use a **2px** radius.
- **Rectilinearity:** Avoid circles or soft organic shapes. Even buttons should feel like precisely machined blocks.
- **Dividers:** Use 1px lines with varying opacity to create hierarchy without adding visual bulk.

## Components

- **Buttons:** 
  - *Primary:* Solid white background with black text. No border. Sharp corners.
  - *Ghost:* Transparent background, 1px white border (30% opacity). On hover, border becomes 100% white with a subtle glow.
- **Input Fields:** 
  - Dark glass background (`rgba(255, 255, 255, 0.05)`). 1px bottom-border only or a full 1px thin border. Focus state triggers a white glowing border.
- **Chips/Tags:** 
  - Monospaced font (JetBrains Mono). Rectangular with 2px corners. Light gray background (10% opacity).
- **Cards/Panels:** 
  - The core of the UI. Always feature a backdrop blur. Borders must be 1px and semi-transparent.
- **Checkboxes:** 
  - Square, 2px radius. When checked, the fill is white with a "cross" (X) or a sharp checkmark.
- **Status Indicators:** 
  - Use "Glow Dots." A small circle with a heavy outer blur to indicate "Active," "System Error," or "Ready."
- **Data Tables:** 
  - Minimalist. No vertical lines. Horizontal lines should be `1px` at 5% white opacity. Use monospaced fonts for all numeric data.