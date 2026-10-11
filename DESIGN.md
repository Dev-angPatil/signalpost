# DESIGN.md — Nordic Corporate Intelligence Design System

Design tokens and UI specifications for the Signalpost autonomous corporate research dossiers and interactive HTML showcase.

---

## 1. Design Philosophy: Quiet Confidence & Data-Dense Clarity

- **Nordic Precision:** Crisp, high-contrast palette grounded in deep fjord navy, cool slate neutrals, and stark white surfaces.
- **Data-Dense Elegance:** Engineered for financial, legal, and cryptographic diligence where every figure, date, and SHA-256 hash requires unambiguous scannability.
- **Border-First Architecture:** Clean structural separation using subtle `1px` borders (`#E2E8F0`) rather than heavy drop shadows.
- **Anti-Clutter:** Sleek, minimalist interface with zero decorative noise. Focus exclusively on verified corporate truth, statutory financial filings, and evidence lineage.

---

## 2. Color Palette & Tokens

### 2.1 Brand & Canvas Tokens
| Token | Hex | Role | Usage |
| :--- | :--- | :--- | :--- |
| `--color-primary` | `#0052FF` | Core Brand / Interactive | Active links, primary buttons, focus rings |
| `--color-primary-hover` | `#003ECB` | Interactive Hover | Button hover, pressed states |
| `--color-fjord-deep` | `#0A2540` | Deep Fjord Navy | Header backgrounds, primary titles, hero callouts |
| `--color-surface` | `#FFFFFF` | Card & Panel Surface | Company cards, modal dialogues, data containers |
| `--color-canvas` | `#F8FAFC` | Page Canvas | Background well behind card grids |
| `--color-border` | `#E2E8F0` | Structural Dividers | Card outlines, table cell borders, section lines |
| `--color-border-subtle` | `#EDF2F7` | Subtle Dividers | Inner row separators, secondary borders |

### 2.2 Text Tokens
| Token | Hex | Role | Usage |
| :--- | :--- | :--- | :--- |
| `--color-text-primary` | `#0F172A` | High Contrast | Legal names, section headings, primary metrics |
| `--color-text-secondary` | `#475569` | Mid Contrast | Role descriptions, addresses, financial labels |
| `--color-text-tertiary` | `#94A3B8` | Subtle Metadata | Timestamps, SHA-256 hashes, auxiliary hints |

### 2.3 Semantic & Availability Status Badges
| Token | Hex Background | Hex Foreground | Status Meaning |
| :--- | :--- | :--- | :--- |
| `--badge-available` | `#ECFDF5` | `#047857` | Verified fact anchored in official registries or corroborated web |
| `--badge-not-available` | `#F1F5F9` | `#64748B` | Honest absence (unfiled / missing; never synthesized as zero) |
| `--badge-ambiguous` | `#FFFBEB` | `#B45309` | Uncorroborated domain quarantined by identity gate |
| `--badge-blocked` | `#FEF2F2` | `#B91C1C` | Disallowed crawl, rate-limited, or failed identity verification |
| `--badge-bankrupt` | `#FEE2E2` | `#991B1B` | Adverse legal status (*konkurs* / liquidation) |

---

## 3. Typography Hierarchy

- **UI & Display Font:** `Inter`, `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, `sans-serif` (clean, geometric legibility).
- **Tabular Data & Hash Font:** `JetBrains Mono`, `ui-monospace`, `SFMono-Regular`, `Menlo`, `monospace` with `font-feature-settings: "tnum"` enabled for exact vertical alignment of financial figures, 9-digit org numbers, and 64-character SHA-256 hashes.

### Type Scale
- **Display Hero:** `32px` / `1.2` / Weight `700` (Main showcase title)
- **H1 Section:** `24px` / `1.3` / Weight `700` (Company detail headers)
- **H2 Card Title:** `18px` / `1.4` / Weight `600` (Company name in grid)
- **Body Regular:** `14px` / `1.5` / Weight `400` (General descriptions, addresses)
- **Body Small:** `12px` / `1.5` / Weight `500` (Field keys, source categories)
- **Data Mono:** `13px` / `1.4` / Weight `500` (JetBrains Mono for accounts, amounts, dates)
- **Badge Text:** `11px` / `1.2` / Weight `600` / Uppercase letter-spacing `0.05em`

---

## 4. Spacing, Elevation & Radii

### 4.1 Spacing Scale (Base Unit 4px)
- `space-1`: `4px` (inline badges, micro gaps)
- `space-2`: `8px` (button padding, input horizontal gap)
- `space-3`: `12px` (card content separation)
- `space-4`: `16px` (container inner padding, grid gap)
- `space-6`: `24px` (card padding, section margin)
- `space-8`: `32px` (hero layout spacing)

### 4.2 Border Radii
- `radius-sm`: `4px` (Status badges, tags, code blocks)
- `radius-md`: `8px` (Buttons, search inputs, interactive pills)
- `radius-lg`: `12px` (Dossier cards, data panels, modals)
- `radius-full`: `9999px` (Pills, indicator dots, avatar badges)

### 4.3 Elevation & Shadows
- **Level 0 (Flat):** Primary design default (`box-shadow: none; border: 1px solid var(--color-border)`).
- **Level 1 (Card Rest):** `0 1px 3px rgba(15, 23, 42, 0.05)`
- **Level 2 (Hover State):** `0 4px 12px rgba(15, 23, 42, 0.08)` with border color shifting to `#CBD5E1`.
- **Level 3 (Modal / Drawer):** `0 20px 25px -5px rgba(15, 23, 42, 0.1), 0 8px 10px -6px rgba(15, 23, 42, 0.1)`

---

## 5. Component Conventions

1. **Search & Filter Bar:**
   - Single prominent search input with instant client-side filtering by name, organisation number, municipality, or NACE sector.
   - Filter chips for quick triage: "All", "Active Only", "Audited Financials", "Web Verified".
2. **Company Dossier Card:**
   - Header with legal name, 9-digit orgnr chip, and legal form pill (`AS`, `ASA`, `ENK`).
   - 3-column summary grid: Key Management (CEO / Chair), Audited Revenue & Net Result, Location & Headcount.
   - Verified web domain link with SSL lock icon and direct source lineage indicator.
3. **Financial Statement Matrix:**
   - Clean tabular display with right-aligned currency values (`NOK`).
   - Missing line items explicitly displayed as `—` (dash) with `not_available` tooltip—never displayed as `0.00`.
4. **Evidence Lineage Drawer:**
   - Clickable badge expanding exact source URL, retrieval UTC timestamp, verbatim claim span, and copyable SHA-256 hash.
