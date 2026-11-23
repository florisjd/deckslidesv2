# DeckCraft Studio — Ground-Up Rebuild Plan

*Written 2026-11-23 after reading `project_plan.md`, `CAPABILITIES.md`, ~9,000 lines of server code, the scene engine, the test suite, and inspecting real output (an exported 94 s MP4 and cached slide renders). Companion to those two docs; it does not replace them.*

---

## 0. The short version

**Verdict:** the current app proves the idea and has several genuinely good engineering instincts, but it was grown as a pipeline of *heuristics wrapped around a 4B model*, and the heuristics (regex heading detection, `repair_specs`, a vision-LLM critic) are now the main source of complexity and of remaining quality problems. A rebuild should keep the instincts and replace the foundation.

**The five biggest changes, in order of value:**

1. **Make "accurate to the source" a structural property, not a warning.** Extract *claims* (obligations, prohibitions, deadlines, thresholds, steps, contacts) from the policy with page/quote citations once, then build slides and scripts *from claims*, and verify every script sentence against them. Today the only check is "does this number appear somewhere nearby" (`llm.py:308`).
2. **Replace "free-form JSON + repair" with typed, per-layout schemas.** One Pydantic model per slide kind, filled in small constrained calls. This deletes most of `layouts.py` (431 lines of repair logic) and the "model wrote bullets into the script" class of bugs.
3. **Replace the vision-LLM critic with deterministic layout linting** (overflow, min font size, contrast, empty-area ratio) measured in the browser. Cheaper, instant, and catches the defects I actually saw in your renders (details in §3).
4. **Move state from JSON files + in-memory jobs to SQLite + a content-addressed blob store + a durable job queue.** Gives versioning, review/approval history, crash-safe batches, "what changed when the policy was updated", and undo.
5. **Make the reviewer's job the product.** A side-by-side *source ↔ slide ↔ script* review screen with per-slide approval and an audit trail. For compliance training, "a named person approved this against policy v3" is worth more than any animation.

**Recommended stack in one line:** Python 3.12 core (FastAPI, Pydantic v2, SQLite, ffmpeg, Ollama, Kokoro, Docling) + TypeScript/React studio UI + a shared TypeScript *scene kit* (React + GSAP, seekable timeline) rendered headlessly by Playwright; wrapped later in Tauri for a double-click Windows install.

**Do not start by rewriting.** Phase 0 (§17) is three days of work that would make any rewrite — or any improvement to the current app — measurable: put the project under git (it currently is not), and build a 5-document golden corpus with baseline numbers.

---

## Build status

*Legend: `[x]` built and covered by tests · `[~]` partly built (what is missing is stated) · `[ ]` not built · `[-]` dropped by a decision in §18. Last updated 2026-11-23. Keep this section current: tick items off as they are completed.*

**Where things are:** the new app lives in `deckcraft/` (`packages/core` Python, `web/scene-kit` JS, `themes/`). `uv run --package deckcraft-core pytest` runs the suite (75 tests). `uv run --package deckcraft-core python serve_preview.py <data_dir>` serves the review workspace at `http://127.0.0.1:8765/review`. Verified end to end on two real documents (a welcome/values guide and an obligation-heavy HR guide), each producing a narrated, captioned MP4.

### Phase 0 — Safety net and baseline
- [x] `git init` and a committed history
- [~] Golden corpus: two real documents have been run informally; there are no hand-labelled headings/claims and no `eval/baselines/` numbers
- [ ] Baselines from the *old* app (the old app is not in this repository)
- [ ] Compare 2-3 local models (only `gemma3:4b` has been tried)
- [x] Hermetic tests: all paths are injected through `Settings`; nothing reads real user data

### Phase 1 — Core skeleton + understanding
- [x] Settings, SQLite (WAL) tables, content-addressed blob store (atomic writes, dedupe)
- [x] Durable job queue: retries, cancel, restart recovery of stale `running` jobs
- [ ] Alembic migrations (tables are created with `create_all`; schema changes need a fresh database)
- [ ] SSE event stream (clients poll `/jobs/{id}`)
- [x] Ingest: PDF (pypdfium2, real font size/weight for headings, header/footer removal by repetition, line reflow, bullet and emoji cleanup), DOCX (headings, lists, tables, TOC stripping), PPTX (text, tables, speaker notes, LibreOffice/PowerPoint COM/Pillow rasterisation), Markdown/text, zip-safety checks, section splitting
- [ ] Ingest: scanned PDFs / OCR, Docling, PDF table extraction and PDF TOC stripping
- [x] Section-level source diff (`diff_sections`) with rename detection
- [x] Claims with verbatim-quote validation and char spans (rule-based); modality, kind, risk score
- [x] Lead-in + list handling: items inherit the lead-in's obligation ("team members **must**: ..."), `This includes:` attaches to the previous sentence, claims keep `lead` and `item` separately
- [ ] LLM claim extraction, embedding dedupe, glossary, cross-references, audience roles
- [x] Module planner: chapter-aware grouping (~12 claims, ~90 s), balanced splits, tiny-module merge, unique titles
- [x] Coverage accounting (unassigned must/must-not claims are errors; uncovered info claims are warnings)
- [ ] LLM `name_module`, embedding topic segmentation
- [ ] Eval harness over the corpus

### Phase 2 — Scenes and renderer parity
- [x] Pydantic scene union (13 kinds incl. `image`) with capacity limits; optional list lead captions
- [x] Storyboard: `choose_sequence`, `fallback_scene`, `validate_scene` (provenance, stat number, US spelling)
- [ ] LLM `fill_scene` per kind (slide copy is rule-built from claims)
- [x] scene-kit: 12 layouts + `image`, seekable GSAP timeline, reveals anchored to narration beats, fit-to-frame scaling
- [x] Layout linter (overflow, minimum font size, WCAG contrast, empty-area); blocks export
- [x] One theme format (`themes/*.json`), 3 themes
- [ ] Converter for the 34 legacy templates (not in this repository)
- [ ] Visual regression suite (only linter + determinism hash tests)
- [x] Colour-correct encode (BT.709, limited range, tagged, faststart); cache key includes a scene-kit hash
- [ ] RenderPool / frame-range sharding (one browser, one page at a time)
- [x] `ImageScene` importer for existing decks (PPTX rasterised slides + speaker notes to beats, blob hydration, missing-image lint)

### Phase 3 — Script, verify, voice, export (cut-over slice)
- [x] Beat-based narration: rule writer + optional Ollama writer (falls back to rules if it drifts); reveals tied to beats
- [ ] Length control (`length_off` / resize turn)
- [x] Verifier: numbers (digits and number words), modality weakening, negation flips, entities, coverage, locale lint; warnings for paraphrased strength, omitted slide numbers and unsupported sentences; judged against the one claim a sentence restates
- [ ] Entailment (NLI) and retrieval support
- [x] Kokoro provider, per-beat audio cache, two-pass loudness mastering to -16 LUFS / 48 kHz stereo
- [ ] Forced word alignment (cue times inside a beat are estimated by character count)
- [ ] ASR intelligibility check; lexicon UI (a lexicon dict exists)
- [x] MP4 + SRT + VTT with chapters and soft captions
- [x] Review service: edit, verify, approve (blocking findings stop approval, not editing), export gated on approval, audit log
- [x] Minimal review UI

### Phase 4 — Review workspace and updates
- [x] Three-pane review workspace (source with highlighted quote · slide with schema-driven form · narration + checks), keyboard shortcuts, reorder/delete slides
- [~] Timeline view: read-only estimate; no waveform or draggable pauses
- [x] Policy-update workflow: diff preview, apply, stale modules, regenerate only stale, retire emptied modules, source versions
- [ ] Legacy importer (old `deck.json` / templates / lexicon are not in this repository)
- [ ] pdf.js source viewer, theme browser, voice/lexicon screens, diagnostics, comments

### Phase 5 — Optional extras
- [x] Intro/outro cards from project branding (intro skipped when the first slide is a cover)
- [x] Music bed with sidechain ducking and re-mastering (user supplies the music file; none bundled)
- [x] Multilingual behind a flag: translation with number-preservation and capacity checks, Kokoro voices for es/fr/it/pt, subtitle language tags (non-English audio not reviewed by ear)
- [x] Style memory: edit capture, house style guide, few-shot retrieval (word overlap), `edit_ratio` metric (API only, no UI)

### Hardening found by running real documents (not in the original plan)
- [x] Obligations survive list formatting; bold lead-ins are not headings
- [x] Page suffixes ("(1/2)") are not spoken or flagged as invented numbers
- [x] Lead-in shown and spoken once on list and Do/Don't slides; mixed-polarity lists keep full sentences
- [x] Cards get real titles or none; text is clipped at clause boundaries, never mid-sentence with an ellipsis
- [x] Corruption test on real data: changing "must" to "may" in narration is refused at approval

### Dropped by decision (see §18)
- [-] Tauri shell, installer, auto-update, multi-user workflows, cloud LLM provider, HTML5 player, SPFx / SharePoint, SCORM/cmi5, quizzes

### Known gaps worth doing next
Takeaways slide repeats earlier content · local-model module naming · listen-through review of audio and pacing · UI for branding, style guide, translation and music · Alembic migrations · RenderPool for faster export.

---

## 1. The outcome we are building toward

### 1.1 Product outcome

> A small HR/L&D team uploads a workplace policy or handbook and, in one sitting, ends up with **short, accurate, on-brand, narrated and subtitled training videos** that employees will actually finish — with everything running on the team's own computer.

Unpacked into things a user would notice:

| # | Outcome | What "good" looks like |
|---|---|---|
| O1 | **Faithful** | No rule, number, deadline, name or obligation strength ("must" vs "should") appears that isn't in the source; no important `must`/`must not` is silently dropped. |
| O2 | **Reviewable fast** | A reviewer can check a module against the source in minutes, not by re-reading the PDF. Edits are cheap and only re-do the affected audio/frames. |
| O3 | **Watchable** | Layout fills the frame sensibly, text is legible on a phone, motion supports the narration instead of decorating it, audio is consistent in loudness. |
| O4 | **Private & cheap** | Documents never leave the machine; no per-minute or per-token cost. |
| O5 | **Maintainable over time** | When the policy changes, the team sees *which* modules/slides are affected, regenerates only those, re-approves, re-exports. |
| O6 | **On-brand** | Their colours/fonts/tone, consistently across a series, without a designer. |
| O7 | **Deliverable where learning actually happens** | MP4 for sharing; ideally also an LMS-ready package with a knowledge check and completion tracking. |

### 1.2 Who it is for (inferred — please correct me)

HR / L&D / compliance staff at small organisations, no video or design resource, confidentiality concerns, Windows laptop with an 8 GB GPU. Sample data in the repo is an Australian trades/logistics employer ("Nationwide Hotshots", "Strategic HR Australia").

### 1.3 Proposed success metrics (set baselines in Phase 0, then hold the rebuild to them)

| Metric | Proposed target | How measured |
|---|---|---|
| Script faithfulness | ≥ 98 % of script sentences supported by a cited claim; 0 contradictions | Claim-level verifier + human spot check on golden corpus |
| Obligation coverage | 100 % of `must`/`must not` claims either covered or explicitly excluded by the reviewer | Coverage report per module |
| Reviewer edit rate | < 15 % of words changed between AI draft and approved script | Already measurable in the existing Learning page logic (`learn.edit_ratio`) |
| Layout validity at export | 0 overflow / contrast / min-size violations | Deterministic linter, blocks export |
| Empty-area ratio | < 25 % of frame empty on content slides | Linter (pixel/DOM based) |
| Loudness | −16 LUFS ±1 integrated, true peak ≤ −1.5 dBTP | ffmpeg `loudnorm` report |
| TTS intelligibility | Round-trip ASR word-error-rate < 3 % | faster-whisper over generated audio |
| Time to first reviewable module | < 3 min for a 30-page PDF on the target laptop | Timed run |
| Reproducibility | Same inputs → identical frame hashes | Hash test (the existing `test_motion_determinism.py` idea, extended) |

