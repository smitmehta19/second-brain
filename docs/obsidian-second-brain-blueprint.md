# Obsidian Second Brain — Complete Blueprint

> **Profile**: Data engineer / AI practitioner. Android Pixel + Android tablet + Windows desktop. Replacing Notion, browser bookmarks, WhatsApp saves, Instagram/YouTube/Substack captures. Obsidian Sync. Minimal warm charcoal/champagne aesthetic.

---

## 1. Vault Architecture

```
SecondBrain/
├── 00_Inbox/                  # Everything lands here first. Zero friction capture zone.
├── 01_Projects/               # Active, time-bound work with a clear outcome (PARA)
│   ├── Job Search 2026/
│   ├── Wedding Planning/
│   └── ...
├── 02_Areas/                  # Ongoing responsibilities without end dates (PARA)
│   ├── Career/
│   ├── Finance/
│   ├── Health & Fitness/
│   ├── Cooking/
│   └── ...
├── 03_Resources/              # Topic-based reference material (PARA + Zettelkasten)
│   ├── Data Engineering/
│   ├── Gen AI/
│   ├── Data Science/
│   ├── Politics/
│   ├── Anime/
│   └── ...
├── 04_Archive/                # Completed/inactive projects and outdated resources
├── 05_Atlas/                  # MOCs + structural notes (LYT navigational layer)
│   ├── MOC - Data Engineering.md
│   ├── MOC - Gen AI.md
│   ├── MOC - Fitness.md
│   └── ...
├── 06_Calendar/               # Daily notes, weekly reviews, meeting notes
│   ├── Daily/
│   ├── Weekly/
│   └── Meetings/
├── 07_People/                 # Person notes — contacts, mentors, collaborators
├── _Templates/                # All templates live here
├── _Attachments/              # Images, PDFs, screenshots — never loose in root
└── _Meta/                     # Dashboard, CSS snippets, plugin configs, vault docs
    ├── Home.md
    └── Vault Guide.md
```

**Why this hybrid**: PARA gives you actionable buckets (01-04). LYT's Atlas (05) gives you navigational MOCs so you can browse by topic. Zettelkasten atomic notes live *inside* 03_Resources as interlinked evergreen notes. Calendar (06) keeps temporal notes separate from knowledge notes.

---

## 2. Categorization System

### Tag Taxonomy

| Prefix | Purpose | Examples |
|--------|---------|---------|
| `#type/` | Note type | `#type/fleeting`, `#type/literature`, `#type/evergreen`, `#type/project`, `#type/moc`, `#type/meeting`, `#type/person` |
| `#domain/` | Knowledge domain | `#domain/data-engineering`, `#domain/gen-ai`, `#domain/fitness`, `#domain/finance`, `#domain/cooking`, `#domain/anime`, `#domain/politics`, `#domain/job-search`, `#domain/wedding` |
| `#status/` | Processing state | `#status/inbox`, `#status/processing`, `#status/evergreen`, `#status/archived` |
| `#source/` | Where it came from | `#source/web`, `#source/youtube`, `#source/substack`, `#source/instagram`, `#source/whatsapp`, `#source/book`, `#source/podcast`, `#source/thought` |
| `#effort/` | Size of work | `#effort/quick` (< 15 min), `#effort/medium` (< 1 hr), `#effort/deep` (> 1 hr) |

**Rules**: Always lowercase, hyphens not spaces. Max 5 tags per note. Tags classify; links connect.

### Frontmatter Schemas

**Fleeting Note**
```yaml
---
type: fleeting
created: "{{date:YYYY-MM-DD}}"
status: inbox
source: 
domain: 
tags: [type/fleeting]
---
```

**Literature Note**
```yaml
---
type: literature
created: "{{date:YYYY-MM-DD}}"
status: inbox
source-type:          # article | book | podcast | video | reel | substack
source-url: 
author: 
domain: 
rating:               # 1-5
key-takeaways: 
tags: [type/literature]
---
```

**Evergreen Note**
```yaml
---
type: evergreen
created: "{{date:YYYY-MM-DD}}"
status: evergreen
domain: 
related: []           # links to other evergreen notes
tags: [type/evergreen]
---
```

**Project Note**
```yaml
---
type: project
created: "{{date:YYYY-MM-DD}}"
status: active        # active | paused | completed
area:                 # parent area
deadline: 
domain: 
tags: [type/project]
---
```

**Area Note**
```yaml
---
type: area
created: "{{date:YYYY-MM-DD}}"
domain: 
review-cycle: monthly
tags: [type/area]
---
```

**Person Note**
```yaml
---
type: person
created: "{{date:YYYY-MM-DD}}"
company: 
role: 
context:              # how you know them
last-contact: 
tags: [type/person]
---
```

**Meeting Note**
```yaml
---
type: meeting
created: "{{date:YYYY-MM-DD}}"
attendees: []
project: 
action-items: []
tags: [type/meeting]
---
```

**MOC**
```yaml
---
type: moc
created: "{{date:YYYY-MM-DD}}"
domain: 
tags: [type/moc]
---
```

