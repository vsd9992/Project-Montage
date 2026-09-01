# Local AI Album Studio

### Project Concept / Product Definition

## 1. Product vision

A **fully local AI-assisted photo and video album creation application** capable of taking hundreds or thousands of photos/videos from an event and turning them into a professionally designed, printable album.

Typical input:

> 2,500 wedding photographs + 80 videos

Typical output:

> 30-spread / 60-page wedding album
> 12×36-inch spreads
> 300 DPI
> professionally selected photographs
> coherent event/story sequence
> intelligently designed layouts
> print-ready JPEG/PDF

The user should not need to manually shortlist 2,500 photographs before starting.

The AI's job is to reduce that workload dramatically while leaving final creative control with the user.

---

# 2. Core philosophy

The project should separate **AI judgement** from **production rendering**.

AI decides:

> What is happening?
> Which photos are good?
> Which are duplicates?
> Who appears in them?
> Which moments belong together?
> Which photo deserves prominence?
> What sequence tells the story?
> What type of layout suits this group?

Traditional software decides:

> Exact crop
> Exact coordinates
> Image resolution
> Margins
> Bleed
> typography
> masks
> page dimensions
> final rendering

This distinction is extremely important.

**AI should design the album, but it should not redraw the photographs.**

Original photographs remain untouched unless the user explicitly enables a generative editing feature.

---

# 3. Primary workflow

The application could have six major stages:

**Import → Analyse → Curate → Storyboard → Design → Export**

### Import

User selects a directory containing:

* JPEG
* PNG
* HEIC
* RAW where practical
* MP4/MOV and common video formats

Original files remain untouched.

The application creates its own project database/cache.

---

# 4. Media analysis

This is the first batch-processing stage.

Every photograph receives a record containing things such as:

```text
File
Date/time
Camera
Resolution
Orientation
GPS
Visual embedding
Quality score
Sharpness
Exposure
Faces/person IDs
Scene
Event classification
Duplicate group
Similarity group
AI description
Selection score
```

But we shouldn't ask Qwen to analyse everything immediately.

Cheap deterministic analysis comes first.

---

# 5. Duplicate and burst detection

Wedding photographers can produce ten almost identical photographs within three seconds.

The system should recognize:

```text
IMG_101
IMG_102
IMG_103
IMG_104
IMG_105
```

as one photographic moment.

It can then rank them.

For example:

```text
Moment #47

IMG_103    94/100 ★ recommended
IMG_105    89/100
IMG_102    82/100
IMG_101    76/100
IMG_104    51/100 - eyes closed
```

This alone could remove enormous amounts of manual work.

---

# 6. Photo quality assessment

Selection shouldn't simply mean "sharp = good."

Several dimensions matter:

**Technical**

* focus
* blur
* exposure
* resolution
* noise
* clipping

**Composition**

* subject positioning
* framing
* headroom
* crop potential
* distracting elements

**Human**

* eyes open
* expression
* face visibility
* emotional moment
* interaction

**Album value**

This is different again.

A technically mediocre photograph of a bride hugging her father may deserve inclusion over the fifteenth technically perfect portrait.

That distinction is where the VLM becomes useful.

---

# 7. People recognition and grouping

The system should cluster recurring faces.

Initially:

```text
Person 01
Person 02
Person 03
...
```

User can optionally identify:

```text
Person 01 → Bride
Person 02 → Groom
Person 03 → Bride's mother
Person 04 → Groom's father
```

That information becomes extremely valuable.

The AI can then understand instructions such as:

> Prioritize bride and groom but make sure immediate family is adequately represented.

We should select a commercially safe face-recognition model rather than locking ourselves to restricted pretrained InsightFace weights.

---

# 8. Event understanding

Photos should be grouped into meaningful events.

For a wedding:

```text
Getting Ready
Bride Portraits
Groom Portraits
Venue
Decor
Guests Arriving
Baraat
Bride Entry
Ceremony
Varmala
Family
Couple Portraits
Reception
Candid Moments
Closing
```

