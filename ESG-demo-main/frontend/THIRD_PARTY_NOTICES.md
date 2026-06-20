# Third-party assets & commercial use

This document summarizes image/icon licensing for the Euler ESG frontend.
Last reviewed: 2026-06.

## Project-owned assets (confirm internal rights)

| Asset | Location | Notes |
|-------|----------|--------|
| Euler icon + wordmark | `public/Euler-Img.svg`, `src/assets/Euler-Img.svg` | Custom project branding. Safe for commercial use **only if your team owns the trademark/design** or has written permission. |
| Euler wordmark only | `src/assets/Euler.svg` | Same as above. |

## UI icons (libraries — commercial use allowed)

| Library | License | Used for |
|---------|---------|----------|
| [lucide-react](https://lucide.dev) | ISC | Landing page, PDF viewer, cross-analysis UI |
| [@ant-design/icons](https://github.com/ant-design/ant-design-icons) | MIT | Dashboard, upload, chat, status |
| [react-icons](https://react-icons.github.io/react-icons/) (Material Design subset) | MIT (icons: Apache 2.0) | Nav user menu (`MdPerson`, `MdSettings`, `MdLogout`) |
| [@radix-ui](https://www.radix-ui.com) primitives | MIT | Buttons, dialogs, dropdowns (no bundled artwork) |

## Fonts

| Font | License | Notes |
|------|---------|--------|
| System UI stack + `Inter` fallback in CSS | Inter: [SIL Open Font License 1.1](https://scripts.sil.org/OFL) | Commercial use permitted. Consider self-hosting Inter via `next/font` for production. |

## Charts & PDF

| Package | License |
|---------|---------|
| `@ant-design/plots` / G2 | MIT |
| `pdfjs-dist` / `react-pdf` | Apache 2.0 |

## Stock photography

| Asset | Location | License |
|-------|----------|---------|
| Hands holding plant (Noah Buscher) | `public/hero-esg.jpg` | [Unsplash License](https://unsplash.com/license) — free for commercial use |

## Landing page visuals

The marketing hero uses the stock photo above inside a rounded frame. All other landing sections use CSS and open-source icons only.

## Removed defaults

Unused `create-next-app` files (`vercel.svg`, `file.svg`, `window.svg`) were removed to avoid accidental use of third-party branding.

## Action items for production

1. Confirm **Euler** logo/wordmark ownership with your organization before public/commercial launch.
2. Keep this file updated when adding new image packs, fonts, or icon sets.
3. Do not add Unsplash/Pexels/figma community assets without checking their license terms.
