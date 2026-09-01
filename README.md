# Project Montage

**A fully local, AI-assisted photo album designer.** Point it at a folder of a few
thousand event photos (a wedding, a birthday, any big shoot) and it imports, culls
duplicates/bursts, scores technical quality, understands scenes with a vision-language
model, clusters people by face, and lays the best shots out into a print-ready,
professionally designed album — spreads, styles, crops and all — that you can still
edit by hand or by typing a plain-English instruction. Everything runs on your own
machine; no photo ever leaves it.

## Why this exists

After a big event you end up with thousands of near-duplicate photos and no realistic
way to hand-pick and lay out a real printed album without paying a designer or spending
a weekend in Lightroom. Commercial "AI album" tools exist, but they're closed, cloud-
based, and not something you can point at your own model weights or trust with private
family photos.

This project started as a personal tool (September 2026) to solve that problem for one
specific event, on the premise that the whole pipeline — curation, scene understanding,
layout, and even conversational editing — can run entirely offline against small,
locally-hosted models, with the human keeping final creative control at every step. It's
released here so anyone else with the same problem (and a folder of thousands of event
photos) can use it, adapt it, or build on it.

## How it was built

Built iteratively, phase by phase, with an AI coding agent (Claude Code) doing the
implementation from a detailed product/technical brief the project owner wrote and
refined — included in this repo as [`_projectIdea.md`](_projectIdea.md), the original
vision document that every phase and design decision below traces back to (code comments
throughout `src/` cite it by section, e.g. "idea §16"). Each phase was verified against a
real ~1,800-photo dataset before moving to the next:

1. **Proof of concept** — import → burst/duplicate detection → quality scoring → Qwen3-VL
   scene understanding → proportional shortlist → chronological storyboard, with no UI at
   all, to answer one question first: can the system select and sequence photos
   intelligently? (Verified against a real 1,848-photo dataset, producing a 180-photo,
   71-section balanced storyboard.)
2. **Album engine** — the spread data model and layout grammar, intelligent
   content-aware cropping, the renderer, design styles/typography, multi-print-size
   support, preflight checks + PDF export, and face clustering. (Verified: 180 photos →
   88 styled spreads → an 88-page album PDF.)
