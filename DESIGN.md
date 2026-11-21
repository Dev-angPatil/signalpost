# DESIGN.md: the Signalpost company browser

The page every run writes (`ui/template.html`). Its job: find a Norwegian company, read it in a minute, and verify any fact against its source. Readers are people checking a company before they apply, sell, partner or invest, and reviewers checking that each fact is real.

## Principles

1. **Five signal lamps are the one memorable thing.** Each company has five areas (company record, annual accounts, people & places, website, public footprint). A lamp per area is lit green when found, amber when we can't tell, red when the source was blocked or could not be read, and unlit when nothing was found. The lamps are the brand mark, every list row, the dossier header, the compare table and the run panel. They always encode data.
2. **Stamp blue means proof, and only proof.** The ink of an official rubber stamp marks pressable facts, source chips on hover, source links, the evidence slip and keyboard focus. Nothing decorative uses it.
3. **One register sheet, ruled, not a stack of cards.** A company is one white sheet on fog; its parts are separated by rules. Key figures sit in one ruled strip. Radii are modest (8–14 px), shadows appear only on things that float (dialogs, the compare tray).
4. **Plain labels.** Sentence-case headings, no all-caps eyebrows, no "A · B" meta strings in the list ("Manufacturing, Gulen"; "2 news"; "1 open job"). Missing values say why; nothing missing is drawn as zero.
5. **Motion answers a person.** The only unprompted motion is the five lamps lighting in turn when a company opens; reduced motion turns it off.

## Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--fog` | `#EEF2F1` | `#0E1513` | Page and header |
| `--sheet` | `#FFFFFF` | `#151E1B` | The register sheet, list, dialogs |
| `--ink` | `#17211F` | `#E4EBE8` | Text, selected states |
| `--slate` | `#55645F` | `#9AA9A4` | Secondary text (6.2:1) |
| `--mute` | `#66736F` | `#7F8E89` | Tertiary text (4.9:1) |
| `--rule` | `#D5DDDA` | `#28342F` | Rules and outlines |
| `--stamp` | `#2E3A8C` | `#9CA6F2` | Proof only (10:1) |
| `--lamp-on` | `#1F7A52` | `#4CC38A` | Found |
| `--lamp-amber` | `#95600E` | `#E2A84A` | Can't tell |
| `--lamp-red` | `#B23B3B` | `#EE7B73` | Blocked, could not be read, adverse status |
| `--lamp-off` | `#C6CFCB` | `#34423D` | Not found |

All text colours meet WCAG AA (4.5:1) on their backgrounds.

## Type

- **Schibsted Grotesk** (Norwegian-made grotesk, Google Fonts, variable 400–800) for everything. Company name 30 px / 700 / −0.015 em; section headings 18 px / 650; body 15 px / 1.5; summary 16–17.5 px / 1.55.
- **IBM Plex Mono** only where a fixed width carries meaning: SHA-256 hashes and field keys.
- Tabular figures only in the annual-accounts table, where columns must align (they widen this face's punctuation elsewhere).

## Layout

```
┌ fog header: ●●●●● Signalpost   [ search ……………… ]                 Export list ┐
├ list (sheet) ──────────────┬ register sheet ───────────────────────────────────┤
│ ✓ All run checks passed    │ COMPANY NAME AS                  [Compare][Link]  │
│ filters, signal chips      │ 923 609 016  AS · Limited  Oslo  ● Active  site   │
│ ─────────────────────────  │ ┌ ● Company ┬ ● Accounts ┬ ● People ┬ ● Web ┬ ○ … ┐│
│ NAME AS          NOK 6.2 M │ ─ Summary (lead point larger) ─────────── Verify  │
│ Industry, Place    ▲ 34.7% │ ─ Revenue │ Operating │ Net │ Equity │ Employees  │
│ ●●●●● 4 news  site.no      │ ─ Activity: dated line, lamp-coloured dots        │
│ ─────────────────────────  │ ─ Leadership │ Company                            │
│ …                          │ ─ Annual accounts · Published facts · Sources     │
└────────────────────────────┴───────────────────────────────────────────────────┘
```

Phones (≤ 860 px): one column; the list and the sheet are separate views; coverage becomes a five-row list; key figures two per row; touch targets ≥ 40 px.

## Components

- **Citations:** a fact's own text is pressable (dotted stamp-blue underline, solid on hover), followed by a small grey chip naming its source (*Regnskapsregisteret*, *Brønnøysund roles*, *Kunngjøringer*, *NAV*, the company's domain; "+1" when it cites more). The chip turns stamp blue on hover. Either opens the evidence slip. There is no separate Verify button.
- **Coverage strip:** five buttons, one per area, each a lamp with the area's name and state; pressing one scrolls to its section and briefly marks it.
- **Evidence slip:** the fact's value, then one card per cited record (stamp-blue left rule): source class, address, retrieval time, reporting period, SHA-256, the quoted text, and actions (Open source, Open at the quoted text, Copy quote, Copy hash).
- **Activity:** a vertical dated line; job dots green, news dots ink, notices hollow, warnings red.
- **Annual accounts:** right-aligned figures, `—` with a tooltip for lines not reported in the filing.