### File Naming Conventions

| Type | Convention | Example |
|------|-----------|---------|
| Fleeting | `YYYY-MM-DD - short title` | `2026-05-18 - idea for resume format` |
| Literature | `Author - Title` | `Karpathy - Intro to LLMs` |
| Evergreen | `Declarative statement` | `Retrieval practice beats re-reading` |
| Project | `PRJ - Name` | `PRJ - Wedding Planning` |
| Area | `AREA - Name` | `AREA - Personal Finance` |
| Person | `@ First Last` | `@ Andrej Karpathy` |
| Meeting | `MTG YYYY-MM-DD - Topic` | `MTG 2026-05-18 - Team Standup` |
| MOC | `MOC - Topic` | `MOC - Data Engineering` |
| Daily | `YYYY-MM-DD` | `2026-05-18` |
| Weekly | `W-YYYY-WW` | `W-2026-20` |

### Triage Flow

```
CAPTURE (any device/source)
    ↓
00_Inbox/ (with #status/inbox)
    ↓ [Morning triage — 10 min]
PROCESS: read, tag, link, decide destination
    ↓
┌─────────────────────────────────────────┐
│ Fleeting → stays in Inbox or → Evergreen│
│ Literature → 03_Resources/[domain]/     │
│ Project-related → 01_Projects/[name]/   │
│ Reference → 03_Resources/[domain]/      │
│ Actionable → daily note task list       │
│ Junk → delete                           │
└─────────────────────────────────────────┘
    ↓
Update status: #status/processing → #status/evergreen
Link to relevant MOC in 05_Atlas/
```

---

## 3. Capture-From-Anywhere Setup

### 3a. Android Share Intent (Pixel + Tablet)

**Option 1: Obsidian built-in Share (simplest)**

1. Install Obsidian from Play Store on both devices
2. Enable Obsidian Sync (see Section 4)
3. Any app → Share → Obsidian → it creates a note in your vault

**Option 2: Share to Obsidian via Tasker + Obsidian URI (power user)**

1. Install **Tasker** ($3.49, one-time) from Play Store
2. Create a Tasker Profile:
   - Trigger: `Event > System > Intent Received`
   - Action: `Browse URL`
   - URL: `obsidian://new?vault=SecondBrain&file=00_Inbox%2F{{date}}-capture&content={{clipboard_or_shared_text}}&append=true`

**Option 3 (recommended): Share via "Obsidian" directly + Quick Add plugin**

1. On Android, sharing to Obsidian deposits content into a note
2. With QuickAdd plugin configured (see below), set a capture macro:

```
QuickAdd Settings:
- Name: "Quick Capture"
- Type: Capture
- File Name: "00_Inbox/{{DATE:YYYY-MM-DD}}-{{VALUE:Title}}"
- Capture Format:
---
type: fleeting
created: "{{DATE:YYYY-MM-DD}}"
status: inbox
source: "{{VALUE:Source}}"
tags: [type/fleeting, status/inbox]
---

{{VALUE}}

- Enable: Create file if not found = ON
- Enable: Open file after capture = OFF
```

### 3b. WhatsApp Saved Messages

**Method**: Forward to yourself → Screenshot or copy text → Share to Obsidian

**Better method**: Use **Tasker + AutoShare** on Android:
1. Install AutoShare plugin for Tasker
2. When you share from WhatsApp → AutoShare intercepts → Tasker formats it → sends to Obsidian via URI

**Simplest method (recommended)**: Just share from WhatsApp → Obsidian directly. Android's native share sheet supports this once Obsidian is installed.

### 3c. Instagram Reels / YouTube Videos

**Instagram Reels**:
1. Copy reel link from Instagram
2. Share → Obsidian (or use QuickAdd capture)
3. The link gets saved to Inbox with timestamp

**YouTube Videos**:
1. Share → Obsidian directly
2. Or use the **Obsidian Web Clipper** (see below) when watching on desktop

**Template for media captures** (QuickAdd):
```markdown
---
type: literature
created: "{{DATE:YYYY-MM-DD}}"
status: inbox
source-type: "{{VALUE:Type (reel/video/article)}}"
source-url: "{{VALUE:URL}}"
domain: 
tags: [type/literature, status/inbox]
---

## Why I saved this
{{VALUE:Quick note on why}}

## Key points
- 

## Source
{{VALUE:URL}}
```

### 3d. Obsidian Web Clipper (Desktop Chrome/Edge)

1. Install **Obsidian Web Clipper** browser extension (official, free)
2. Configure in extension settings:

```
Vault: SecondBrain
Folder: 00_Inbox
```

**Custom template for web clipper** (paste into extension settings):

```markdown
---
type: literature
created: "{{date}}"
status: inbox
source-type: article
source-url: "{{url}}"
author: "{{author}}"
domain: 
tags: [type/literature, source/web, status/inbox]
---

# {{title}}

> Clipped from [{{domain}}]({{url}}) on {{date}}

## Highlights
{{highlights}}

## Content
{{content}}

## My Notes
- 
```

### 3e. Substack Posts

Two methods:

1. **Reading on desktop**: Use Obsidian Web Clipper (above) — clip the article
2. **Reading on Android**: Share → Obsidian → lands in Inbox
3. **Email newsletter**: See email workflow below

### 3f. Email-to-Vault Workflow

**Method: Cloudflare Email Workers → Obsidian Sync** (free tier works)

Simpler alternative — **recommended**:

1. Use **Readwise Reader** (free tier: 30 saves/month) or just forward to yourself
2. For Substack specifically: read in browser → Web Clipper
3. For emails you want to save: copy key content → QuickAdd capture on desktop

**Zero-cost method**:
1. Forward important emails to a Gmail label "Obsidian"
2. During daily triage, open labeled emails and paste into Obsidian via QuickAdd
3. This takes 2 min max per email

### 3g. Voice Capture

**On Android**:
1. Install **Google Recorder** (Pixel has this built-in — it auto-transcribes)
2. Record your thought
3. Copy transcript → Share → Obsidian

**On Desktop**:
1. Use Windows Voice Typing: `Win + H` to start dictating
2. Dictate directly into an Obsidian note or QuickAdd capture

**Plugin option**: Install **Audio Recorder** plugin in Obsidian to record directly into a note (audio file goes to `_Attachments/`).

### 3h. Desktop Global Hotkey Quick Capture

1. Install **QuickAdd** plugin
2. Create a Capture Choice called "Quick Capture"
3. Go to Obsidian Settings → Hotkeys → search "QuickAdd: Quick Capture"
4. Assign `Ctrl + Shift + I` (or your preference)
5. Now from anywhere in Obsidian, that hotkey opens a capture modal

**System-wide capture** (even when Obsidian is minimized):
1. Create a Windows shortcut with target:
```
obsidian://new?vault=SecondBrain&file=00_Inbox%2Fquick-capture&append=true
```
2. Right-click shortcut → Properties → set Shortcut Key to `Ctrl + Alt + N`
3. This opens Obsidian and creates/appends to a capture file

### 3i. Screenshots

1. Take screenshot on any device
2. Share → Obsidian (Android) or paste into note (Desktop)
3. Obsidian auto-saves images to `_Attachments/` (configure in Settings → Files & Links → Default location for new attachments → `_Attachments`)

### 3j. Browser Bookmarks Migration

1. Export bookmarks from Chrome: `chrome://bookmarks` → three dots → Export
2. Install **Obsidian Importer** plugin (community plugin)
3. Import bookmarks HTML file → creates notes in `00_Inbox/`
4. Triage during weekly review

---

## 4. Cross-Device Sync

### Comparison Table

| Feature | Obsidian Sync | Google Drive | Syncthing | Git |
|---------|--------------|--------------|-----------|-----|
| **Setup difficulty** | Trivial | Easy | Medium | Hard |
| **Android support** | Native | Possible via Dropsync | Native | Termux only |
| **Windows support** | Native | Native | Native | Native |
| **Conflict handling** | Built-in merge | Last-write-wins | File-level | Merge conflicts |
| **Speed** | Real-time | 1-5 min delay | Real-time | Manual |
| **End-to-end encryption** | Yes | No | Yes (in transit) | No |
| **Selective sync** | Yes | No | Yes | No |
| **Version history** | 12 months | 30 days | No | Full |
| **Cost** | $4/mo | Free | Free | Free |
| **Reliability with Obsidian** | Perfect | Sync bugs common | Good but fiddly | Breaks mobile |
| **Mobile battery impact** | Minimal | Moderate | Moderate | N/A |

### Recommendation: **Obsidian Sync** ($4/month)

**Why**: You have Android + Windows. Google Drive sync with Obsidian on Android is notoriously buggy (phantom duplicates, sync lag). Syncthing works but requires port forwarding or relay servers and background battery management on Android. Git on Android is painful. Obsidian Sync just works — it's purpose-built, handles conflicts intelligently, and is E2E encrypted.

### Setup Steps

**1. Purchase Obsidian Sync**
- Go to `obsidian.md/sync` → sign in → subscribe ($4/mo)

**2. Windows Desktop**
- Open Obsidian → Settings → Sync → Sign in
- Create new remote vault: name it `SecondBrain`
- Choose what to sync: enable everything except `.obsidian/workspace.json` (this stores window layout — different per device)
- Click "Start Syncing"

**3. Android Pixel**
- Install Obsidian from Play Store
- Open → Create vault → name it `SecondBrain`
- Settings → Sync → Sign in with same account
- Connect to existing remote vault `SecondBrain`
- Wait for initial sync (may take a few minutes)

**4. Android Tablet**
- Same steps as Pixel

### Gotchas & Fixes

- **Exclude from sync**: `.obsidian/workspace.json` (prevents layout fights between devices)
- **Battery**: Obsidian Sync is efficient, but on Android go to Settings → Apps → Obsidian → Battery → Unrestricted (prevents Android from killing sync)
- **Large attachments**: Sync has a 200MB total vault limit on the $4 plan. If you clip lots of images, upgrade to $8/mo (10GB) or keep images small
- **First sync**: Don't edit on multiple devices simultaneously until initial sync completes