The exact structure shouldn't be hardcoded.

An Indian wedding, Christian wedding, birthday, baby's first birthday and holiday require completely different stories.

---

# 9. Video analysis

Videos become another source of album material.

We do **not** analyse every video frame.

FFmpeg can detect scene changes and extract representative frames.

For example:

```text
VID_4021.mp4
12 minutes

↓ scene detection

37 representative frames

↓ quality analysis

11 usable

↓ AI

3 interesting moments

00:02:17 Bride entering
00:06:42 Family reaction
00:09:03 Couple laughing
```

The user can extract a high-resolution frame if the source video supports sufficient quality.

Videos can therefore contribute photographs without turning analysis into computational punishment.

---

# 10. AI curation

Now the system creates an intelligent shortlist.

Example:

```text
INPUT

2,843 photos
76 videos

↓

2,201 technically usable

↓

1,316 unique photographic moments

↓

AI shortlist

426 recommended photographs

↓

Album requirement

30 spreads

↓

Final proposed selection

173 photographs
```

Nothing gets deleted.

Rejected images remain available.

---

# 11. Storyboard engine

Before designing pages, create the album's narrative.

Example:

```text
01 Opening
02 Venue
03 Bride preparation
04 Groom preparation
05 Bride portraits
06 Groom portraits
07 Family
08 Baraat
09 Bride entrance
10 Ceremony
...
27 Reception
28 Candid
29 Couple
30 Closing
```

The user can drag sections around before layout generation.

This saves us from regenerating an entire album because somebody decides the reception should precede the couple portraits.

---

# 12. Album design engine

This is probably the project's most valuable proprietary component.

Rather than hundreds of rigid templates, create **layout grammar**.

The engine understands structures such as:

### Hero

```text
┌──────────────────────────────┐
│                              │
│          HERO IMAGE          │
│                              │
└──────────────────────────────┘
```

### Hero + supporting

```text
┌──────────────────┬───────────┐
│                  │     2     │
│       HERO       ├───────────┤
│                  │     3     │
└──────────────────┴───────────┘
```

### Documentary sequence

```text
┌─────────┬─────────┬─────────┐
│    1    │    2    │    3    │
├─────────┴────┬────┴─────────┤
│      4       │      5       │
└──────────────┴──────────────┘
```

But layouts adapt to:

* portrait/landscape orientation
* faces
* subject position
* available negative space
* photograph importance
* chronology
* page balance

Therefore the software can generate thousands of variations without maintaining thousands of Photoshop templates.

---

# 13. Design styles

Style and layout should be separate.

For example:

**Modern Minimal**

White space, clean typography, restrained compositions.

**Luxury Wedding**

Large imagery, elegant typography, muted textures.

**Traditional Indian**

More decorative compositions and culturally appropriate elements.

**Editorial**

Magazine-like compositions.

**Documentary**

More images per spread and chronological storytelling.

**Birthday / Kids**

More playful layouts.

**Travel**

Location-driven storytelling and landscape emphasis.

Users could eventually create and save their own styles.

---

# 14. Spread intelligence

AI shouldn't randomly choose layouts.

It might produce a design instruction:

```json
{
  "spread": 12,
  "event": "Varmala",
  "importance": "high",
  "layout": "hero_plus_three",
  "hero": "IMG_1842.jpg",
  "supporting": [
    "IMG_1847.jpg",
    "IMG_1851.jpg",
    "IMG_1863.jpg"
  ],
  "mood": "celebratory",
  "density": "medium"
}
```

The deterministic renderer converts this into the actual page.

This makes the system **explainable and editable**.

---

# 15. Cropping intelligence

This deserves its own subsystem.

If a photograph contains two faces:

```text
[ Bride ]             [ Groom ]
```

the renderer cannot blindly centre-crop it.

We maintain:

* face bounding boxes
* important subject regions
* saliency
* safe crop regions

Then calculate the crop according to the destination frame.

The user can manually reposition any crop.