### 1.4 Explicit non-goals (suggested)

Cloud hosting, multi-tenant accounts, real-time collaboration, a general-purpose slide designer, voice cloning of real people (licensing/consent minefield; Kokoro doesn't support it anyway).

---

## 2. How the current app reaches the outcome

```
 PDF / PPTX / .deckcraft
        │
        ▼  ingest.py · pdf_modules.extract_pdf_sections (pypdf + regex headings)
  sections[]  ──────────────────────────────────────────────────────────────┐
        │                                                                    │
        ▼  pdf_modules.plan_modules (gemma3:4b JSON, or deterministic)       │ source text
  modules[] (reviewed in the planner UI)                                     │ truncated to
        │                                                                    │ 6000 chars
        ▼  layouts.plan_layouts (code picks layout sequence)  ◄──────────────┘
        ▼  pdf_modules.build_module_slides (ONE LLM call → slides + scripts together)
        ▼  layouts.repair_specs (regex/heuristic repair of the model's JSON)
        ▼  pdf_modules.build_scene_payloads → critic.sanitize_scene
        ▼  motion.render_slide_posters (Playwright PNG posters)
        ▼  critic.polish_slide (vision LLM scores + patches, ≤2 passes)
  deck.json + slides/*.png   (uploads/<id>/)
        │
        ▼  UI: edit script text, pick voice          scripting.py (per-slide AI scripts for decks)
        ▼  tts.SpeechService → tts-server (Kokoro ONNX, CPU) → WAV per sentence chunk
        ▼  export.py + motion.MotionPool: seek GSAP timeline per frame → JPEG → ffmpeg pipe
        ▼  concat (stream copy) + SRT + mov_text
  exports/<deck>/*.mp4 + *.srt
```

Three processes (`studio` :5173, `tts-server` :7860, `ollama` :11434), started by `start-deckcraft.ps1`. State is JSON per deck + JSONL logs + a 5 GB regenerable `.cache`.

### 2.1 What the current design gets right (keep these ideas)

These are real strengths and the rebuild should treat them as requirements:

1. **Seekable, deterministic animation.** Everything lives on one paused GSAP master timeline that the exporter *seeks*, never plays (`motion.py`, `slide-engine.js`). This is the right foundation for frame-exact video and is the hardest thing to get right.
2. **Content-addressed caching** at three levels (TTS chunk, slide audio, video segment) so a one-sentence edit re-does one sentence. (`tts.py`, `export.py` `SegmentSpec.key`).
3. **Display text ≠ spoken text** (`speech_text.py`, 26 golden tests) — `$4.2M`, `Q3`, `25%` stay on screen, are spoken correctly.
4. **Server orchestrates, browser displays**, with SSE progress and cancellable jobs.
5. **"Code plans, model fills"** (`plan_layouts`): measured live that a 4B model left to choose layouts almost always chose `statement`; the project correctly took that decision away from it.
6. **Windows-specific performance engineering** that was measured, not guessed: EcoQoS opt-out (`winperf.py`) gave a ~7× TTS speed-up and ~4× render speed-up; one fixed `num_ctx` to avoid 7 s model reloads.
7. **Security posture**: loopback binding, Host/Origin guard, zip-bomb/path-traversal checks, never auto-deleting user data.
8. **Honest documentation** of limits (`CAPABILITIES.md` "Current limits").
9. **A real test suite**: 193 tests collected, real ffmpeg in export tests, determinism tests.

### 2.2 Where the current design limits the outcome

Ordered by how much each hurts the outcomes in §1.1. "Evidence" is what I verified in code or output, not impressions.

| # | Problem | Outcome hit | Evidence |
|---|---|---|---|
| P1 | **Grounding is shallow.** Only numbers are checked, and only for presence anywhere in neighbouring slides. A real number on the wrong label, a changed `must`→`should`, an invented role, or a dropped prohibition all pass. The project's own §10 item 6 says so. | O1 | `llm.py:308` `ungrounded_numbers`; `project_plan.md` §10.6 |
| P2 | **Source is truncated to 6,000 chars per module and slides+scripts come from one free-form JSON call.** Long modules lose content silently; the model must satisfy 12 layouts × optional fields × script in a single 4B-model generation. | O1, O3 | `pdf_modules.py:699` `mod.source_text[:6000]`; `MODULE_SLIDES_SCHEMA` |
| P3 | **A repair layer compensates for the model.** `repair_specs`, `_fix_items`, `split_script_items`, `_merge`, `_swap_kind`, `_recap` exist because the schema is one loose object with all fields optional. Each repair is a heuristic that can itself be wrong ("`– Key Rule.`" appears as card copy in a real export, see P6). | O1, O3 | `layouts.py:132–340`; frame from the latest export (below) |
| P4 | **PDF understanding is regex-based and overfit to one handbook.** Heading rules assume numbered clauses; the running-footer regex literally contains "Strategic HR Australia Team Handbook Version"; the deterministic planner special-cases `7.x` policy headings; the PPTX renderer hard-codes "Strategic \| Practical \| Impactful" and default org names. A different customer's PDF will behave differently, and tables, columns, scans, and DOCX aren't handled. | O1, O6 | `pdf_modules.py:235`, `:472–473`, `:958`, `:1022`; `app.py:123`; `web/slide-engine.js:722`; `app.js:306` |
| P5 | **No way to edit slide content in the UI.** The only write endpoints are the script text, settings, and design (`slide-md`). To change a card title or a checklist item you must regenerate. | O2 | `app.py` `@app.put/patch` list: `/slide-md`, `/settings`, `/slides/{n}/script`, `/learning` |
| P6 | **Visual defects survive the critic.** In the real 94 s export (frame at 12 s) a 1920×1080 "process" slide has two small steps and ~55 % of the frame empty; card copy reads "Treat everyone with dignity – Key Rule."; US spelling "Prioritize" for an Australian employer. In a cached portrait render the "02" badge is dark-on-dark and illegible while "01" and "03" are fine. The bottom ~45 % of portrait frames is empty (known issue #17; `CAPABILITIES.md` says it was addressed, but the cached renders I opened still show it). A 4B vision model scoring 1–5 is not a reliable gate for any of these; a contrast/overflow/fill measurement is. | O3 | `.cache/archetype_check/p/slide-01.png`, `.cache/spot_check/p/slide-01.png`, ffmpeg frame of `exports/Module_1_Welcome…/…170103.mp4` @ 12 s |
| P7 | **Audio is not mastered.** Exported video: mean −22.1 dB, max −5.0 dB, stereo from a mono source, no loudness normalisation, no compression, no music, no transitions, no chapters. Loudness will differ between modules and from other content on an LMS or phone. | O3 | `ffprobe`/`volumedetect` on the export; grep for `loudnorm/acompressor/amix/xfade/chapter` finds nothing |
| P8 | **Video colour metadata is untagged.** Stream reports `yuvj420p(pc, bt470bg/unknown/unknown)` — full-range, no BT.709 tags — because frames arrive as MJPEG. Some players/browsers will show slightly different colours or contrast. | O3 | `ffmpeg -i` on the export |
| P9 | **State model is fragile and un-queryable.** Per-deck JSON with asyncio locks, JSONL logs, `uploads/` `exports/` `data/` `.cache/` in four places. Jobs live only in memory: a server restart mid-batch loses the batch. No history, no undo, no approval record, no "what changed". | O2, O5 | `jobs.py` (no persistence); `decks.py` |
| P10 | **No policy-update workflow.** Nothing links a deck back to a source version, so re-uploading a revised PDF means starting over. | O5 | `deck.json` has `source_sha256` only for the *generated* file |
| P11 | **Subtitle/animation sync is sentence-chunk granular.** Cues come from ≤380-character chunks (`CHUNK_CHARS`); no word timings, so reveals can't land on the word that motivates them and subtitles can't highlight. | O3 | `tts.py:33`, `:289` |
| P12 | **Monoliths and drift.** `app.py` 1,483 lines with a 190-line closure for generation; `web/app.js` 2,031 lines of globals; three overlapping theme layers (design.md → theme, Python profiles, generated `slide.md` that "wins" over the Python profile, with a test to catch drift). Docs disagree with each other (9 vs 12 layouts; 85/109/193 tests). Rename debt (`voxcpm_server.py`, `voxcpm_url`, VoxCPM docstrings while Kokoro is in use). | maintainability | file sizes; `CAPABILITIES.md` "Template files are generated" |
| P13 | **Tests aren't hermetic.** Two tests read the real `data/` directory and now fail because a custom template was imported through the app (35 ≠ 34). | reliability | `tests/test_slide_md.py:200` `Path("data").resolve()`, `tests/test_sysmon_design.py:109`; 191 pass / 2 fail in my run |
| P14 | **Platform coupling.** Hard-coded Chrome/Edge install paths, `--no-sandbox`, a 206 MB alpha Rust renderer (`engine/`) used only to rasterise uploaded PPTX, ctypes Windows-only perf hooks inline with generic code. Needs the user to have Chrome/Edge. | packaging | `motion.py:22–27, :55`; `render.py` |
| P15 | **Learning subsystem is built ahead of its data.** ~1,000 lines (`learn.py`, `selfeval.py`, `examples.py`, DPO export, design-lesson mining) for a log with 2 edited records; the fine-tune needs ~200 pairs. The *data capture* is cheap and right; the *machinery* is premature. | focus | `project_plan.md` §10.18 |
| P16 | **No LMS-grade output.** Scenario slides are "interactive" only as animation in a video; there is no actual quiz, no completion tracking, no SCORM/xAPI. For compliance training this is often the real requirement. | O7 | outputs = `.mp4`, `.srt`, `narrated.pptx` |
| P17 | **Only one model has been tried** (`gemma3:4b`; it's the only one installed). Output quality problems are being engineered around instead of measured against alternatives. | O1–O3 | `ollama list` |
| P18 | **Version control absent.** The directory is not a git repository, with ~5 GB of regenerable cache beside irreplaceable code and data. | all | environment check; `project_plan.md` §10.1 |

**Picture of the real output** (frame at 12 s of `Module_1_Welcome_to_Nationwide_Hotshots…mp4`, landscape 1920×1080): clean typography and a nice rough-notation underline — the *engine* is good — but a half-empty frame and weak copy. That is the gap between "engine works" and "outcome O3 achieved", and the cause is content/fit logic, not the animation tech.

---

## 3. Rebuild principles

1. **Claims are the unit of truth.** Slides, scripts, quizzes, and review all point at claim IDs with source spans.
2. **Types over repair.** If a layout needs 3–5 steps, the schema for that layout says so and constrained decoding enforces it. Repair code is a smell.
3. **Deterministic where possible, model where necessary.** Layout choice, fit, contrast, loudness, caching, ordering: code. Wording, grouping, naming, explanation: model. Every model call has a deterministic fallback and an eval.
4. **Measure before and after.** Golden corpus + metrics from §1.3 gate every phase.
5. **One source of truth per concept.** One schema for scenes (Pydantic → JSON Schema → TypeScript types), one theme format, one store.
6. **Phases, not locks, for the GPU.** The pipeline is naturally LLM-phase → TTS-phase → render-phase; a scheduler orders phases so VRAM is never contended (instead of runtime arbitration).
7. **Everything expensive is content-addressed; everything user-authored is versioned.**
8. **Local-first, ownership-first.** No cloud by default; project folders are portable (zip them, move them).
9. **Vertical slice first.** A thin end-to-end path in weeks 1–4 beats layer-by-layer perfection.

---

## 4. Target architecture

### 4.1 Process model

```
┌──────────────────────── Desktop shell (Tauri 2, later) ───────────────────────┐
│  Studio UI (React + TS, Vite)  ◄── REST/OpenAPI + SSE ──►  Core service (FastAPI)│
└──────────────────────────────────────────────────────────┬────────────────────┘
                                                           │ job queue (SQLite)
              ┌──────────────┬──────────────┬──────────────┼───────────────┐
              ▼              ▼              ▼              ▼               ▼
        Ingest worker   LLM gateway    Speech worker   Render workers   Packager
        (Docling, OCR,  (Ollama /      (Kokoro ONNX    (Playwright +    (ffmpeg, SCORM,
         own venv)       llama.cpp)     + aligner)      Chromium pool)   HTML player)
                                                                              
 Storage: %LOCALAPPDATA%\DeckCraft\  →  library.db (SQLite WAL) · blobs/ (sha256) · projects/<id>/ · models/ · logs/
```

- **One user-facing process** (the core) that supervises workers as subprocesses. No `start-deckcraft.ps1`; the shell/launcher starts the core, the core starts Ollama if absent and the workers on demand and stops them when idle.
- **Why separate workers?** Heavy native stacks (Docling→torch, onnxruntime, Chromium) are isolated crash/memory domains and can have their own virtualenvs (the project already does this for TTS; generalise it).
- **GPU/RAM scheduler** inside the core: job kinds declare resource needs (`llm` ≈ 4–6 GB VRAM, `render` = CPU, `tts` = CPU, `ingest` = CPU-heavy). It runs phases in an order that keeps the LLM resident while it is needed and never overlaps two heavy CPU jobs beyond configured limits. This replaces `GpuArbiter`.

### 4.2 Languages and why two

- **Python** for everything that touches documents, models, audio and orchestration: that's where Docling, PyMuPDF/pdfium, onnxruntime, Kokoro, faster-whisper, ffmpeg wrappers, Pydantic and the Ollama client are best.
- **TypeScript** for everything that is a browser: the studio UI and the scene engine. The scene engine *must* be web tech (it's HTML/SVG/CSS/GSAP rendered by Chromium).
- A single typed contract between them: Pydantic models → OpenAPI/JSON Schema → generated TS types/clients (`openapi-typescript` + `orval`, or `@hey-api/openapi-ts`). Scene schema is exported as JSON Schema and validated with Zod/Ajv on the TS side so the Python and TS views of a scene cannot silently diverge (today they are linked only by convention: `SlideSpec` dataclass ↔ `MODULE_SLIDES_SCHEMA` dict ↔ `build_scene_payloads` ↔ `slide-engine.js`).

### 4.3 Repository layout

```
deckcraft/
├─ pyproject.toml / uv.lock                 # uv workspace
├─ packages/
│  ├─ core/                                  # Python: deckcraft_core
│  │  ├─ api/            routers (thin), dependency injection, SSE
│  │  ├─ db/             SQLAlchemy models, Alembic migrations, repositories
│  │  ├─ blobs.py        content-addressed store
│  │  ├─ jobs/           queue, workers, resource scheduler
│  │  ├─ ingest/         detect, validate, convert, normalise, outline
│  │  ├─ understand/     claims, glossary, entities, risk ranking
│  │  ├─ plan/           modules, durations
│  │  ├─ storyboard/     layouts, scene filling, validation
│  │  ├─ script/         writing, beats, length control
│  │  ├─ verify/         numbers, modality, entailment, coverage
│  │  ├─ speech/         spoken text, lexicon, providers, alignment, mastering
│  │  ├─ render/         pool, segments, encode, outputs (mp4, html, scorm, pptx, pdf)
│  │  ├─ review/         state machine, comments, audit
│  │  ├─ memory/         edit capture, style guide, few-shot retrieval, eval replay
│  │  ├─ llm/            provider interface, Ollama, prompts, evals
│  │  └─ platform/       windows.py (EcoQoS), paths, browser discovery
│  └─ workers/           speech-worker/, ingest-worker/ (own envs)
├─ web/
│  ├─ scene-kit/         TS: layouts, timeline builder, linter, themes → CSS vars
│  ├─ studio/            React app
│  └─ player/            standalone HTML5 player (reuses scene-kit)
├─ themes/               one JSON/YAML per theme (validated), fonts manifest
├─ eval/                 golden corpus, promptfoo/pytest evals, baselines
├─ tests/                unit · integration · visual · e2e
├─ tools/                dev scripts (fetch_fonts etc.)
└─ docs/                 generated API docs, ADRs (architecture decision records)
```

Use `pnpm` workspaces for `web/*`, `uv` workspace for Python, one root task runner (`just` or `task`) with `just dev`, `just test`, `just eval`, `just package`.

---

## 5. Data model

### 5.1 Storage

- **`library.db`** — SQLite in WAL mode (SQLAlchemy 2 + Alembic migrations): projects, sources, claims, modules, scenes, scripts, audio assets, renders, outputs, jobs, reviews, themes, lexicon, style examples, settings, audit log.
- **`blobs/sha256/ab/cd…`** — immutable content-addressed files: source PDFs, page images, poster PNGs, TTS WAVs, video segments, final outputs. Rows reference blobs by hash. Garbage collection = "delete unreferenced and unpinned blobs older than N days" (this replaces the LRU/age logic in `housekeeping.py` with something provably safe).
- **Paths** from `platformdirs` (`%LOCALAPPDATA%\DeckCraft`), overridable. A **portable project export** (`.deckcraftproj` = zip of the DB slice + referenced blobs) gives backup and sharing for free.
- **Never delete user data automatically** (keep this rule); only unreferenced cache blobs are collectable.

### 5.2 Core tables (columns that matter)

| Table | Key columns | Notes |
|---|---|---|
| `project` | id, name, org_name, series_name, theme_id, orientation, language, settings_json | Replaces "deck" as the top unit; a project can hold many modules |
| `source` | id, project_id, filename, blob_sha, mime, page_count, ingest_version | Original upload, immutable |
| `source_version` | id, source_id, blob_sha, uploaded_at, diff_from_id | Enables policy-update diffing |
| `section` | id, source_version_id, heading_path, level, text, page_start, page_end, bbox_json | Structured, not flat strings |
| `claim` | id, section_id, kind, text, quote, page, char_start, char_end, modality, actor, condition, number_value, unit, risk | `kind ∈ obligation, prohibition, permission, definition, deadline, threshold, step, contact, consequence, example` |
| `module` | id, project_id, number, title, subtitle, summary, status, target_seconds | `status ∈ draft, in_review, approved, stale` |
| `module_claim` | module_id, claim_id, role (`primary/context/excluded`), reason | Coverage accounting |
| `scene` | id, module_id, position, kind, data_json (validated union), theme_overrides_json, rev | `data_json` validated by the Pydantic discriminated union |
| `scene_claim` | scene_id, claim_id | Provenance for each slide |
| `beat` | id, scene_id, position, text, spoken_text, reveal_target (element id or null), pause_after_ms | The script *is* a list of beats, each optionally tied to a visual element |
| `script_revision` | id, scene_id, text, author (`ai/human`), parent_id, created_at, finding_json | History + learning pairs |
| `audio_asset` | id, beat_id, voice, provider, params_hash, blob_sha, duration_ms, words_json | Word timings from alignment |
| `render` | id, module_id, spec_hash, state, blob_sha, log_blob | Segment/final video records |
| `output` | id, module_id, kind (`mp4/srt/vtt/html/scorm/pptx/pdf/transcript`), blob_sha | Deliverables |
| `job` | id, kind, project_id, state, payload_json, progress, message, error, attempts, locked_by, heartbeat_at | Durable queue; survives restart |
| `review_event` | id, module_id/scene_id, actor, action, comment, source_version_id, created_at | Approval audit trail |
| `lexicon_entry` | id, project_id?, word, respelling or phonemes, scope | Pronunciation |
| `style_example` | id, project_id?, scene_kind, draft, final, origin, embedding | Few-shot + eval + future fine-tune |
| `theme` | id, slug, source, json, preview_blob | One validated format |
| `audit_log` | ts, actor, action, entity, before/after | Everything user-initiated |

### 5.3 The scene contract (one definition, typed)

```python
class Beat(BaseModel):          # a sentence of narration, optionally anchored to an element
    text: str
    reveal: ElementRef | None

class CoverScene(BaseModel):  kind: Literal["cover"]; eyebrow: str; title: str; subtitle: str
class StatScene(BaseModel):   kind: Literal["stat"]; value: str  # must contain a digit and appear in a cited claim
                              label: str; body: str
class ProcessScene(BaseModel):kind: Literal["process"]; steps: Annotated[list[Step], Len(3, 5)]
class CompareScene(BaseModel):kind: Literal["compare"]; do: Annotated[list[str], Len(2,4)]; dont: Annotated[list[str], Len(2,4)]
class ScenarioScene(BaseModel):kind: Literal["scenario"]; situation: str
                              options: Annotated[list[str], Len(2,3)]; answer_index: int; why: str
# ... cards, checklist, statement, myth_fact, spotlight, escalation, takeaways, quote, timeline, split, image
Scene = Annotated[Union[CoverScene, StatScene, ...], Field(discriminator="kind")]
```

Every field carries length limits derived from the layout's real capacity, so "too long for the card" is rejected at the schema, not discovered in a render. `answer_index < len(options)` and similar invariants are `model_validator`s. This is what lets `repair_specs` shrink to a small, tested `fallback_scene(kind, claims)`.

---

## 6. Pipeline, stage by stage

Each stage lists: purpose · tools · key functions · failure/fallback · how it's measured.

### S1 — Ingest

**Purpose:** turn any input into a structured `SourceDocument` (sections with heading paths, tables, page/bbox provenance).

**Inputs:** PDF (text and scanned), DOCX, PPTX, Markdown, HTML, pasted text, URL (optional). *Adding DOCX matters: policies are authored in Word.*

**Tools:**
- **Docling** (MIT, local) for layout-aware conversion of PDF/DOCX/PPTX/HTML to a structured document with headings, lists, tables, reading order, optional OCR. Run in its own worker env; CPU only so it never fights the LLM for VRAM.
- **pypdfium2** (permissive) as the lightweight fallback: per-page text with font size/weight → heading detection by typography instead of by regex; also renders page images for the source viewer.
- **RapidOCR (ONNX)** or **Tesseract** for scanned pages (currently rejected outright: "No selectable text found").
- **python-docx / python-pptx / markdown-it-py** for direct formats.
- *Avoid PyMuPDF/pymupdf4llm unless you accept AGPL.*

**Key functions:**
- `detect_type(path) -> SourceKind` (magic bytes, not extension)
- `validate_upload(path, limits) -> None` (size, zip-bomb ratio/entries, path traversal — port from `ingest.py`)
- `convert_to_document(path, *, ocr: bool) -> SourceDocument`
- `detect_running_furniture(pages) -> set[Block]` — headers/footers/page numbers found by *repetition across pages* (position + text similarity), replacing `_HEADER_FOOTER_RE`
- `strip_toc(doc) -> SourceDocument` (TOC detected by dot-leaders *and* by heading/page-number density)
- `reflow_word_per_line(page) -> list[str]` (port the existing word-per-line fix; it handles a real failure mode)
- `build_outline(blocks) -> list[Section]` (heading levels from typography + numbering patterns as a *signal*, not a rule)
- `split_oversized(section, max_tokens) -> list[Section]` (token-based, sentence-aware)
- `extract_tables(doc) -> list[Table]` → table-to-prose rendering for the LLM
- `fingerprint(doc) -> str` and `diff_versions(a, b) -> SourceDiff` (section-level: added / removed / changed with similarity)

**Fallback:** pypdfium2 path if Docling fails or isn't installed; pasted text always works.
**Measured by:** section-boundary F1 against hand-labelled golden PDFs; % of pages with recovered text; furniture leak rate.

### S2 — Understand (claim extraction) — *the new core*

**Purpose:** convert prose into a list of atomic, cited, typed claims. Everything downstream consumes claims.

**Tools:** local LLM with constrained JSON (Pydantic schema), small context per call (one section at a time); embeddings model for dedupe/linking; regex only as a *cross-check* (modal verbs, numbers, dates).

**Key functions:**
- `extract_claims(section, glossary) -> list[Claim]` — each with `quote` (verbatim substring), `page`, char span, `kind`, `modality ∈ {must, must_not, should, may, info}`, `actor`, `condition`, `number+unit`
- `validate_claim_quote(claim, section) -> bool` — the quote must literally appear in the section; reject hallucinated quotes (cheap and powerful)
- `dedupe_claims(claims) -> list[Claim]` (embedding + normalised-text similarity)
- `build_glossary(doc) -> dict[str, Definition]` (defined terms, acronyms)
- `link_cross_references(claims, doc)` ("see section 7.4")
- `rank_risk(claims) -> list[Claim]` (prohibitions, legal/safety, deadlines, penalties first → coverage priority)
- `detect_audience_roles(doc) -> list[Role]`
- `detect_language(doc)`, `detect_locale_spelling(doc)` (AU/UK/US) so generated copy matches the source's English — fixes the "Prioritize" issue

**Fallback:** sentence-level rule extractor (modal verbs + numbers) so the pipeline still runs without a model, with lower richness.
**Measured by:** claim recall against golden annotations; % of claims whose quote validates (target 100 %).

### S3 — Plan modules

**Purpose:** group claims/sections into bite-sized modules with durations and titles.

**Tools:** embeddings (`embeddinggemma` / `nomic-embed-text` / `bge-m3` via Ollama or ONNX) for topic segmentation; token budgets; LLM only for titles/subtitles/summaries.

**Key functions:**
- `segment_topics(sections, target_tokens) -> list[Group]` (adjacent-section similarity + heading structure; no hard-coded `7.x`)
- `plan_modules(doc, claims, constraints) -> ModulePlan` (constraints: target seconds, max slides, orientation)
- `name_module(group) -> (title, subtitle, summary)`
- `estimate_duration(module, wpm) -> seconds`
- `merge_modules(a, b)`, `split_module(m)` (UI operations; recompute coverage)
- `coverage(plan) -> Report` — every claim is `primary`, `context`, or `excluded` (with reason); unassigned `must/must_not` claims are errors

**Fallback:** deterministic grouping by heading tree and token budget.
**Measured by:** module size variance, coverage %, reviewer merge/split rate (a proxy for bad plans).

### S4 — Storyboard (scenes)

**Purpose:** choose a layout per slide and fill it with content *from specific claims*.

**Key functions:**
- `choose_sequence(module, profile, previous) -> list[LayoutKind]` — port the idea of `plan_layouts` (content signals → layouts, scenario slot guaranteed, variety vs neighbouring modules), but driven by claim kinds (`step` claims → process; `prohibition` + `permission` → compare; `threshold/deadline` → stat; `contact` → escalation; `definition` → statement/spotlight)
- `fill_scene(kind, claims, locale, constraints) -> Scene` — **one small constrained call per scene** using that kind's Pydantic schema (3–6 claims in context, not the whole module)
- `fit_content(scene, layout_capacity) -> Scene` — shorten/split by rule before ever touching a model; if a scene overflows, split into two scenes
- `write_scenario(claims) -> ScenarioScene` — scenario options must each cite a claim; wrong options are written as *plausible violations* of a cited rule (this directly addresses the known "model writes rationales as options" weakness)
- `fallback_scene(kind, claims) -> Scene` — deterministic builder, tested
- `validate_scene(scene, claims) -> list[Finding]` (schema, capacity, provenance, locale spelling, duplicate text across slides)

**Measured by:** schema-valid-on-first-try rate (target > 95 % with the chosen model), % scenes with ≥ 1 cited claim (target 100 %).

### S5 — Script (narration)

**Purpose:** natural spoken narration, tied to what's on screen, at the right length.

**Key functions:**
- `write_script(scene, claims, style_guide, target_seconds) -> list[Beat]` — generate narration as *beats* (sentences), each optionally referencing a visual element (card 2, step 3, the stat). Reveals become anchored to words rather than to a guess.
- `length_control(beats, target_seconds)` — measured in *syllable-estimated seconds*, one resize turn max (port `length_off` / `length_fix_messages`)
- `apply_style_guide(prompt, guide)` — the user-editable "house style" text (tone, forbidden phrases, spelling variant, how to address the viewer)
- `rewrite_selection(text, instruction)` — UI "rewrite this sentence shorter / plainer / more formal"
- `to_display_script(beats)` / `to_spoken(beats, lexicon)` — keep "display ≠ spoken"

**Rule change vs today:** write the narration *first* (from claims), then bind visuals to beats — not slides-and-script from one JSON blob. Slides become a visual summary of what the voice says, which is how good explainer videos are built.

### S6 — Verify (the compliance layer)

**Purpose:** catch unfaithful content before a human wastes time on it, and tell the reviewer exactly where to look.

**Key functions (cheap → expensive):**
1. `check_numbers(text, claims)` — numbers/dates/currency/percent each map to a *cited claim*, not just "present somewhere"
2. `check_modality(sentence, claim)` — flags `must`→`should`/`may` drift and negation flips
3. `check_entities(text, source)` — roles/names/phone numbers/URLs exist in the source
4. `retrieve_support(sentence, claims, k)` — embedding retrieval
5. `entails(premise, hypothesis) -> {entail, neutral, contradict}` — a small NLI cross-encoder (ONNX, CPU) is fast and independent of the generator; LLM-as-judge only for the borderline band
6. `coverage_report(module)` — which must-claims no beat touches
7. `locale_lint(text, locale)` — spelling variant consistency
8. `Finding(severity, kind, span, claim_ids, suggestion)` → shown inline in the editor; blocking findings prevent *approval*, not editing

**Measured by:** precision/recall on a hand-seeded set of corrupted scripts (swap a number, flip a modality, add an invented rule). This test set is the single most valuable asset for trusting the product.

### S7 — Speech

**Tools:** **Kokoro-82M ONNX** (Apache-2.0) stays the default: fast on CPU, offline, good English. Wrap in a `TTSProvider` protocol so a heavier model can be dropped in when VRAM is free (the LLM phase is over by then). Keep `speech_text.py` and its golden tests essentially verbatim.

**Key functions:**
- `to_spoken(display, lexicon) -> str` (port) · `chunk_script(text) -> list[str]` (port)
- `Lexicon.resolve(word) -> respelling | phonemes` — per-project pronunciation dictionary with a UI ("hear it", "add word"), passed through Kokoro's G2P (misaki) with inline phoneme overrides where the engine supports them
- `TTSProvider.voices() / synthesize(text, voice, speed) -> Audio / capabilities`
- `align_words(audio, text) -> list[WordTiming]` — forced alignment (`ctc-forced-aligner` or faster-whisper word timestamps) so beats and subtitles have word-accurate times
- `ensure_beat_audio(beat, voice) -> AudioAsset` (content-addressed; port `ensure_slide_audio` logic incl. in-flight de-duplication)
- `assemble_scene_audio(beats) -> Audio` — lead-in/pauses/tail as today, but pause lengths come from beat data
- `master_audio(track) -> Audio` — `loudnorm` two-pass to −16 LUFS, optional gentle compressor/de-esser, mono→stereo explicitly, 48 kHz
- `check_intelligibility(audio, text) -> wer` — faster-whisper round trip; flags garbled or mispronounced chunks automatically
- `music_bed(track, music, duck_db)` — optional `sidechaincompress` ducking; royalty-free library bundled, user can add own

**Voices:** Kokoro ships en-US/en-GB (no en-AU). If the audience is Australian, an en-GB voice with an AU-aware lexicon is the realistic option; verify by ear on the golden corpus. Kokoro also covers several other languages, which helps O7/multi-language (§12).

### S8 — Render

**Purpose:** turn scenes + word timings + theme into frames, then a master video.

**Design:** keep the seekable-timeline principle exactly. Formalise it as: `buildTimeline(scene, beats, theme, orientation) -> gsap.Timeline` (pure; no globals), `stage.seek(t)` (sync), `stage.ready()` (fonts loaded). The exporter *only* calls `seek`.

**Key functions (Python side):**
- `RenderPool` (port `MotionPool`): N workers, one Chromium + stage page each, reused; add **frame-range sharding** so a single long scene can use all workers (today parallelism is per-segment only)
- `render_scene(spec) -> Segment` — frames via CDP screenshot → ffmpeg stdin; hash the **scene+audio+theme+renderer version** for the cache key (replace manual `SEGMENT_VERSION` with a hash of the scene-kit build and ffmpeg args so cache invalidation can't be forgotten)
- `poster(scene, t=hero)` / `thumb(scene)` (loops off, as today)
- `encode_args(profile) -> list[str]` with explicit `-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv` and a proper RGB→YUV conversion (`scale=…:out_color_matrix=bt709:out_range=tv`) to fix P8
- `concat(segments) -> mp4` (stream copy, as today) + `add_chapters(mp4, chapters)` (ffmetadata) + `add_subtitles(mp4, srt)`
- `intro_outro(project) -> Segment` (optional branded cards)
- `dedupe_static_frames(frames)` (ffmpeg `mpdecimate` or hash compare) — optional speed-up when a scene is in a static hold
- `unthrottle_process_tree(pid)` (port `winperf.py` into `platform/windows.py`, behind an interface)
- Browser: **Playwright's bundled Chromium** installed into the app's data dir (no dependency on the user's Chrome/Edge; removes the hard-coded install paths and `--no-sandbox` need for ordinary runs)

**Alternatives considered:** Remotion (nice DX, but a commercial licence is required for companies above a small size — verify current terms — and it's the same screenshot-per-frame approach underneath); Motion Canvas (MIT, code-driven, less suited to data-driven templates); native Skia/Rust renderer (fast, but you'd lose HTML/CSS/SVG/fonts and the 34-theme investment). The seek-and-screenshot approach is already competitive at 19.5 ms/frame with 3 workers and is the least risky path.

### S9 — Outputs

| Output | How | Why |
|---|---|---|
| **MP4** (H.264/AAC, 720p/1080p, portrait/landscape) | as above | Sharing, WhatsApp/Teams |
| **SRT + WebVTT + transcript.md** | from word timings (line-balancing code from `export.py` `split_cue/two_lines` ports directly) | Accessibility |
| **Interactive HTML5 player** | the same scene-kit + audio + cues, played live in the browser, with real quiz interactions | Tiny files; no render step; scenario slides become real questions |
| **SCORM 1.2 / 2004 and cmi5/xAPI package** | HTML player + `imsmanifest.xml` + runtime wrapper (e.g. `scorm-again`); quiz score + completion reported | The way compliance training is usually delivered and tracked |
| **Editable PPTX with notes** | python-pptx (port `render_module_pptx`, but drive it from scenes and remove hard-coded brand strings) | Hand-off to people who want PowerPoint |
| **PDF handout / one-page summary** | HTML→PDF via the same Chromium | Reference sheet for employees |
| **Source-cited review report** | claims ↔ slides ↔ beats table | Evidence for auditors |

### S10 — Quality gates (automatic, before "Ready to review" and before "Export")

`lint_scene` (DOM overflow, min font size, contrast ≥ WCAG AA, empty-area ratio, text-fit), `verify_module` (§S6), `audio_qc` (loudness, clipping, WER), `video_qc` (duration vs audio, black frames, colour tags via ffprobe), `a11y_qc` (captions present, reduced-motion behaviour in the HTML player). A failed gate is a *finding with a suggested fix*, not an exception.

---

## 7. The scene engine and design system

### 7.1 `scene-kit` (TypeScript package)

- **Layouts as components:** one React component per layout (`Cover`, `Statement`, `Stat`, `Cards`, `Process`, `Checklist`, `Compare`, `Scenario`, `MythFact`, `Spotlight`, `Escalation`, `Takeaways`, plus new `Quote`, `Timeline`, `Split`, `ImageScene` for narrating existing slides). Components are *pure functions of (scene, theme, orientation)*.
- **Timeline builder:** per layout, a function that registers element anchors and returns tweens; a generic `applyBeats(timeline, beats, wordTimings)` places reveals at the word the beat references. Port the *entrance catalogue* (`slide-up`, `pop-spring`, `crisp-fade`, `snap-left`, `step`, `wipe`, `zoom`, `drop`) and the **loop layer** (finite yoyo/repeat tweens computed from remaining time, spotlight synced to cues) from `slide-engine.js` — that code encodes hard-won lessons (no `@keyframes`, no `repeat:-1`, end loops at rest).
- **Fit:** `fitText(el, {min,max})` (binary search on font size within container), `balanceHeights`, **vertical distribution** (centre/spread content in the frame; scale type up when content is short). This is the real fix for the empty-lower-half problem and replaces ad-hoc CSS tweaks per layout.
- **Linter:** `measureOverflow(root)`, `minFontPx(root)`, `contrastReport(root)` (compute actual foreground/background including gradients by sampling), `emptyAreaRatio(root)`; exposed to Python via `page.evaluate` and returned as `Finding`s.
- **Visual regression:** Playwright screenshots at fixed times for each layout × orientation × 3 themes, compared with `pixelmatch`/Playwright's built-in snapshot assertions.

### 7.2 One theme format

Collapse today's three layers (design.md → theme JSON, `profiles.py`/`template_profiles.py`, generated `slide.md`) into **one validated theme file**:

```yaml
id: emerald-editorial
tokens:            # W3C Design Tokens–style, compiled to CSS custom properties
  color: { bg, ink, primary, secondary, tertiary, card, muted }
  font:  { heading, body, mono }      # families from the bundled manifest
  radius: sharp|rounded|pill
  spacing/scale: …
composition:       # what `profiles.py` calls a profile
  cover: poster|band|split|center
  header: kicker|pill
  cards: boxed|ruled|tinted
  list: check|dash|number
  footer: bar|hairline
  backdrop: orbs|grid|plain
  prefer: [cards, stat]   avoid: [myth_fact]
decor: [ {shape, zone, size, color, entrance, loop, on} … ]
motion: { energy, entrance, accent_effect, stagger_s }
voice: { tone_guidance, forbidden_phrases }       # feeds the script writer
```

Keep the 34 templates' hand-authored data — convert once with a script, then delete the generated-duplicate mechanism and its drift test. Extraction from an uploaded brand `design.md` / PPTX / HTML (`design.py`, `template_import.py`) stays as importers that *emit this one format*; run them in a sandboxed iframe/worker since they read untrusted files.

**Fonts:** keep bundling (offline requirement); source from `@fontsource` variable fonts to cut file count; the manifest stays. Add a **contrast-safe palette check** to theme import so a brand palette that can't meet AA is repaired (nudge lightness) with a visible warning.

### 7.3 GSAP licence note

`CAPABILITIES.md` says GSAP is "free for commercial use only while end users are not charged." My understanding is that GSAP (including its former paid plugins such as SplitText/DrawSVG) became free for commercial use in 2025 under Webflow's licence, with a restriction on building products that compete with Webflow's visual animation tooling. *I have not re-read the current licence text; please verify it before relying on either statement.* If it ever mattered, the timeline layer is isolated enough to swap (Web Animations API / Motion One / anime.js).

---

## 8. The studio UI

**Stack:** React 19 + TypeScript + Vite · Tailwind v4 + Radix primitives (shadcn/ui style) · TanStack Query (server state) · Zustand (local UI state) · TanStack Router · dnd-kit (reorder) · CodeMirror 6 or TipTap (script editor with inline markers) · pdf.js (source viewer) · wavesurfer.js (waveform/timeline) · Zod (runtime validation) · Vitest + Testing Library · Playwright (e2e). Svelte 5 would also be a fine choice; React is recommended for ecosystem and AI-assistant fluency.

**Screens:**

1. **Library / Projects** — cards with status chips (draft · in review · approved · stale), search, import project zip.
2. **New project wizard** — upload → *understanding report* (pages, sections, claim counts, scanned pages, warnings) → module plan → theme → language/voice → generate. Shows coverage live.
3. **Module planner** — draggable modules; merge/split; coverage bar; "excluded claims" drawer with reasons; duration estimate.
4. **Review workspace (the main screen)** — three synchronised panes: **Source** (pdf.js, cited spans highlighted) · **Slide** (live scene preview, directly editable fields with schema-driven forms) · **Script** (beats list, inline findings, per-beat play, per-beat reveal target picker). Per-slide *Approve*, comments, status. Keyboard-first (←/→ slide, Space play, `A` approve, `/` find).
5. **Timeline view** — waveform per scene, beat boundaries, reveal anchors; drag to adjust pauses; scrub the actual scene at any time.
6. **Theme browser / brand editor** — what exists now (search, filters, specimens) plus live contrast/AA readout and "apply to project".
7. **Voice & pronunciation** — audition voices on *your own* text; lexicon editor with "hear it".
8. **Export** — outputs checklist (MP4/HTML/SCORM/PPTX/PDF), resolution/orientation, burned vs soft captions, blocking findings list, preview, open folder.
9. **Updates** — upload revised source → diff view (added/removed/changed sections) → affected modules flagged *stale* → regenerate-only-stale → re-approve.
10. **Diagnostics** — models (install/pull with progress), VRAM/RAM/CPU, disk usage, cache GC, logs, "copy support bundle".
11. **Style memory** (later) — house style guide editor, learned preferences with on/off, eval results.

**UX rules:** every long operation is a job with progress, cancel, and a retry that resumes; nothing blocks the UI; everything the AI wrote is visibly marked until a human touches or approves it; every finding is clickable and jumps to the span; no destructive action without undo or a confirmation that names what will be lost.

---

## 9. The AI layer

### 9.1 Provider interface

```python
class LLM(Protocol):
    async def generate(self, model: str, messages: list[Msg], schema: type[BaseModel] | None,
                       *, options: GenOptions, images: list[bytes] | None = None) -> BaseModel | str
    async def embed(self, model: str, texts: list[str]) -> list[list[float]]
    async def capabilities(self, model: str) -> Caps         # vision, thinking, ctx, size
    async def ensure_loaded(self, model: str) / unload(self, model: str)
```

Implementations: **Ollama** (default; structured outputs via JSON Schema), **llama.cpp server** (GBNF grammars, finer control), and an **opt-in cloud provider** (**dropped by decision, see §18**; you chose local-only). Structured output via Pydantic everywhere (an `instructor`-style helper or a small in-house wrapper: validate → on failure, one repair retry that feeds the validation error back).

### 9.2 Model roster for an 8 GB VRAM / 16 GB RAM laptop

I can't benchmark these on your machine from here and model availability moves fast, so treat this as a *shortlist to run through the eval harness*, not a decision:

| Role | Candidates to benchmark (Ollama names may vary) | Notes |
|---|---|---|
| Claims, scenes, scripts (text) | Qwen3 8B (Q4) · Gemma 3 4B (current) · Gemma 3 12B (QAT, partial offload) · Phi-4-mini · Llama 3.x 8B | 8B-class Q4 fits in ~5–6 GB; test structured-output reliability and instruction-following on *your* claim/scene prompts |
| Vision (optional design/image review) | Qwen2.5-VL / Qwen3-VL small · Gemma 3 4B (vision) | Demoted from "gatekeeper" to "optional second opinion" |
| Embeddings | `embeddinggemma`, `nomic-embed-text`, `bge-m3` | Small, CPU-friendly |
| NLI / entailment | a DeBERTa-v3 MNLI cross-encoder exported to ONNX | CPU, 0 VRAM, independent of the generator |
| ASR for alignment/QC | faster-whisper `small.en` / `base.en` | CPU; also used for TTS intelligibility checks |

**Selection method:** run each candidate over the golden corpus; score schema-valid rate, claim recall, faithfulness, scene fit, script length accuracy, time. Pick per role; store the result in `eval/baselines/`. Re-run when a new model appears.

### 9.3 Prompting practice

- Small contexts, one job per call (extract → plan → fill scene → write beats → judge). The current single call with a 6,000-char source slice and 12 layout descriptions is the opposite.
- Few-shot examples come from the *same scene kind* and the project's approved items (retrieved by embedding), not from anywhere.
- Temperature low for extraction (0–0.2), moderate for wording (0.4–0.6); fixed seeds in evals.
- Prompts live as versioned templates in `llm/prompts/`, each with an eval case. Prompt version is stored with every generated artefact.
- Cache LLM calls by (model, prompt-hash, params) in the blob store — makes re-runs and tests free and reproducible.
- Keep one `num_ctx` per loaded model (the 7 s reload finding is real) and `keep_alive` through a phase; unload at phase end when TTS/render need the RAM.

### 9.4 "Learning," right-sized

- **From day one, capture** `(scene_kind, ai_draft, human_final, origin, model, prompt_version)` in `style_example` and `script_revision`. It's cheap and is the raw material for everything later.
- **Ship first:** an editable **house style guide** (plain text: tone, vocabulary, words to avoid, spelling variant) injected into prompts, plus embedding-retrieved few-shot examples. Predictable and explainable beats mined "avoid" phrases.
- **Keep:** `edit_ratio` as the headline quality metric and the replay-evaluation idea (it's good).
- **Defer:** DPO export, LoRA fine-tune, design-lesson mining until there are ~200 real pairs. If/when that point comes, try DSPy-style prompt optimisation before fine-tuning.

---

## 10. Audio/video recipes (concrete)

```text
# Loudness-normalised, explicitly stereo, 48 kHz speech (two-pass loudnorm in practice)
-af "highpass=f=70,acompressor=threshold=-18dB:ratio=2.5:attack=15:release=250,
     loudnorm=I=-16:TP=-1.5:LRA=9,aresample=48000" -ac 2

# Music bed with ducking under narration
[music]volume=0.18[m];[m][voice]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[bed];
[voice][bed]amix=inputs=2:normalize=0

# Colour-correct H.264
-vf "scale=W:H:flags=lanczos:out_color_matrix=bt709:out_range=tv,format=yuv420p,setsar=1"
-c:v libx264 -preset veryfast -crf 20 -profile:v high
-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv -movflags +faststart

# Chapters per scene (ffmetadata), subtitles as mov_text + sidecar SRT/VTT
```

NVENC stays optional (probe at runtime, as today); libx264 is fast enough for this content. Use the ffmpeg build you ship consciously: the `imageio-ffmpeg` binary is a GPL build with libx264 — fine for internal use; if the app is ever *distributed*, review the licence obligations or switch to a build you control.

---

## 11. Platform, packaging and operations

- **Distribution:** Phase 1–3 as a local web app launched by one command; Phase 6 wraps it in **Tauri 2** (small installer, WebView2) with sidecars: core service, ffmpeg, Ollama (detect existing or offer install), Chromium for rendering. An MSI/NSIS installer, per-user install, no admin rights. (Electron is the alternative if you want to render frames inside the shell's own Chromium and avoid a separate Chromium download; it is larger.)
- **Model manager:** first-run screen listing required models with size, licence, and a progress bar; verifies SHA-256; offers a "low VRAM" preset.
- **Logging:** `structlog` → rotating JSON log files per component; job logs stored with the job; "Copy support bundle" zips logs + versions + system info, with source text excluded by default.
- **Crash safety:** durable job queue (`attempts`, `heartbeat_at`, resume-on-boot), idempotent stages (content-addressed outputs make retry free), atomic blob writes.
- **Backups:** one-click "Export project" and a scheduled optional backup folder.
- **Updates:** none automatic by default; "Check for updates" against a static manifest you host (or none at all for an internal tool).
- **Security:** keep loopback + Host/Origin guard; generate a per-launch API token so other local processes can't drive the API; sandbox imported template HTML in a null-origin iframe; never `eval` imported content; cap upload and unzip limits (port); `--no-sandbox` only as an opt-in fallback.
- **Privacy:** no telemetry. Cloud LLM provider (if enabled) is per-project, off by default, with an explicit data-leaves-device notice.
- **Config:** typed `Settings` (Pydantic Settings) from file + env; all paths via `platformdirs`; one place for limits.
- **Cross-platform:** isolate Windows specifics in `platform/windows.py`; macOS/Linux then largely "just work" (Chromium, ffmpeg, onnxruntime, Ollama all exist there).

---

## 12. Features worth adding (ranked by value ÷ effort, with the reason)

| Rank | Feature | Why now | Effort |
|---|---|---|---|
| 1 | Claim-level provenance + verifier + coverage report | The product's trust story; makes review fast | Large (core of Phase 1–3) |
| 2 | Source ↔ slide ↔ script review screen with approvals/audit | Compliance value; turns the app into a workflow | Medium |
| 3 | Slide content editing (schema-driven forms) | Today impossible (P5) | Small–Medium |
| 4 | Deterministic layout linter + fit/vertical-distribution | Fixes the visible quality gaps (P6) cheaply | Medium |
| 5 | Audio mastering + colour tags + chapters | Perceived quality; 1–2 days | Small |
| 6 | Quiz generation (from claims) + HTML player + SCORM/cmi5 | Where compliance training actually lives | Medium–Large |
| 7 | Policy update workflow (diff → stale → regenerate → re-approve) | O5; unique value vs generic video tools | Medium |
| 8 | DOCX + scanned-PDF ingestion | Policies are Word documents; scans are common | Small–Medium (via Docling) |
| 9 | Word-level timing (captions highlight, anchored reveals) | Polish; enables karaoke captions | Medium |
| 10 | Multi-language: translate script (local model) + multilingual Kokoro voices | Multilingual workforces | Medium (quality needs human review per language) |
| 11 | Music bed with ducking, intro/outro cards, transitions | Production polish | Small–Medium |
| 12 | Project export/import, backups | Safety, sharing | Small |
| 13 | Pronunciation editor UI | Already on the roadmap (#11) | Small |
| 14 | Style memory + eval replay UI | Once data exists | Medium |
| 15 | "Narrate my existing deck" as a first-class mode (`ImageScene` + notes → beats) | Preserves the original product line without a separate pipeline | Medium |

---

## 13. Testing and evaluation strategy

**Pyramid:**

1. **Unit (fast, hermetic):** pure functions — speech normalisation (port the 26 golden tests), cue splitting, claim quote validation, schema invariants, theme normalisation, hashing/cache keys. *No test may touch the real data directory* (fixes P13): all paths injected via a `tmp_path` `Settings`; add a CI check that fails if the real `%LOCALAPPDATA%\DeckCraft` is modified.
2. **Contract:** OpenAPI snapshot test; generated TS types compile; Pydantic scene JSON Schema ↔ Zod parity test.
3. **Integration:** real SQLite, real ffmpeg, fake LLM/TTS providers (as today), job-queue crash/resume tests.
4. **Visual:** scene-kit screenshots per layout × orientation × theme at fixed times; failure shows a diff image. Add the *defects I found* as permanent cases (dark-on-dark badge, empty lower half).
5. **Eval (non-deterministic, run on demand/nightly):** golden corpus through the real pipeline with the real model → the §1.3 metrics, plus **adversarial verifier tests** (corrupt a script in known ways; the verifier must flag it). Use `promptfoo` or a small pytest-based harness; keep results in `eval/baselines/*.json` and fail the run on regression beyond a tolerance.
6. **End-to-end:** Playwright drives the real studio through upload → review → export on a short fixture; asserts an MP4 with the expected duration, loudness, colour tags, captions.

**Golden corpus:** 5–8 real (or anonymised) policy PDFs/DOCX chosen to cover: clean Word export; word-per-line export (your current oddity); multi-column; table-heavy; scanned; very long handbook; very short single policy; non-English. Hand-label headings and the top ~30 claims per doc once; this is the single most valuable asset in the project.

**Tooling:** `ruff` (lint+format), `pyright` (strict on `core`), `pytest` + `pytest-asyncio` + `hypothesis` (properties for chunking/cue splitting/number parsing), `biome` or ESLint+Prettier, `vitest`, Playwright, `pre-commit`, GitHub Actions on `windows-latest` (or a local `just ci`).

---

## 14. Licensing and risk register

| Item | Concern | Action |
|---|---|---|
| GSAP | Local notes and my understanding disagree (§7.3) | Read the current licence; keep the timeline layer swappable |
| PyMuPDF / pymupdf4llm | AGPL | Avoid; use pypdfium2 / Docling |
| Remotion | Commercial licence thresholds | Not recommended here; don't adopt without reading terms |
| XTTS-v2 and some other TTS | Non-commercial or restrictive model licences | Stay with Kokoro (Apache-2.0) unless a model's licence is verified |
| Model licences (Gemma, Llama, Qwen) | Use-restriction clauses differ | Record each model's licence in the model manager; surface it |
| imageio-ffmpeg build | GPL (libx264) | Fine internally; revisit before distribution |
| Fonts | 57 OFL families | Keep the licence files with the bundle (already done for engine fonts); generate a NOTICE file in CI |
| Imported templates (`beautiful-html-templates`, MIT) | Attribution | Keep `LICENSE`; list in NOTICE |
| LLM hallucinating policy | Legal exposure if wrong guidance reaches employees | Approvals + audit trail + verifier; never auto-publish; footer "Approved by X against policy vN" optional on the final card |
| Docling weight (torch) | ~GBs, slow install | Separate worker env; lazy install; pypdfium2 fallback |
| Quality of 4–8B models | Might not reach acceptable faithfulness | Eval first; optional cloud tier behind explicit consent |
| Scope creep | 15 features above | Vertical slice + metrics gate each phase |
| Migration | Existing decks/templates/learning data | Importer in Phase 4 (see §16) |

---

## 15. Bill of materials

| Concern | Pick | Alternatives | Licence (verify) |
|---|---|---|---|
| Python env | Python 3.12 + **uv** | poetry | – |
| API | **FastAPI** + Uvicorn | Litestar | MIT/BSD |
| Models / validation | **Pydantic v2** | msgspec | MIT |
| DB | **SQLite (WAL)** + **SQLAlchemy 2** + **Alembic** | SQLModel, Peewee | public domain / MIT |
| Jobs | own SQLite-backed queue + asyncio + subprocess workers | Huey (SQLite), Dramatiq | – |
| Doc conversion | **Docling** | Unstructured, Marker (GPL), MinerU | MIT |
| PDF raster/text | **pypdfium2** | pdfplumber (MIT), pypdf (BSD) | BSD/Apache |
| OCR | **RapidOCR (ONNX)** | Tesseract | Apache-2.0 |
| DOCX/PPTX | python-docx, python-pptx | – | MIT |
| LLM runtime | **Ollama** | llama.cpp server, LM Studio | MIT |
| Structured output | Pydantic + Ollama JSON Schema | instructor, outlines | MIT/Apache |
| Embeddings | embeddinggemma / nomic-embed-text / bge-m3 | – | check each |
| NLI | DeBERTa-v3 MNLI cross-encoder (ONNX) | LLM judge | check model card |
| TTS | **Kokoro-82M ONNX** (`kokoro-onnx`) | Piper, Chatterbox, F5-TTS | Apache-2.0 |
| G2P | misaki / espeak-ng | – | check |
| ASR / alignment | **faster-whisper**, `ctc-forced-aligner` | WhisperX | MIT |
| Audio | ffmpeg (`loudnorm`, `sidechaincompress`), `pyloudnorm` for QC, `soundfile` | – | LGPL/GPL |
| Render | **Playwright** + bundled Chromium | Puppeteer, Remotion (licence) | Apache-2.0 |
| Animation | **GSAP** (+ Rough Notation, SplitText/DrawSVG) | Motion, anime.js | verify |
| Scenes UI | React 19 + TS in `scene-kit` | Svelte/Solid | MIT |
| Studio UI | React + Vite + Tailwind v4 + Radix/shadcn | Svelte 5 | MIT |
| State/data (UI) | TanStack Query, Zustand, TanStack Router | – | MIT |
| Editor | CodeMirror 6 or TipTap | Lexical | MIT |
| PDF viewer | pdf.js | – | Apache-2.0 |
| Waveform | wavesurfer.js | peaks.js | BSD |
| Drag/drop | dnd-kit | – | MIT |
| Types bridge | openapi-typescript + orval / hey-api | – | MIT |
| Colour | `culori` (contrast, OKLCH) | chroma-js | MIT |
| Icons | Lucide | – | ISC |
| Fonts | `@fontsource/*` variable fonts | self-hosted woff2 (today) | OFL |
| Packaging | **Tauri 2** | Electron | MIT/Apache |
| SCORM | `scorm-again` runtime + generated manifest | pipwerks | MIT |
| Logging | structlog | loguru | MIT |
| Paths | platformdirs | – | MIT |
| Quality | ruff, pyright, pytest, hypothesis, vitest, Playwright, pre-commit, just | mypy, tox | – |
| Eval | promptfoo or pytest harness | Ragas-style custom | MIT |

---

## 16. Function catalogue

*Names are proposals. "Port" = carry over from the current code with its tests; "New" = doesn't exist today.*

### `core/ingest`
`detect_type` · `validate_upload` *(port: `ingest.py`)* · `convert_to_document` · `detect_running_furniture` · `strip_toc` · `reflow_word_per_line` *(port)* · `build_outline` · `split_oversized` *(port idea)* · `extract_tables` · `fingerprint` · `diff_versions` *(New)*

### `core/understand`
`extract_claims` · `validate_claim_quote` · `dedupe_claims` · `build_glossary` · `link_cross_references` · `rank_risk` · `detect_audience_roles` · `detect_locale_spelling` *(all New)* · `rule_based_claims` (fallback, New)

### `core/plan`
`segment_topics` · `plan_modules` *(port idea of `_deterministic_plan`, drop customer-specific rules)* · `name_module` · `estimate_duration` · `merge_modules` · `split_module` · `coverage`

### `core/storyboard`
`choose_sequence` *(port: `plan_layouts`, `content_signals`, `_favour`)* · `fill_scene` · `fit_content` · `write_scenario` · `fallback_scene` *(port: `_deterministic_slides` + the repair rules that were genuinely right)* · `validate_scene` · `assign_variants` *(port)* · `resolve_icon` *(port: `icons.py`)*

### `core/script`
`write_script` · `length_control` *(port: `target_words`, `length_off`, `length_fix_messages`)* · `apply_style_guide` · `rewrite_selection` · `clean_script` *(port)* · `to_display_script` · `bind_beats_to_elements`

### `core/verify`
`check_numbers` *(upgrade `ungrounded_numbers`)* · `check_modality` · `check_entities` · `retrieve_support` · `entails` · `coverage_report` · `locale_lint` · `Finding` model · `verify_module`

### `core/speech`
`to_spoken` · `chunk_script` · `load_lexicon` *(port: `speech_text.py`, keep golden tests)* · `TTSProvider` · `KokoroProvider` *(port: `tts-server` logic, incl. EcoQoS opt-out, model download, thread setting)* · `align_words` · `ensure_beat_audio` *(port: `SpeechService` caching and in-flight dedupe)* · `assemble_scene_audio` *(port: pause logic)* · `master_audio` · `check_intelligibility` · `music_bed`

### `core/render`
`RenderPool` *(port: `MotionPool`)* · `render_scene` · `render_frames_range` · `poster` / `thumb` *(port: `render_slide_posters`, `make_derived`)* · `encode_args` · `concat` *(port)* · `add_chapters` · `add_subtitles` · `build_srt` / `build_vtt` / `split_cue` / `two_lines` *(port from `export.py`)* · `scene_cache_key` · `intro_outro` · `export_html_player` · `export_scorm` · `export_cmi5` · `export_pptx` *(port, de-branded)* · `export_pdf_handout` · `export_transcript` · `export_review_report`

### `core/review`
`transition(module, action)` (state machine) · `add_comment` · `approve_scene` · `approve_module` · `mark_stale(source_diff)` · `audit(event)`

### `core/memory`
`record_edit` · `retrieve_examples` *(port: `examples.py` idea, switch TF-IDF → embeddings)* · `edit_ratio` *(port)* · `replay_eval` *(port: `selfeval.py`)* · `style_guide.load/save`

### `core/llm`
`LLM` protocol · `OllamaProvider` *(port: `llm.OllamaClient`, `model_info` capability detection, fixed `NUM_CTX`)* · `structured(schema)` helper · `cache_call` · `PromptTemplate` registry · `ModelManager.pull/list/verify`

### `core/jobs`
`enqueue` · `claim_next` · `heartbeat` · `complete` · `fail` · `cancel` · `resume_incomplete_on_boot` · `ResourceScheduler.acquire` · `JobContext.progress/check_cancelled` *(port: `jobs.py` semantics)* · `EventBus` *(port)*

### `core/db` and `core/blobs`
`session()` · repositories per aggregate · `migrate()` · `put_bytes/put_file/get_path/exists/touch/gc(live_refs)` · `export_project/import_project` · `import_legacy_deck` (migration)

### `core/platform`
`unthrottle(pid)` / `unthrottle_tree()` *(port: `winperf.py`)* · `find_browser()` · `app_dirs()` · `system_metrics()` *(port: `sysmon.py`)*

### `web/scene-kit`
`registerLayout` · `createStage` · `stage.load/seek/ready/heroTime` · `buildTimeline` · `applyBeats` · `entranceTween` *(port)* · `addYoyoLoop` / `addRepeatLoop` / `spotlightSync` *(port)* · `fitText` · `distributeVertically` · `tokensToCssVars` · `measureOverflow` · `minFontPx` · `contrastReport` · `emptyAreaRatio` · `lintScene` · `shapes` / `decor` *(port: `shapes.js`, `spot-illustrations.js`)*

### `web/studio`
Routes: `Library`, `NewProject`, `Planner`, `Review`, `Timeline`, `Themes`, `Voices`, `Export`, `Updates`, `Diagnostics`, `Style`. Shared: `SourceViewer`, `SceneForm` (schema-driven), `BeatList`, `FindingsPanel`, `JobTray`, `CoverageBar`, `ContrastBadge`, `ShortcutHelp`.

### API surface (`/api/v1`, OpenAPI-documented, SSE for events)

`projects` CRUD/export/import · `projects/{id}/sources` (upload, versions, diff) · `…/understanding` (sections, claims) · `…/plan` (get/edit/regenerate/coverage) · `modules/{id}` (scenes, beats, status) · `scenes/{id}` PATCH (content edits) · `beats/{id}` PATCH · `modules/{id}/generate|verify|voice|render|export` (each returns a job) · `jobs` list/get/cancel/retry · `themes` CRUD/import/preview · `voices` list/preview · `lexicon` CRUD · `models` list/pull · `system` metrics/health · `events` (SSE) · `review` (comments, approve).

---

## 17. Roadmap (original draft; superseded by the revised roadmap in §18)

*Effort is a rough solo-developer estimate with AI assistance; treat as ordering, not a promise.*

### Phase 0 — Safety net and baseline (≈3 days) — *do this first, even if you never rebuild*
- `git init`, commit with the existing `.gitignore`; add a remote or a nightly backup of `data/`, `uploads/`, `exports/`.
- Build the **golden corpus** (§13) and hand-label headings + top claims.
- Run the *current* app on it and record baselines: time, section F1, % slides with empty areas, script word-count accuracy, number errors found by a human, loudness. Write them to `eval/baselines/current-app.json`.
- Fix the hermetic-test bug (`Path("data")` → injected settings) and reconcile doc drift (test counts, layout counts).
- Install two or three candidate models and run the current generation prompts against them; you may find a cheap quality win before writing any new code.

### Phase 1 — Core skeleton + understanding (≈2 weeks)
Repo/workspace, settings, SQLite + Alembic, blob store, durable jobs + SSE, ingest (Docling + pypdfium2 fallback), claims extraction with quote validation, module planner with coverage, eval harness wired to the corpus.
**Exit:** claim recall and coverage numbers beat or match baseline on the corpus; kill-the-server-mid-job resumes.

### Phase 2 — Scenes and renderer parity (≈2 weeks)
Pydantic scene union, `fill_scene` per kind, scene-kit with the 12 layouts ported from `slide-engine.js`, fit + vertical distribution, linter, theme format + converter for the 34 templates, visual regression suite, RenderPool with frame sharding, colour-tagged encode.
**Exit:** every layout × orientation × 3 themes passes the linter; frames deterministic; render speed ≥ current.

### Phase 3 — Script, verify, voice, export — first end-to-end slice (≈2–3 weeks)
Beat-based scripts, verifier + adversarial tests, Kokoro provider + lexicon + alignment + mastering, MP4/SRT/VTT export with chapters, minimal review UI (enough to approve and export).
**Exit:** PDF → approved MP4 on the corpus at the §1.3 targets. *This is the cut-over point where the new pipeline can replace the old one for real work.*

### Phase 4 — Studio UI and migration (≈2–3 weeks)
Full review workspace (source/slide/script panes), schema-driven slide editing, timeline view, planner, theme browser, diagnostics; legacy importer (`uploads/*/deck.json` → projects, `data/templates`, `pronunciations.json`, `approved_scripts.jsonl` → `style_example`).
**Exit:** you can do a full series start-to-finish without touching files or code.

### Phase 5 — Outputs that matter in the workplace (≈2 weeks)
Quiz generation from claims, HTML5 player, SCORM 1.2/2004 + cmi5, PPTX/PDF handouts, update workflow (diff → stale → regenerate → re-approve), DOCX/scan ingestion polish.

### Phase 6 — Packaging and polish (≈1–2 weeks)
Tauri shell, installer, model manager, support bundle, first-run experience, music/intro-outro/transitions, multi-language (translate + voices) behind a feature flag.

### Phase 7 — Style memory and optimisation (ongoing)
House style guide, retrieval few-shots, replay eval UI; consider DSPy-style prompt optimisation once ≥ 200 pairs; fine-tune only if the eval shows prompts have plateaued.

### What to port verbatim vs retire

| Port (with tests) | Retire |
|---|---|
| `speech_text.py` + golden tests · `winperf.py` · `MotionPool` idea · loop layer & entrance catalogue in `slide-engine.js` · `shapes.js`, `spot-illustrations.js`, icon catalogue · cue splitting & SRT line balancing · 34 templates, fonts manifest · upload safety checks · `edit_ratio` & replay-eval idea · EcoQoS finding · `num_ctx` finding | `layouts.repair_specs` & friends (replaced by typed schemas) · regex-first PDF sectioning (kept only as fallback) · vision-LLM critic as the quality gate · JSON-per-deck store and in-memory jobs · `GpuArbiter` · generated-`slide.md`-vs-Python-profile duplication · `engine/deckcraft-cli` (206 MB; only used to rasterise uploaded PPTX — replace with Docling text + optional LibreOffice rasterisation, or `ImageScene` import) · customer-specific strings ("Strategic HR Australia", "Strategic \| Practical \| Impactful", `7.x`) · `voxcpm_*` names |

---

## 18. Decisions made (2026-11-23) and what they change

| # | Your answer | Effect on this plan |
|---|---|---|
| 1 | **Delivery: LMS** | SCORM/cmi5 packaging, the HTML5 player and quiz generation move from Phase 5 to **core (Phase 3)**. MP4 stays as a secondary output. Verify early which LMS and which standard it accepts (SCORM 1.2, 2004, or cmi5/xAPI); this is the first thing to find out. |
| 2 | **Local-only, to cut AI token costs** | The cloud-provider option in §9.1 is **dropped**. Local models are the only path, so the Phase 0 model benchmark and the eval harness become the most important early work. The `LLM` interface stays (it is cheap and lets you swap local runtimes). |
| 3 | **Single user** | Drop team/server mode, approval-by-another-person workflows and the per-launch API token concern. Keep the review screen and audit trail, but as a personal **checklist and history** ("I verified this against policy v3"), not multi-user permissions. |
| 4 | **Existing decks stay a pillar** (I read "I am sure about this one" as *yes, keep it*; tell me if you meant otherwise) | `ImageScene` plus notes-to-beats is **in scope from Phase 2**. PPTX input is converted by Docling/python-pptx for text, with slides rasterised for the visuals. The 206 MB `engine/deckcraft-cli` can be retired only after confirming LibreOffice or another rasteriser matches its fidelity on your real decks. |
| 5 | **English now, multilingual later** | Build **i18n-ready, not multilingual**: every scene, beat, lexicon entry and theme string carries a `language` field; prompts live in per-language templates; locale spelling checks and the TTS provider take a language parameter; fonts manifest records script coverage. Translation and extra voices are then a Phase 6 feature flag instead of a refactor. Choose an English variant per project (AU/UK/US) now. |
| 6 | **Personal use only** | Drop **Tauri, installers, model-manager polish, auto-update, NOTICE generation, support bundles** and most of the licence risk register (§14). Keep one launcher command (`just dev` or a single script). Licences only matter if you ever share it. Phase 6 shrinks to a one-day launcher and optional extras. |

### 18.1 Addendum: your own SharePoint LMS, used in Teams on phones

You own the LMS, so you are not bound to SCORM. That changes the output design.

**1. Make the player the primary output, and make completion reporting pluggable.**
The HTML5 player emits a small set of events (`started`, `progress`, `quiz_answered`, `completed`, `score`) through a `CompletionAdapter`. Ship adapters for `postMessage` (to your LMS web part), a plain REST/Graph callback, and SCORM/cmi5 (kept as a cheap extra in case you ever want it). Your LMS decides what to do with the events; no SCORM runtime is needed unless you want it.

**2. Responsive means "re-laid-out", not "scaled video".**
The scene-kit already renders portrait (1080x1920) and landscape. In the player, pick the orientation from the container's aspect ratio and re-render the scene live at the real size (CSS container queries plus `fitText`), so text is legible on a phone and there is no letterboxing. This is a strong reason for the live HTML player over MP4 for your use. MP4 remains a fallback and for sharing.

**3. Teams-mobile constraints to design for** (from my knowledge; test on your actual devices, because Teams webviews behave differently from browsers):
- **Autoplay with sound is blocked**, so start on a clear "Tap to begin" screen, and request audio only after that tap. Use `playsinline` for video.
- **Fullscreen and orientation lock APIs are unreliable** in embedded webviews: do not depend on them; fill the available viewport.
- **Memory and CPU are limited:** keep the player light (no per-frame heavy effects on low-power mode; honour `prefers-reduced-motion`; the `calm` and `off` motion levels become the mobile default), preload audio per scene, not the whole module.
- **Bandwidth:** audio per scene as AAC/Opus, small scene JSON; far smaller than a 1080p MP4.
- **Dark mode and theme:** read the Teams theme (via `@microsoft/teams-js`) if embedded as a tab, and offer a high-contrast-safe variant.
- **Touch targets** at least 44 px, no hover-only interactions, quiz answers reachable one-handed, captions always available (users watch with sound off).
- **Resume:** save position and quiz state (to your LMS through the adapter) so a call or app switch does not lose progress.

**4. Hosting is the likely trap: verify early.** As I understand it, SharePoint Online document libraries serve `.html` files as downloads, not as rendered pages, so a player cannot simply be uploaded there and opened. Realistic options: a small static host you control (Azure Static Web Apps or Blob Storage with CDN), an SPFx web part that bundles the player, or hosting media in SharePoint and the player code elsewhere. This needs a 1-hour test in your tenant before Phase 3. Also check your tenant's CSP and iframe/embed allowances, and that audio and video files stream from your library with range requests.

**5. Phase 0 additions:** (a) build a throwaway "hello player" page that plays one scene with audio and a tap-to-start, and open it from your LMS in **Teams on a real phone (iOS and Android)**; (b) record what works for autoplay, audio, fullscreen and storage. This decides the player's constraints before anything is built around them.

**6. Roadmap effect:** Phase 3 produces the responsive player plus the adapter and quiz; SCORM export moves to optional. Add a **mobile visual test matrix** (e.g. 360x640, 390x844, 412x915, tablet, desktop) to the Playwright suite, with the linter's minimum font size (suggest 16 px equivalent body text at phone width) as a blocking check.

### 18.2 Decision: everything lives in SharePoint, so the player is an SPFx web part

Hosting answer: training material is stored in SharePoint and the LMS uses SharePoint lists. Consequences (from my understanding of SharePoint Online; verify each in your tenant):

1. **Split code from content.**
   - **Player = one SPFx web part**, built once, deployed through your tenant App Catalog. It is the only "app" you host, and it never changes per module.
   - **Content = a plain folder per module** in a document library: `manifest.json`, `scenes.json`, `audio/*.m4a`, `images/*`, `captions.vtt`, `quiz.json`, optional `video.mp4`. Plain data files (JSON, audio, images) are served normally by SharePoint; only `.html` is the problem, and this design avoids it.
2. **Completion goes straight to your lists.** Inside SPFx the web part is already signed in as the user, so it can write to a SharePoint list through `SPHttpClient` or PnPjs with no Graph app registration, tokens or SCORM runtime. The `CompletionAdapter` becomes a `SharePointListAdapter` (create or update one item per user and module: status, score, progress %, last scene, timestamps, attempt count). Keep SCORM/cmi5 export only as an optional extra.
3. **Teams on phones.** SPFx web parts can be surfaced in Teams (tab or personal app). This is the route I'd test first, because it keeps one codebase for SharePoint pages and Teams. Support on the Teams *mobile* clients is more limited than on desktop, so the Phase 0 phone test (§18.1 item 5) must use this exact path, not a generic web page. If it misbehaves, fallback is the module's `video.mp4` in a SharePoint video or file viewer, which plays on every client.
4. **Publishing from the studio.** Add a `publish_to_sharepoint(module)` function that uploads the content folder to the right library and creates or updates the module's catalogue list item (title, duration, language, version, content URL). Authenticate with a one-time device-code sign-in under a dedicated app registration; keep the token cache local. Start manually (drag the folder into the library) and automate once the format is stable.
5. **Versioning and re-training.** Put a `content_version` and the source policy version in `manifest.json`. When a policy changes and a module is regenerated, the LMS can see that earlier completions refer to an older version and prompt retraining. This is the practical payoff of the update workflow in §6.
6. **Large files and caching.** Keep each module small (audio per scene, images compressed, no video unless asked). SharePoint supports range requests for media; confirm your library permits anonymous-free access for signed-in users only, and that the web part sets cache headers sensibly via versioned file names (content-addressed names fit the existing blob design).
7. **Updated deliverables.** Primary output becomes the **content package + SPFx player**. Secondary: MP4 (fallback/sharing), SRT/VTT, PDF handout. SCORM: optional.

**Roadmap changes:** Phase 0 adds "scaffold a blank SPFx web part, deploy it to your App Catalog, have it read a JSON file and play an audio file from a library, and write one row to a list, then open it in Teams on a phone." Phase 3's player becomes a TypeScript/React component library (`web/player`) consumed by the SPFx project, so the same code also runs in a local browser for development.

### 18.3 Final scope decision: the product is a downloadable MP4 (supersedes 18.1, 18.2 and the LMS items in the table above)

**Outcome:** policy or existing deck in, a narrated, subtitled **MP4** out. What you do with the file afterwards (SharePoint, Teams, anywhere) is outside the app.

**Dropped from scope:** HTML5 player, SPFx web part, SharePoint publishing, completion adapters, quiz generation, SCORM/cmi5, live responsive re-layout. Sections 18.1 and 18.2 are kept only as notes in case you revisit LMS delivery later; the scene-kit and data model do not need to change to allow it.

**What stays important, now that video is the whole product:**
- **Phones are still the viewing device**, so keep the legibility rules: portrait 1080x1920 as the default, landscape optional; minimum body text roughly 16 px-equivalent at phone width, enforced by the layout linter as a blocking check; captions on by default (people watch muted); calm motion default.
- **Video quality is the product**, so the earlier video findings move to the top: loudness normalisation to about -16 LUFS, BT.709 colour tags, no empty-frame layouts, chapters, burned-in or soft captions, optional intro/outro and music bed.
- **Accuracy and review** (claims, verifier, source-versus-slide-versus-script review screen) remain the main value.
- **File-friendly output:** sensible file size (target a few MB per minute at 1080p), `faststart`, one MP4 plus `.srt`/`.vtt`, and a PDF transcript if wanted.

**Simplified roadmap (replaces the revised roadmap below)** — *progress is tracked in the [Build status](#build-status) section near the top:*
- **Phase 0 (about 3 days):** `git init`; golden corpus of your real PDFs plus one existing PPTX; baselines from the current app (including loudness and empty-frame measures); compare 2-3 local models; fix the two non-hermetic tests.
- **Phase 1 (about 2 weeks):** core skeleton, SQLite, jobs, ingest (PDF, DOCX, PPTX), claims with quote validation, module planner, eval harness.
- **Phase 2 (about 2 weeks):** typed scenes, scene-kit with the 12 layouts plus `ImageScene` for existing decks, fit and layout linter, theme converter, visual regression, colour-correct render.
- **Phase 3 (about 2-3 weeks):** beat-based scripts, verifier, Kokoro with word alignment and mastering, MP4/SRT/VTT with chapters, minimal review UI. **Cut-over point.**
- **Phase 4 (about 2 weeks):** full review workspace, slide editing, timeline, legacy importer, policy-update workflow.
- **Phase 5 (optional):** intro/outro and music, multilingual, style memory.

First milestone: about 3 weeks to one policy PDF becoming one approved, correctly mastered MP4 on your laptop.

### Earlier revised roadmap (superseded by 18.3)

- **Phase 0 (3 days):** `git init`; golden corpus (include a few of *your real* policy PDFs and one existing PPTX); baselines for the current app; **benchmark 2-3 local models** (e.g. Qwen3 8B vs Gemma 3 4B/12B) on the existing prompts. Fix the two non-hermetic tests. Find out your LMS's accepted standard and import a trivial SCORM test package to prove the loop.
- **Phase 1 (2 wks):** core skeleton, SQLite, jobs, ingest (PDF, DOCX, PPTX), claims with quote validation, planner, eval harness.
- **Phase 2 (2 wks):** scene schemas, scene-kit with the 12 layouts plus `ImageScene`, linter, theme converter, visual regression, colour-correct render.
- **Phase 3 (3 wks):** beats and scripts, verifier, Kokoro and alignment and mastering, **HTML5 player + quiz + SCORM/cmi5 export**, MP4/SRT/VTT, minimal review UI. *Cut-over point.*
- **Phase 4 (2-3 wks):** full review workspace, slide editing, timeline, legacy importer.
- **Phase 5 (1-2 wks):** policy-update workflow (diff, stale, regenerate), PDF handouts, music and intro/outro.
- **Phase 6 (optional, ongoing):** multilingual, style memory, replay-eval UI, fine-tuning only if prompts plateau.

Because you are the only user and cost is the driver, a good first milestone is a **thin slice in about 3-4 weeks**: PDF in, claims out, a few scenes, narration, one SCORM package that plays in your LMS. Everything else can wait for that to work.

## Appendix A — What I examined, and what I did not

**Read in full or substantially:** `project_plan.md`, `CAPABILITIES.md`, `pyproject.toml`, `start-deckcraft.ps1`, `pdf_modules.py`, `llm.py`, `motion.py`, `export.py`, `tts.py`, `layouts.py`, `app.py` (routes and the PDF-generate flow), and the heads of `critic.py`, `templates.py`, `template_import.py`, `profiles.py`, `slide_md.py`, `jobs.py`, `decks.py`, `config.py`, `gpu.py`, `icons.py`, `scripting.py`, `selfeval.py`, `tts-server/voxcpm_server.py`, `web/slide-engine.js`, `web/app.js` (function index), `tests/conftest.py`.

**Observed directly:** `pytest` (193 tests collected; see Appendix B for the result), `ollama list` (only `gemma3:4b`), `nvidia-smi` (RTX 5050 Laptop, 8,151 MiB, driver 592.82), `ffprobe`-style output and a decoded frame from the latest export, two cached portrait slide renders, a loudness measurement of the export, targeted greps for missing features (no loudnorm/music/transitions/chapters; no slide-content edit endpoint; jobs not persisted).

**Not examined:** `learn.py`, `design.py`, `template_profiles.py`, `fonts.py`, `ingest.py`/`render.py` internals beyond headers; the bodies of `web/app.js`, `styles.css`, `slide-stage.css`; the other exported videos; the app running interactively in a browser; whether the portrait empty-area fix described in `CAPABILITIES.md` is live in the current code path (the two cached renders I opened predate or contradict it — worth a 2-minute check).

**Not verifiable from here:** third-party licences and current versions (my knowledge of them runs to mid-2026 and licences change — verify before adopting); model quality on your hardware (hence the eval-first approach); time estimates.

## Appendix B — Test run

Full run on 2026-11-23: **191 passed, 2 failed** (89 s).

Both failures have the same cause and are not product bugs: `tests/test_slide_md.py::test_custom_dropin_slide_md_template_and_standardized_builtins` and `tests/test_sysmon_design.py::test_builtin_mit_template_library_list_preview_and_apply` assert `len(items) == 34` against the **real** `data/templates` directory, which now contains 35 because a custom template (`management-project`) was imported through the app. Tests that depend on the developer's live data are what §13 and finding P13 mean by "not hermetic".