---

## 5. Visual Design

### Theme: **AnuPpuccin**

**Why**: AnuPpuccin is the most customizable Obsidian theme. It supports Style Settings plugin for deep tweaking, has a warm color palette option, supports glassmorphism, rainbow folders, and looks great on both desktop and mobile. It achieves your "minimal + warm charcoal/champagne" aesthetic perfectly.

**Install**: Settings → Appearance → Themes → Browse → search "AnuPpuccin" → Install & Use

**Style Settings config** (after installing Style Settings plugin):

```
AnuPpuccin Settings:
- Color Scheme: Mocha (dark) / Latte (light)  
- Accent Color: Champagne (#F5E6CC) or Warm Gold (#D4A574)
- Extended Colorschemes: Enable → "Rosé Pine" for the warm charcoal feel
- Card Layout: Enable (gives notes a clean card look)
- Custom Background: Translucent → enable for subtle glassmorphism
- Rainbow Folders: Enable (color-codes your folder tree)
- File Browser: Relationship Lines → Enable
```

### CSS Snippets

Save each as a `.css` file in `.obsidian/snippets/`, then enable in Settings → Appearance → CSS Snippets.

**Snippet 1: Warm Charcoal Accent Overrides** — `warm-accents.css`

```css
/* Warm charcoal/champagne palette overrides */
.theme-dark {
  --background-primary: #1e1e1e;
  --background-secondary: #252525;
  --background-modifier-border: #3a3a3a;
  --text-accent: #d4a574;
  --text-accent-hover: #e8c49a;
  --interactive-accent: #d4a574;
  --interactive-accent-hover: #e8c49a;
}

/* Subtle warm glow on active note tab */
.workspace-tab-header.is-active {
  border-bottom: 2px solid #d4a574;
}
```

**Snippet 2: Beautiful Callouts** — `callouts.css`

```css
/* Glassmorphic callouts */
.callout {
  background: rgba(212, 165, 116, 0.08) !important;
  border-left: 4px solid var(--text-accent) !important;
  backdrop-filter: blur(8px);
  border-radius: 8px !important;
  margin: 1em 0;
}

.callout-title {
  font-weight: 600;
  color: var(--text-accent) !important;
}

/* Custom callout types */
.callout[data-callout="insight"] {
  border-left-color: #f0c674 !important;
}

.callout[data-callout="action"] {
  border-left-color: #81a2be !important;
}

.callout[data-callout="question"] {
  border-left-color: #b294bb !important;
}
```

**Snippet 3: Heading Styling** — `headings.css`

```css
/* Clean heading hierarchy with warm accents */
.markdown-rendered h1,
.cm-header-1 {
  font-size: 1.8em !important;
  color: var(--text-accent) !important;
  border-bottom: 1px solid rgba(212, 165, 116, 0.3);
  padding-bottom: 0.3em;
  margin-top: 1.5em;
}

.markdown-rendered h2,
.cm-header-2 {
  font-size: 1.4em !important;
  color: #e8c49a !important;
  margin-top: 1.3em;
}

.markdown-rendered h3,
.cm-header-3 {
  font-size: 1.15em !important;
  color: #c8c8c8 !important;
  font-style: italic;
}
```

**Snippet 4: Checkbox Styling** — `checkboxes.css`

```css
/* Custom checkbox states */
/* Standard done */
input[data-task="x"]:checked,
li[data-task="x"] > input:checked {
  background-color: #d4a574;
  border-color: #d4a574;
}

/* In-progress: / */
li[data-task="/"] > input[type="checkbox"] {
  background-color: rgba(212, 165, 116, 0.4);
  border-color: #d4a574;
}

/* Cancelled: - */
li[data-task="-"] {
  text-decoration: line-through;
  color: #666;
}

/* Important: ! */
li[data-task="!"] > input[type="checkbox"] {
  border-color: #cc6666;
  background-color: rgba(204, 102, 102, 0.3);
}
```

**Snippet 5: Embeds & Transclusions** — `embeds.css`

```css
/* Clean embedded notes */
.markdown-embed {
  border: 1px solid rgba(212, 165, 116, 0.2) !important;
  border-left: 3px solid var(--text-accent) !important;
  border-radius: 8px !important;
  padding: 1em !important;
  background: rgba(30, 30, 30, 0.6) !important;
  margin: 0.8em 0;
}

.markdown-embed-title {
  color: var(--text-accent) !important;
  font-weight: 600;
}
```

**Snippet 6: Graph Node Colors** — `graph-colors.css`

```css
/* Graph view coloring handled via Obsidian graph settings, 
   but this enhances the background */
.graph-view.color-fill-attachment {
  color: #d4a574;
}

.graph-view.color-fill-tag {
  color: #b294bb;
}

/* Smoother graph animation */
.graph-view canvas {
  border-radius: 12px;
}
```

### Iconize Plugin Config

