# Layer Group Tab Redesign — BasemapInputDialog

**Date:** 2026-07-12
**Scope:** `BasemapInputDialog` Layer Group tab in `basemaps_dialog.py`

## Goal

Redesign the Layer Group tab in the Add/Edit Basemap dialog for a leaner, more
direct editing flow:

1. Remove the Update button; rename Add → New. New appends a filled source;
   editing a selected source updates it in real time (on focus loss).
2. Show a layer-type icon per source row (reuse Browser panel icon pattern).
3. Compress the source list height (~half) and widen the dialog.
4. Collapse the Layer Metadata (optional) group by default.

## Current State (file:line)

- `_build_tabs` Layer Group tab: `basemaps_dialog.py:4760-4837`
- Group source management methods: `basemaps_dialog.py:4846-4924`
  - `_on_group_add_source` (4854-4880): Add/Update toggle via
    `_editing_source_index` + button text swap
  - `_on_group_remove_source` (4882-4893)
  - `_on_group_source_selected` (4895-4909): fills editor fields, swaps button
    to "Update"
  - `_refresh_group_sources_list` (4911-4920): plain-text labels, **no icon**
- Metadata group: `basemaps_dialog.py:4586-4630` — always expanded
- `_update_ok_state`: `basemaps_dialog.py:4928-4947`
- Icon pattern to reuse: `browser_items.py:859-869`
  - raster → `QgsApplication.getThemeIcon("mIconXyz.svg")`
  - vector → `QgsApplication.getThemeIcon("mIconVectorTileLayer.svg")`
- `QgsApplication` imported at `basemaps_dialog.py:22`
- `MessageBox.warning(text, title, parent)` API (`messageTool.py:398-421`)

## Design

### 1. New button + real-time update (no empty records)

**New button** (`group_add_btn`, text "New"):
- Validate: `group_src_url_edit` and `group_src_name_edit` non-empty. If either
  empty → `MessageBox.warning(self.tr("Please fill in Source URL and Source Name."), self.tr("Warning"), self)` and abort. Do NOT append an empty record.
- On success: build `source = {"url":..., "source_name":..., ["source_type":"raster"]}`
  (raster when combo index == 1; vector omits the key, same as today).
  Append to `_group_sources`, refresh list, clear URL + Name fields (keep Tile
  Type combo value so the next New inherits the last type), clear list selection,
  re-focus URL field.

**Selecting a row** (`_on_group_source_selected`):
- Before populating editor fields, commit any pending edits of the current
  input fields to the previously selected row (call `_commit_current_source()`).
- Fill url/name/tile_type from the selected source. No button text swap (button
  stays "New"). `group_remove_btn` enabled when a row is selected.

**Real-time update** (new method `_commit_current_source`):
- If no row selected (`currentRow() < 0` or out of range) → return.
- Read `group_src_url_edit`, `group_src_name_edit`, `group_src_type_combo`.
- Write back to `_group_sources[currentRow()]`. Refresh only the affected row's
  text + icon in the list (cheap in-place update; keep current selection).
- Wired to: `group_src_url_edit.editingFinished`,
  `group_src_name_edit.editingFinished`, and
  `group_src_type_combo.currentIndexChanged` (combo commits immediately on
  switch, per user requirement).
- `editingFinished` fires on focus loss / Enter press — matches the "失焦更新"
  requirement.

**Remove button** (`_on_group_remove_source`): unchanged behavior; after
removal clear fields, disable Remove, clear selection.

**Remove `_editing_source_index`** entirely and all button-text swaps to
"Update"/"Add". The button label is always "New".

### 2. Per-source type icon

In `_refresh_group_sources_list`, before `addItem`:
```python
icon = (QgsApplication.getThemeIcon("mIconXyz.svg")
        if source.get("source_type") == "raster"
        else QgsApplication.getThemeIcon("mIconVectorTileLayer.svg"))
item.setIcon(icon)
```
For in-place row refresh (real-time update path), set icon on the existing
`QListWidgetItem` via `self.group_sources_list.item(row).setIcon(icon)`.

### 3. List height compression + dialog resize

- `group_sources_list.setMaximumHeight(120)` (roughly half the current
  expanding height).
- Change `group_layout.addWidget(self.group_sources_list, 1)` → remove the
  stretch factor so the list no longer dominates the layout.
- At end of `BasemapInputDialog.__init__`:
  `self.resize(560, 420)` and `self.setMinimumSize(520, 380)` — wider and
  shorter than the current auto-sized default.

### 4. Collapsible Layer Metadata group

- `meta_group.setCheckable(True)`, `meta_group.setChecked(False)` (collapsed by
  default).
- Move the four field layouts (website / copyright / terms / description) into
  a container `QWidget` inside `meta_group`; connect
  `meta_group.toggled` → `container.setVisible(checked)`.
- When unchecked, only the group box title row is shown, reclaiming vertical
  space.

### 5. OK validation (防呆)

- Override `accept()` (or intercept `button_box.accepted`): before calling
  `super().accept()`:
  1. Commit current source (`_commit_current_source()`) so pending edits are
     captured.
  2. Validate Name non-empty.
  3. If on Layer Group tab: every source in `_group_sources` must have non-empty
     `url` and `source_name`.
  4. On any failure: `MessageBox.warning(...)` listing the missing fields
     (e.g. "Source #2 is missing URL"), and return without accepting.
- `_update_ok_state` keeps its existing coarse-grained enable/disable of the OK
  button (Name + tab-appropriate URL/source presence); the final `accept()`
  guard is the authoritative check.

## Data Compatibility

- The `_group_sources` dict shape is unchanged:
  `{"url", "source_name", "source_type"?}`. `get_data()` and YAML persistence
  are untouched.
- No changes to `config_loader.py`, `browser_items.py`, or any caller.

## Files Changed

- `basemaps_dialog.py` only:
  - `BasemapInputDialog.__init__` (resize, metadata collapse, accept guard)
  - `_build_tabs` Layer Group section (New button text, list max height,
    signal wiring, remove stretch)
  - `_on_group_add_source` → renamed behavior to New (validate, append, clear)
  - `_on_group_source_selected` (commit-then-fill, no Update text swap)
  - new `_commit_current_source`
  - `_refresh_group_sources_list` (add icon)
  - remove `_editing_source_index` and all "Update"/"Add" text swaps
  - `_update_ok_state` (no behavior change beyond what's needed)
  - new `accept()` override

## Out of Scope

- Single Layer tab, WMS fields — untouched.
- Drag-and-drop / Browser panel behavior — untouched.
- Translation `.ts` updates — "New"/"Update" strings already exist in the file;
  the string "New" is already used elsewhere in the UI. If `pylupdate` is run
  later, the new tr() calls will be picked up; not part of this change.