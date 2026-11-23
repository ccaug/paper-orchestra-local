# The control-room GUI

Starts with the review (`--port 8765` by default, `--no-gui` to disable). Views: **Original**, **Reescrito** (after), **Cambios** (red = removed, blue = added).

| Element | Meaning |
|---|---|
| Red word boxes sweeping the text | The model is **reading** that chunk (prompt processing). The sweep speed is an *estimate* from measured prefill speed: the model does not expose which word it is on |
| Pulsing red boxes | Exact words the model **cited** in its output (real, parsed live) |
| Blue/cyan arcs + chip (`value`, `concept`) | The passage is compared with another section; the chip says how |
| Purple card at the margin | Comparison with a line of your results log |
| Green boxes | Passage rewritten; amber dashed = suggestion; gray/blue = note |
| Ring | Progress and ETA (likely); "peor caso" = worst case |
| Minimap | One cell per chunk (height = risk, colour = state, ✎n = rewrites), arcs = cross-references |
| Timeline | Fast vs deep model tasks over time |
| Changes / Suggestions | Diffs with justification, signals (screener/deep/attack/harmonize/tournament), a one-click "Aplicar al paper" for suggestions (LaTeX mode) |

The UI text is in Spanish. Suggestions accepted in the GUI are written to `refined_flat.tex` (recompile to refresh the PDF).