Install **Iconize** plugin, then:
- `00_Inbox` → icon: `lucide-inbox`
- `01_Projects` → icon: `lucide-rocket`
- `02_Areas` → icon: `lucide-compass`
- `03_Resources` → icon: `lucide-library`
- `04_Archive` → icon: `lucide-archive`
- `05_Atlas` → icon: `lucide-map`
- `06_Calendar` → icon: `lucide-calendar`
- `07_People` → icon: `lucide-users`
- `_Templates` → icon: `lucide-file-text`
- `_Attachments` → icon: `lucide-paperclip`
- `_Meta` → icon: `lucide-settings`

### Graph View Settings

Open Graph View (Ctrl+G), click the gear icon:

| Setting | Value |
|---------|-------|
| Depth | 2 |
| Show orphans | OFF |
| Show attachments | OFF |
| Show tags | ON |

**Color Groups** (add in graph settings):
- `path:01_Projects` → Orange `#d4a574`
- `path:02_Areas` → Blue `#81a2be`
- `path:03_Resources` → Green `#b5bd68`
- `path:05_Atlas` → Purple `#b294bb`
- `path:06_Calendar` → Gray `#969896`
- `tag:#type/evergreen` → Gold `#f0c674`

---

## 6. Plugin Stack

### Essential (install on Day 1)

| Plugin | Rationale |
|--------|-----------|
| **Dataview** | Powers the dashboard, queries, and all dynamic views. Non-negotiable. |
| **QuickAdd** | Multi-format capture with templates. Your primary input mechanism. |
| **Templater** | Dynamic templates with dates, prompts, and logic. Core templates depend on this. |
| **Calendar** | Visual calendar in sidebar for daily/weekly note navigation. |
| **Periodic Notes** | Manages daily + weekly note creation with templates. |
| **Style Settings** | Deep theme customization for AnuPpuccin. |
| **Obsidian Web Clipper** | Browser extension for capturing web content. (Install in browser, not Obsidian) |

### Recommended (install on Day 3-4)

| Plugin | Rationale |
|--------|-----------|
| **Iconize** | Folder and file icons. Visual clarity in file tree. |
| **Homepage** | Auto-opens your dashboard on vault launch. |
| **Kanban** | Visual project boards for wedding planning, job search tracking. |
| **Tag Wrangler** | Rename, merge, and manage tags across vault. Prevents tag sprawl. |
| **Natural Language Dates** | Type `@tomorrow` or `@next friday` in notes. Speeds up date entry. |
| **Paste URL into Selection** | Select text, paste a URL, auto-creates markdown link. Tiny but saves time. |

### Optional (install if needed, Week 2+)

| Plugin | Rationale |
|--------|-----------|
| **Excalidraw** | Whiteboard/diagrams inside notes. Useful for system design or architecture thinking. |
| **Tasks** | If you want Obsidian to replace a task manager — queries tasks across all notes. |
| **Book Search** | Auto-fills book metadata from Google Books. Only if you read a lot. |
| **Readwise Official** | If you use Readwise/Reader to capture highlights. Syncs automatically. |
| **Advanced Tables** | Tab-to-navigate table editing. Install if you make many tables. |
| **Audio Recorder** | Record voice notes directly in Obsidian. |