---

# 16. Interactive editor

AI generation should create **version 1**, not declare itself the Michelangelo of wedding albums.

The user sees the entire album:

```text
[01-02] [03-04] [05-06] [07-08]
[09-10] [11-12] [13-14] [15-16]
...
```

Open a spread and:

* drag photos
* swap photos
* resize frames
* reposition crop
* change layout
* add/remove photo
* add text
* change background
* lock elements
* regenerate spread

Important command:

> **Regenerate this spread**

Not regenerate the entire album.

---

# 17. Locking

Anything approved should be lockable.

For example:

```text
Spread 1     🔒
Spread 2     🔒
Spread 3     regenerate
Spread 4     🔒
```

Regeneration never touches locked spreads.

Similarly individual photos could be locked:

> This photograph must remain in the album.

---

# 18. AI conversation layer

Eventually the editor can support natural instructions:

> Make this spread cleaner.

> Use fewer photos here.

> Give the bride portrait more prominence.

> These two photographs look repetitive.

> Put the family photograph on the next spread.

> Make the opening more luxurious.

The VLM converts those requests into structured layout operations.

It shouldn't directly manipulate pixels.

---

# 19. Print production

Professional output needs to be a first-class feature.

Album presets:

```text
12 × 18
12 × 24
12 × 30
12 × 36
14 × 40
custom
```

Settings:

* DPI
* bleed
* safe zone
* gutter
* colour profile
* JPEG quality
* PDF export
* individual spread export

Before export:

```text
PRE-FLIGHT CHECK

✓ 30 spreads
✓ 300 DPI
✓ bleed correct
✓ safe margins
✓ no missing assets
✓ no low-resolution images

⚠ Spread 17:
  IMG_2281 effective resolution = 214 DPI
```

That's the sort of boring feature that separates actual production software from an AI demo.

---

# 20. Local-first architecture

I'd keep the entire system offline-capable.

```text
                DESKTOP UI
                    │
             Project Manager
                    │
      ┌─────────────┼──────────────┐
      │             │              │
 Media Engine    AI Engine    Design Engine
      │             │              │
 FFmpeg         Qwen3-VL       Layout rules
 OpenCV         SigLIP2        Crop engine
 Metadata       Face model     Renderer
      │             │              │
      └─────────────┼──────────────┘
                    │
                  SQLite
                    │
                 Cache
                    │
              Original Media
```

No cloud dependency.

No per-photo API charges.

No photographs leaving the computer.

---

# 21. Model lifecycle

This is specifically important for Vishnu.

Don't keep everything in VRAM.

```text
IMPORT
   ↓
CPU analysis
   ↓
Load SigLIP
   ↓
Batch embeddings
   ↓
Unload
   ↓
Load face model
   ↓
Batch faces
   ↓
Unload
   ↓
Load Qwen3-VL
   ↓
Selected/ambiguous image analysis
   ↓
Unload
   ↓
Design/render
```

Therefore the RTX 4060's practical VRAM limit becomes manageable.

System RAM and disk become our working/cache layers.

---

# 22. Persistent analysis

Every expensive result gets cached.

If 3,000 photographs have already been analysed:

```text
Photo hash → analysis record
```

Opening them again should cost effectively nothing.

If the user adds another 200 photographs:

> Analyse 200 new files.

Not:

> Analyse all 3,200 again because apparently electricity is free.

This also means the same photographs can be reused in different albums.

---

# 23. Non-destructive architecture

Absolute rule:

**Never alter original media.**

Projects reference source files.

Generated items live separately:

```text
AlbumProject/
    project.db
    cache/
    previews/
    extracted_frames/
    generated_assets/
    exports/
```

Deleting an album project doesn't delete the photographs.

---

# 24. Generative image AI

I would explicitly make this **Phase 2/3**, not MVP.

Later it could provide:

* background extension
* object removal
* decorative backgrounds
* texture generation
* backdrop cleanup
* intelligent fill
* stylistic elements