3. **Desktop application** — a real browser UI over all of the above: project/pipeline
   management, drag-and-drop storyboard reordering, people labeling, and a spread editor
   with lock/regenerate. Originally four separate local web tools on four ports;
   consolidated into one process/one port with a shared design system (see
   [Architecture](#architecture)).
4. **Advanced AI (in progress)** — conversational spread editing ("give the bride
   portrait more prominence") is implemented; face-relationship inference, richer
   emotional scoring, video support, and learned style preferences are not yet built.
5. **Generative tools (not started)** — background generation/extension/cleanup, always
   on copies only, never on the originals.

See [Roadmap](#roadmap--current-status) for exactly what's done vs. planned.

## Features

- **Fully local.** No photo, face, or crop ever leaves your machine — every model
  (vision-language, face detection, quality scoring) runs on local weights.
- **Smart curation.** Perceptual-hash burst/duplicate grouping, technical quality scoring
  (sharpness, exposure), and a vision-language model's judgment of which photos actually
  deserve a place in the album — not just the sharpest ones.
- **People clustering.** Faces are detected and grouped into people automatically; a
  browser UI lets you name, merge, or deprioritize clusters.
- **Automatic storyboard + spread layout.** Event/story detection sequences photos
  chronologically by section; a layout grammar assigns hero/supporting slots per spread.
- **Content-aware cropping** that keeps detected faces in frame.
- **Multiple design styles** (modern minimal, luxury wedding, editorial, documentary),
  separate from layout, so changing style never touches slot geometry.
- **Editable, not a black box.** Every spread can be locked, regenerated individually or
  in bulk, or hand-edited slot by slot — automation proposes, you decide.
- **Conversational editing.** Type an instruction on a spread ("use fewer photos here",
  "these two look repetitive") and a local VLM proposes a small, reviewable set of slot
  swaps you approve or discard — it never touches pixels directly.
- **Print production, not a demo.** Multiple print sizes (12×18 through 12×36), 300 DPI
  target, preflight checks (resolution, missing assets) before you export a real PDF.
- **One app, one browser tab.** A single process serves the whole UI; the heavy AI model
  only loads on demand (running a pipeline stage, or first use of chat editing) — never
  at startup.

## Architecture

```
                         Browser (http://127.0.0.1:8000/)
                                     │
                              src/app.py  (single public port)
                          reverse-proxies by path prefix
        ┌───────────┬───────────────┼───────────────┬────────────┐
        │            │               │               │            │
   Dashboard       People        Storyboard     Spread Editor    Export
  project_app.py  label_people   reorder_       spread_editor    export_app.py
   (pipeline       _app.py       spreads_app.py  _app.py          (preflight +
    runner)        (face                                          PDF export)
                    clusters)
```

Each section above is its own `http.server`-based app (stdlib only, no web framework),
run on its own internal-only localhost port as a background thread inside the single
`app.py` process; `app.py` is the only port exposed to the browser, and forwards each
request to the right section. This replaced four/five separate processes on separate
ports (`project_app.py --port 8002`, etc.) with one double-click launcher
(`run_album_studio.bat`). All five screens share one design system
(`src/web_theme.py`): a light theme, a persistent sidebar nav, and a live AI-engine
status indicator, laid out fluidly so it works at any browser window width.

The **processing pipeline** itself is a sequence of independent, resumable, idempotent
stages (each re-run skips already-processed rows/files), driven by the Dashboard screen
or runnable standalone from the CLI:

```
import → burst/duplicate detection → quality scoring → Qwen3-VL understanding
  → shortlist → spread layout planning → face detection → people clustering
  → intelligent cropping → render spreads → export PDF
```

Every stage reads/writes a single local SQLite database (`cache/project_full.db`) plus a
couple of JSON files (`exports/spreads.json`, `exports/crops.json`) that describe the
current storyboard and crop choices — nothing is baked into rendered pixels until the
render step, so re-ordering, re-styling, or re-cropping never means starting over.

## Components / tech stack

| Component | What it does | Tech |
|---|---|---|
| Import | Ingests photos, extracts EXIF | Pillow |
| Burst/duplicate detection | Groups near-identical shots taken in a short window | ImageHash (perceptual hashing) |
| Quality scoring | Ranks shots within a burst by sharpness/exposure | OpenCV |
| Scene understanding | Judges album-worthiness, tags each shot's event/moment | **Qwen3-VL** (local, via `llama.cpp`'s `llama-server`) |
| Face detection + clustering | Detects faces, groups them into people | **InsightFace** (`buffalo_l` model pack) |
| Layout + cropping | Assigns spread layouts, computes face-aware crops | Custom layout grammar + Pillow |
| Rendering | Composites final spread images | Pillow |
| Conversational editing | Turns a plain-English instruction into slot-swap proposals | Qwen3-VL (text-only prompt against the same `llama-server`) |
| Web UI | Single-process local app, five screens | Python stdlib `http.server` (no framework) |
| PDF export | Preflight checks + multi-page PDF assembly | Pillow |

## Getting started

### 1. Clone and set up Python

```bash
git clone https://github.com/vsd9992/Project-Montage.git
cd Project-Montage
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

Built and verified on Windows with Python 3.13. It should run on macOS/Linux with the
same dependencies, with two known Windows-only spots — see
[Known limitations](#known-limitations).

### 2. Models

Model weights are **not** included in this repo (they're multiple gigabytes). Download
them yourself and place them exactly as below — the paths are also overridable with
environment variables if you'd rather keep them elsewhere.

| Model | Used for | Where to get it | Expected path |
|---|---|---|---|
| Qwen3-VL 8B Instruct (GGUF, Q4_K_M) + its mmproj file (Q8_0) | Scene understanding, conversational editing | Search "Qwen3-VL 8B Instruct GGUF" on Hugging Face (multiple community GGUF conversions exist) | `models/qwen3-vl/Qwen3VL-8B-Instruct-Q4_K_M.gguf` and `models/qwen3-vl/mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf`, or set `ALBUM_STUDIO_QWEN_MODEL`/`ALBUM_STUDIO_QWEN_MMPROJ` |
| `llama.cpp`'s `llama-server` executable | Serves the Qwen3-VL model locally over HTTP | Build or download a release from https://github.com/ggml-org/llama.cpp | `models/llama-cpp/server/llama-server.exe`, or set `ALBUM_STUDIO_LLAMA_SERVER` |
| InsightFace `buffalo_l` | Face detection + embeddings for people clustering | Auto-downloads on first run via the `insightface` Python package (needs internet the first time only) | `models/insightface/` (created automatically) |

`models/siglip2` (SigLIP2) was downloaded during early planning as a candidate for
duplicate/similarity detection but isn't wired into any pipeline stage — burst/duplicate
detection currently uses perceptual hashing instead. You don't need it.

Pick a GGUF quantization that fits your GPU/CPU memory; Q4_K_M is what this project was
built and tested against, as a reasonable quality/size tradeoff.

### 3. Run the pipeline on your photos

Launch the app and use the Dashboard's Start button (it runs every stage in order,
auto-advancing, resumable if paused):

```bash
run_album_studio.bat
```

or from any shell:

```bash
.venv\Scripts\python.exe src\app.py --db cache\project_full.db --exports exports ^
  --spreads exports\spreads.json --crops exports\crops.json ^
  --rendered-dir exports\rendered_spreads --out-pdf exports\album.pdf
```

This opens `http://127.0.0.1:8000/` in your browser. Point the Dashboard's "Source photo
directory" field at your event's photo folder and hit Start. No AI model loads until a
stage that needs one actually runs (Qwen3-VL for scene understanding) or you open the
conversational-editing chat.

Each stage is also runnable standalone from the CLI (`python src/import_stage.py --help`,
etc.) if you want to script the pipeline instead of using the Dashboard.

### 4. Review, edit, export

- **People** — name or merge face clusters.
- **Storyboard** — drag spreads into a different order.
- **Spread Editor** — swap a slot's photo, lock a spread, regenerate one or all unlocked
  spreads, or type a plain-English instruction in the chat panel.
- **Export** — check the preflight results (resolution, missing assets) and export the
  final PDF.

## Project structure

```
src/                   All application code (stdlib http.server web app + pipeline stages)
  app.py                Single entry point / reverse proxy (start here)
  web_theme.py           Shared design system for the web UI
  project_app.py         Dashboard: pipeline runner
  label_people_app.py    People screen
  reorder_spreads_app.py Storyboard screen
  spread_editor_app.py   Spread Editor screen + conversational editing
  export_app.py          Export screen
  import_stage.py, burst_stage.py, quality_stage.py, qwen_stage.py,
  shortlist_stage.py, spread_stage.py, face_stage.py, person_cluster_stage.py,
  crop_stage.py, render_stage.py, export_pdf.py
                          The pipeline stages, each also runnable standalone
  conversation_stage.py  Qwen3-VL prompt building + op validation for chat editing
  layout_geometry.py, style_stage.py
                          Layout grammar and design styles
  db.py                  SQLite schema/connection helper
run_album_studio.bat    Double-click launcher
requirements.txt        Python dependencies
```

Not included in this repo (see `.gitignore`): your source photos, the generated
SQLite database/exports, model weights, and this project's own AI-agent working notes.

## Roadmap / current status

| Phase | Status |
|---|---|
| 1. Proof of concept | Complete |
| 2. Album engine | Complete |
| 3. Desktop application | Complete (consolidated into a single app + shared design system) |
| 4. Advanced AI | In progress — conversational editing done; face relationships, richer emotional scoring, video, learned style preferences not started |
| 5. Generative tools | Not started |

## Known limitations

- **Windows-only font paths.** `style_stage.py` looks up caption fonts from
  `C:\Windows\Fonts`; on macOS/Linux you'll need to point it at your system's font
  directory (or embed fonts) before styles render captions correctly.
- **Single fixed canvas size per run.** Print size/style are chosen when you start the
  pipeline (or via `--size`/`--style` flags), not editable per-spread after rendering —
  changing them means re-rendering.
- **Conversational editing is deliberately narrow.** It can only swap which photo fills
  an existing slot, from same-event candidates the model is explicitly shown. It cannot
  change a spread's layout, slot count, or move a photo to a different spread — those
  remain manual, hands-on-keyboard edits by design (see `_projectIdea.md` §18: "It
  shouldn't directly manipulate pixels").
- **Not mobile-responsive by design.** The UI is fluid across desktop browser window
  widths, not built for phone-sized screens.

## Contributing

Issues and pull requests are welcome. If you build a new pipeline stage or UI screen,
please follow the existing pattern: stdlib-only web code, one stage = one independently
resumable script, and keep layout geometry and visual style decoupled (see
`style_stage.py`'s docstring for why).

## License

Licensed under **AGPL-3.0 with the Commons Clause** — see [`LICENSE`](LICENSE) for the
full text. In plain terms:

- **Personal / non-commercial use is fully permitted**, including modifying it, running
  your own copy, and self-hosting it for yourself.
- **If you modify and distribute it, or run a modified version as a network service
  others interact with, you must share your source code** under this same license (the
  AGPL-3.0 copyleft condition).
- **You may not sell it, or sell any product or service whose value comes substantially
  from it** — this project or a fork of it — under the Commons Clause condition layered
  on top of AGPL-3.0. That covers selling the software itself, selling access to a
  hosted version of it, and selling paid support/consulting built around it.

## Acknowledgments

Built on [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL), [llama.cpp](https://github.com/ggml-org/llama.cpp),
[InsightFace](https://github.com/deepinsight/insightface), and Pillow/OpenCV/ImageHash.
Implementation assisted by [Claude Code](https://claude.com/claude-code).