**What I deliberately excluded**: Obsidian Git (you're using Sync), Sliding Panes (built-in now), Outliner (niche), Mind Map (Excalidraw is better), Daily Stats (Dashboard covers it), Admonitions (native callouts replaced this).

---

## 7. Templates

### 7a. Daily Note

Save as `_Templates/Daily Note.md`:

```markdown
---
type: daily
created: "{{date:YYYY-MM-DD}}"
tags: [type/daily]
---

# {{date:dddd, MMMM D, YYYY}}

## Focus
> What's the ONE thing that matters today?

- 

## Tasks
- [ ] 
- [ ] 
- [ ] 

## Captures
> Quick thoughts, links, ideas — process during evening shutdown

- 

## Log
> What happened today? Key events, conversations, wins.



## Gratitude
1. 
2. 
3. 

---
**Yesterday**: [[{{date-1d:YYYY-MM-DD}}]] | **Tomorrow**: [[{{date+1d:YYYY-MM-DD}}]]
```

### 7b. Weekly Review

Save as `_Templates/Weekly Review.md`:

```markdown
---
type: weekly
created: "{{date:YYYY-MM-DD}}"
week: "{{date:YYYY-[W]WW}}"
tags: [type/weekly]
---

# Week {{date:WW}} Review — {{date:YYYY}}

## Review
### What went well?
- 

### What didn't go well?
- 

### What did I learn?
- 

## Inbox Triage
> Process everything in 00_Inbox. Move, tag, link, or delete.

```dataview
TABLE status, source, created
FROM "00_Inbox"
SORT created DESC
LIMIT 20
```

## Active Projects Check
```dataview
TABLE status, deadline
FROM "01_Projects"
WHERE status = "active"
SORT deadline ASC
```

## This Week's Notes
```dataview
LIST
FROM "06_Calendar/Daily"
WHERE created >= date("{{date:YYYY-MM-DD}}") - dur(7 days)
SORT created DESC
```

## Next Week
### Top 3 priorities
1. 
2. 
3. 

### Habits to maintain
- [ ] Exercise 3x
- [ ] Read 30 min/day
- [ ] Process inbox daily
```

### 7c. Project

Save as `_Templates/Project.md`:

```markdown
---
type: project
created: "{{date:YYYY-MM-DD}}"
status: active
area: 
deadline: 
domain: 
tags: [type/project]
---

# PRJ - {{title}}

## Outcome
> What does "done" look like? One sentence.



## Why
> Why does this matter? What happens if I don't do it?



## Tasks
- [ ] 
- [ ] 
- [ ] 

## Resources
- 

## Notes
- 

## Log
### {{date:YYYY-MM-DD}}
- Project created

---
**Area**: [[]]
**Related**: 
```

### 7d. Area

Save as `_Templates/Area.md`:

```markdown
---
type: area
created: "{{date:YYYY-MM-DD}}"
domain: 
review-cycle: monthly
tags: [type/area]
---

# AREA - {{title}}

## Purpose
> What is this area of responsibility about?



## Standards
> What does "good enough" look like here?

- 

## Active Projects
```dataview
LIST
FROM "01_Projects"
WHERE area = this.file.name
WHERE status = "active"
```

## Key Resources
- 

## Review Notes
### {{date:YYYY-MM-DD}}
- Area created
```

### 7e. Literature Note (Flexible — article/book/podcast/video)

Save as `_Templates/Literature Note.md`:

```markdown
---
type: literature
created: "{{date:YYYY-MM-DD}}"
status: inbox
source-type: "{{VALUE:Type — article/book/podcast/video/reel/substack}}"
source-url: ""
author: ""
domain: 
rating: 
key-takeaways: 
tags: [type/literature]
---

# {{title}}

## Summary
> 2-3 sentences: what is this about?



## Key Takeaways
1. 
2. 
3. 

## Notes
> Detailed notes, quotes, and reactions



## How This Connects
> Link to existing notes and MOCs

- Related: [[]]
- MOC: [[]]

## Action Items
- [ ] 

---
**Source**: [Link]({{VALUE:URL}})
**Author**: {{VALUE:Author}}
```

### 7f. Person

Save as `_Templates/Person.md`:

```markdown
---
type: person
created: "{{date:YYYY-MM-DD}}"
company: 
role: 
context: 
last-contact: "{{date:YYYY-MM-DD}}"
tags: [type/person]
---

# @ {{title}}

## Context
> How do I know this person? Why are they in my vault?



## Key Info
- **Company**: 
- **Role**: 
- **Email**: 
- **LinkedIn**: 

## Interactions
### {{date:YYYY-MM-DD}}
- First added to vault

## Notes
- 

## Shared Interests / Topics
- 
```

### 7g. Meeting

Save as `_Templates/Meeting.md`:

```markdown
---
type: meeting
created: "{{date:YYYY-MM-DD}}"
attendees: []
project: 
action-items: []
tags: [type/meeting]
---

# MTG {{date:YYYY-MM-DD}} — {{title}}

## Attendees
- 

## Agenda
1. 
2. 

## Notes


## Decisions
- 

## Action Items
- [ ] @person — task — due date
- [ ] 

---
**Project**: [[]]
```

### 7h. Fleeting Note

Save as `_Templates/Fleeting.md`:

```markdown
---
type: fleeting
created: "{{date:YYYY-MM-DD}}"
status: inbox
source: thought
domain: 
tags: [type/fleeting, status/inbox]
---

# {{title}}

{{cursor}}

---
*Process this: convert to evergreen, link to MOC, or delete.*
```

### 7i. MOC (Map of Content)

Save as `_Templates/MOC.md`:

```markdown
---
type: moc
created: "{{date:YYYY-MM-DD}}"
domain: 
tags: [type/moc]
---

# MOC — {{title}}

## Overview
> What is this domain about? Why do I care?



## Core Concepts
> The foundational ideas in this domain

- [[]]
- [[]]

## Projects
```dataview
LIST
FROM "01_Projects"
WHERE domain = this.domain
WHERE status = "active"
```

## Key Resources
```dataview
TABLE author, rating, source-type
FROM "03_Resources"
WHERE domain = this.domain
SORT rating DESC
LIMIT 15
```

## Open Questions
- 

## Related MOCs
- [[]]
```

---

## 8. Home Dashboard

Save as `_Meta/Home.md`:

```markdown
---
type: dashboard
tags: [type/dashboard]
cssclasses: [dashboard]
---

# Second Brain

> *{{date:dddd, MMMM D, YYYY}}*

---

## Today's Focus
![[{{date:YYYY-MM-DD}}#Focus]]

---

## Active Projects

```dataview
TABLE WITHOUT ID
  file.link AS "Project",
  status AS "Status",
  deadline AS "Deadline",
  area AS "Area"
FROM "01_Projects"
WHERE status = "active"
SORT deadline ASC
```

---

## Inbox (needs processing)

```dataview
TABLE WITHOUT ID
  file.link AS "Note",
  type AS "Type",
  source AS "Source",
  created AS "Captured"
FROM "00_Inbox"
SORT created DESC
LIMIT 10
```

> **Inbox count**: `$= dv.pages('"00_Inbox"').length` items waiting

---

## Recent Captures (last 7 days)

```dataview
TABLE WITHOUT ID
  file.link AS "Note",
  type AS "Type",
  domain AS "Domain"
FROM ""
WHERE created >= date(today) - dur(7 days)
WHERE type != "daily" AND type != "weekly" AND type != "dashboard"
SORT created DESC
LIMIT 10
```

---

## Maps of Content

```dataview
LIST
FROM "05_Atlas"
WHERE type = "moc"
SORT file.name ASC
```

---

## Upcoming Reviews

```dataview
TABLE WITHOUT ID
  file.link AS "Area",
  review-cycle AS "Cycle"
FROM "02_Areas"
WHERE type = "area"
SORT file.name ASC
```

---

## Knowledge Stats

> - Total notes: `$= dv.pages().length`
> - Evergreen notes: `$= dv.pages().where(p => p.type === "evergreen").length`
> - Literature notes: `$= dv.pages().where(p => p.type === "literature").length`
> - Projects (active): `$= dv.pages('"01_Projects"').where(p => p.status === "active").length`
> - Inbox items: `$= dv.pages('"00_Inbox"').length`
> - People: `$= dv.pages('"07_People"').length`

---

## Quick Actions

> - [[00_Inbox/Quick Capture|Capture a thought]]
> - [[06_Calendar/Daily/{{date:YYYY-MM-DD}}|Today's daily note]]
> - [[05_Atlas/MOC - Data Engineering|Data Engineering MOC]]
> - [[05_Atlas/MOC - Gen AI|Gen AI MOC]]
> - [[05_Atlas/MOC - Job Search|Job Search MOC]]
> - [[01_Projects/PRJ - Wedding Planning|Wedding Planning]]

```

**Homepage plugin config**: Set `_Meta/Home` as the homepage so it opens on vault launch.

**Dashboard CSS** — save as `.obsidian/snippets/dashboard.css`:

```css
/* Dashboard-specific styling */
.dashboard .markdown-rendered {
  max-width: 900px;
  margin: 0 auto;
}

.dashboard .markdown-rendered h1 {
  text-align: center;
  font-size: 2em;
  margin-bottom: 0.2em;
}

.dashboard .markdown-rendered h2 {
  border-bottom: 1px solid rgba(212, 165, 116, 0.3);
  padding-bottom: 0.3em;
  margin-top: 2em;
}

.dashboard .dataview.table-view-table {
  font-size: 0.9em;
}

.dashboard blockquote {
  border-left-color: var(--text-accent) !important;
  background: rgba(212, 165, 116, 0.05);
  border-radius: 4px;
  padding: 0.5em 1em;
}
```

---

## 9. Daily and Weekly Workflow

### Morning Capture Review (10 min)

```
08:00  Open Obsidian → Dashboard loads automatically
       1. Check inbox count on dashboard
       2. Open 00_Inbox — scan each note:
          - Junk? → Delete
          - Actionable? → Add to today's daily note tasks
          - Knowledge? → Move to 03_Resources, tag, link to MOC
          - Project-related? → Move to 01_Projects/[name]
       3. Open today's daily note → write Focus for the day
       4. Done. Close inbox. Start working.
```

### Evening Shutdown (5 min)

```
18:00  Open today's daily note
       1. Log section: write 2-3 bullets about the day
       2. Check off completed tasks, move incomplete to tomorrow
       3. Gratitude: write 3 things
       4. Capture section: process any quick captures from the day
       5. Close Obsidian. Day is done.
```

### Weekly Review (30 min, Sunday evening)

```
Sunday 19:00
       1. Create weekly review note (Periodic Notes does this)
       2. Answer the three reflection questions
       3. Full inbox triage — process EVERYTHING in 00_Inbox
       4. Review active projects — update statuses, deadlines
       5. Check Areas — anything need attention?
       6. Review this week's daily notes — extract any evergreen ideas
       7. Set top 3 priorities for next week
       8. Quick graph view scan — any orphan notes to link?
```

### Monthly MOC Tending (45 min, 1st Sunday of month)

```
1st Sunday 19:00
       1. Open each MOC in 05_Atlas
       2. Add new notes created this month to relevant MOCs
       3. Identify gaps — any topics I'm learning about without a MOC?
       4. Create new MOCs if needed
       5. Review Areas — update review notes
       6. Archive completed projects → move to 04_Archive
       7. Tag cleanup — use Tag Wrangler to merge/rename messy tags
       8. Backup check — verify Sync is working on all devices
```

---

## 10. Seven-Day Rollout Plan

### Day 1 (Monday) — Foundation

- [ ] Install Obsidian on Windows desktop
- [ ] Create vault named `SecondBrain`
- [ ] Create all top-level folders (copy from Section 1)
- [ ] Settings → Files & Links → Default location for new attachments → `_Attachments`
- [ ] Settings → Files & Links → New file location → `00_Inbox`
- [ ] Install essential plugins: Dataview, QuickAdd, Templater, Calendar, Periodic Notes, Style Settings
- [ ] Install AnuPpuccin theme
- [ ] Configure Style Settings per Section 5
- [ ] Purchase and enable Obsidian Sync
- [ ] Create remote vault, start syncing

### Day 2 (Tuesday) — Templates & Capture

- [ ] Create all 9 templates in `_Templates/` (copy from Section 7)
- [ ] Configure Templater: Template folder → `_Templates`
- [ ] Configure Periodic Notes: Daily note template → `_Templates/Daily Note`, folder → `06_Calendar/Daily`
- [ ] Configure Periodic Notes: Weekly note template → `_Templates/Weekly Review`, folder → `06_Calendar/Weekly`
- [ ] Set up QuickAdd capture macros (Section 3a)
- [ ] Set up desktop hotkey `Ctrl+Shift+I` for quick capture
- [ ] Install Obsidian Web Clipper on Chrome/Edge, configure template (Section 3d)
- [ ] Create your first daily note — test the template

### Day 3 (Wednesday) — Mobile & Sync

- [ ] Install Obsidian on Android Pixel
- [ ] Connect to Obsidian Sync, wait for full sync
- [ ] Test: share a link from Chrome → Obsidian on Android
- [ ] Test: share from WhatsApp → Obsidian
- [ ] Test: share from YouTube → Obsidian
- [ ] Test: share from Instagram → Obsidian
- [ ] Install Obsidian on Android tablet, connect Sync
- [ ] Set battery optimization to "Unrestricted" for Obsidian on both Android devices
- [ ] Verify a note created on phone appears on desktop and tablet

### Day 4 (Thursday) — Visual Polish & Navigation

- [ ] Create all CSS snippets from Section 5, save to `.obsidian/snippets/`
- [ ] Enable all snippets in Settings → Appearance
- [ ] Install Iconize plugin, set folder icons (Section 5)
- [ ] Configure Graph View color groups (Section 5)
- [ ] Install Homepage plugin, set `_Meta/Home` as homepage
- [ ] Create the Home dashboard (copy from Section 8)
- [ ] Install remaining recommended plugins: Tag Wrangler, Natural Language Dates, Paste URL, Kanban

### Day 5 (Friday) — Content Migration

- [ ] Export Chrome bookmarks, import via Obsidian Importer → triage into `03_Resources`
- [ ] Go through WhatsApp saved messages — capture the important ones to Inbox
- [ ] Go through Instagram saved reels — capture key ones as literature notes
- [ ] Skim Notion — identify the 10-20 most valuable pages, recreate in Obsidian
- [ ] Don't try to migrate everything — just the living, useful content

### Day 6 (Saturday) — Structure & MOCs

- [ ] Create initial MOCs in `05_Atlas/` for your top domains:
  - [ ] MOC - Data Engineering
  - [ ] MOC - Gen AI
  - [ ] MOC - Data Science
  - [ ] MOC - Job Search
  - [ ] MOC - Fitness
  - [ ] MOC - Personal Finance
  - [ ] MOC - Cooking
  - [ ] MOC - Wedding Planning
- [ ] Create Area notes in `02_Areas/`:
  - [ ] AREA - Career
  - [ ] AREA - Health & Fitness
  - [ ] AREA - Finance
  - [ ] AREA - Cooking & Diet
- [ ] Create active Project notes in `01_Projects/`:
  - [ ] PRJ - Job Search 2026
  - [ ] PRJ - Wedding Planning
- [ ] Link everything: projects → areas → MOCs
- [ ] Do your first weekly review using the template

### Day 7 (Sunday) — Workflow Lock-in

- [ ] Do a full morning review ritual (Section 9) — time it
- [ ] Do an evening shutdown ritual — time it
- [ ] Do a mini weekly review
- [ ] Test capture from every source: Android share, web clipper, voice, screenshot, WhatsApp
- [ ] Verify all Dataview queries on dashboard are working
- [ ] Check graph view — everything connected?
- [ ] Delete any test notes cluttering the vault
- [ ] Set a recurring Sunday 19:00 calendar reminder: "Weekly Review in Obsidian"
- [ ] Set a recurring 1st-Sunday-of-month reminder: "Monthly MOC Tending"
- [ ] You're operational. Start using it for real.

---

## Quick Reference Card

```
CAPTURE:     Ctrl+Shift+I (desktop) | Share→Obsidian (Android) | Web Clipper (browser)
DAILY NOTE:  Click today on Calendar sidebar
FIND:        Ctrl+O (quick switcher) | Ctrl+Shift+F (vault search)
LINK:        [[ + start typing
TAG:         # + type/ or domain/ or status/
PROCESS:     Morning 10 min: triage inbox. Evening 5 min: log + shutdown.
WEEKLY:      Sunday 30 min: review + plan.
GRAPH:       Ctrl+G
DASHBOARD:   Click home icon or open _Meta/Home
```

---

*This system prioritizes working over perfection. Start capturing on Day 2. Refine as you go. The best PKM system is the one you actually use.*