But it must always operate on copies.

And generative editing should be visibly identified as such.

---

# 25. Hardware target

Vishnu should actually become our **minimum recommended development target**, rather than something we reluctantly support.

Target:

**RTX 4060 8 GB class GPU
32 GB RAM
modern 6-core-ish CPU
SSD strongly recommended**

Lower hardware can work more slowly.

Better GPUs get larger models/batches.

The application should detect hardware and automatically select:

```text
Low Memory
Balanced
High Performance
```

rather than forcing users to understand quantization and CUDA layers just to make Auntie's anniversary album.

---

# 26. Batch-processing design

Large projects need to survive interruption.

For example:

```text
Analysing 1,284 / 3,842

Completed       1,284
Remaining       2,558
Current batch   32
```

Stop the application.

Restart tomorrow.

Resume:

```text
1,285 / 3,842
```

Every operation should therefore be **checkpointed and resumable**.

This is essential, not polish.

---

# 27. Suggested product phases

I would resist building everything simultaneously.

### Phase 1: Proof of concept

Take ~500 wedding photographs.

Build:

**Import → similarity → quality → Qwen understanding → shortlist → simple storyboard**

No fancy UI.

Success criterion:

> Can our system select and sequence photographs intelligently?

If not, there is no point building an editor.

### Phase 2: Album engine

Build:

**spread model + layout grammar + intelligent cropping + high-resolution renderer**

Success criterion:

> Can we automatically produce a respectable 20-spread album?

### Phase 3: Desktop application

Add:

**project management + thumbnails + storyboard + spread editor + regenerate + locking + export**

Now it becomes usable software.

### Phase 4: Advanced AI

Add:

**face relationships + better emotional scoring + conversational editing + video analysis + learned style preferences**

### Phase 5: Generative tools

Only then:

**background generation + extension + cleanup + creative AI editing**

---

# 28. Training our own model?

**Not initially.**

We shouldn't train Qwen to become an album designer.

Instead create our own structured **album-design knowledge/rules** around it.

For example:

```text
Wedding Design Rules

Opening:
1-2 photographs

Important ceremony:
1 hero + maximum 3 supporting

Family groups:
medium density

Couple portrait:
prefer full spread

Repeated burst:
maximum one unless sequence adds meaning
```

Then improve these rules based on real albums.

Eventually, if we accumulate enough approved design decisions:

```text
AI proposal
→
human modification
→
final approved spread
```

we obtain a genuinely valuable training dataset.

**That** could justify fine-tuning later.

---

# 29. The moat

The open models aren't the product.

Anyone can download Qwen.

The valuable part becomes:

**our photo scoring + event understanding + album storytelling + layout grammar + crop intelligence + design styles + user corrections/learning + production renderer.**

In other words:

> Qwen understands photographs.

> **Our system understands how to make an album from them.**

That's a much stronger project direction.

---

# 30. MVP definition I would lock

For the first serious version, I'd limit scope to:

**Photos only initially.
Wedding + Birthday albums.
JPEG/PNG initially.
Qwen3-VL + SigLIP2 + commercially safe face model.
500-5,000 photos/project.
Automatic shortlist.
Duplicate/burst grouping.
People grouping.
Event/story detection.
Automatic storyboard.
20-40 spread generation.
5-6 design styles.
Editable spreads.
Lock/regenerate.
12×18 through 12×36 and custom sizes.
300-DPI JPEG/PDF output.
Completely local.
No subscription/API dependency.
Non-destructive.
RTX 4060 / 32 GB as baseline hardware.**

I would **deliberately exclude video, RAW workflow, generative image editing and model training from the MVP**. Architect for them, certainly, but don't build them yet. Otherwise a very good album application quietly turns into Photoshop + Lightroom + Premiere + an AI research laboratory, which is how perfectly sensible projects acquire a three-year roadmap before producing page one.

This scoped version is both technically realistic on Vishnu and substantial enough to answer the important question: **can local AI actually produce an album you'd be willing to print?**
