# Vestígio — UI Kit

High-fidelity recreation of the Vestígio forensic-communications platform. Single product surface: the internal SaaS app used by perícia teams, contenciosos, and due-diligence operators.

## Screens

- **Dashboard** — list of active cases with case metadata, perito, corpus size.
- **Case detail** — single case with tabs: Corpus · Cronologia · Vestígios · Laudos.
- **Cronologia (timeline)** — chronological view of all vestígios in a corpus, with filter rail.
- **Vestígio detail** — single piece of evidence with forensic metadata, hash, source, content preview.
- **Laudo (report cover)** — the output document. Serif, paper, with seal.

## Components

`AppShell.jsx` · `CaseList.jsx` · `CaseDetail.jsx` · `Timeline.jsx` · `VestigioCard.jsx` · `LaudoCover.jsx` · `Controls.jsx` (buttons, inputs, tags)

## How to use

Open `index.html`. Click across the nav — top-level tabs switch between the screens. This is a cosmetic prototype; data is static.
