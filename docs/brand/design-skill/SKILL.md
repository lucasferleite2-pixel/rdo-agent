---
name: vestigio-design
description: Use this skill to generate well-branded interfaces and assets for Vestígio, a forensic-communications-analysis SaaS platform (Portuguese-language, Brazilian legal/M&A market), for production or throwaway prototypes/mocks. Contains essential design guidelines, colors, type tokens, fonts, logos, seals, and UI kit components. Aesthetic: institutional forensic — Stripe Press × The Economist × Hermès palette. No blue anywhere. Serif headlines mandatory. High information density. Print-safe in black & white.
user-invocable: true
---

# Vestígio — Design Skill

Read `README.md` first. It covers product context, content/voice rules, visual foundations, and iconography. Then consult:

- `colors_and_type.css` — single source of truth for color + type + spacing + motion tokens. Import it into every artifact.
- `assets/` — logos, seal, monogram. Copy these out; do not redraw.
- `ui_kits/vestigio/` — componentized recreation of the product. Read the JSX for patterns; copy what you need.
- `preview/` — reference cards showing each token/pattern in isolation.

## Invocation behavior

If a user invokes this skill with no other guidance, ask them:
1. Is this a production surface (app screen, doc) or a throwaway (mock, deck, pitch)?
2. Language: Portuguese (default) or English?
3. Is it a marketing piece, product UI, or forensic document (laudo)?
4. Print vs screen?

Then act as a senior designer. Output HTML artifacts for mocks/prototypes/decks; code snippets for production.

## Non-negotiables — do not violate

1. **No blue.** Not in links, not in accents, not in iconography. Links are graphite with underline; hover bordô.
2. **Serif headlines mandatory.** EB Garamond at H1–H4. Sans only in body and UI chrome.
3. **No emoji.** Any surface, any reason.
4. **No gradients, no glassmorphism, no flat illustrations.**
5. **High density.** Reject empty heroes; reject giant cards with three words.
6. **Print-safe.** Every screen must survive a B&W print. Hierarchy from weight/size/family/rules — not color alone.
7. **Tables editorial, not dashboard.** Thin hair rules, uppercase tracked headers underlined in ink, tabular numerals.
8. **Bordô sparingly.** It is the voice of authority, not decoration. Gold (`#8B6F47`) is ONLY for seals and watermarks.
9. **Tone.** Short dense sentences. Technical forensic vocabulary (laudo, corpus, vestígio, cronologia, evidência). Zero hype. No "você" in marketing copy.
10. **Minimum body 15–16px**, line-height 1.6–1.7.

## Starting template

```html
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="colors_and_type.css">
  <title>Vestígio — [surface]</title>
</head>
<body>
  <!-- build here -->
</body>
</html>
```

If a screen looks like "any SaaS product", it is wrong. Push it toward consulting report / legal brief / editorial review.
