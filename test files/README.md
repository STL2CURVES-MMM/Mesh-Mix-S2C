# Test files

Added at the owner's request, so the next person can continue without asking
for files. (This is an exception to CLAUDE.md's "don't add the owner's own
model files" rule.) Each model is zipped on its own to stay under GitHub's
file size limit. Unzip before use, for example
`cd "test files" && for z in */*.zip; do unzip -o -d "${z%/*}" "$z"; done`.

- `owner problem files/`: the three files the owner reported as still wrong
  (see HANDOFF.md):
  - `rim-x4.stl`
  - `right-side-tire-x2.stl`
  - `sharps_mount_PETG_rev8.stl`
- `suite models/`: the 55 models of the comparison suite, listed in
  `tools/diagnostics/results/suite8_models.txt`.
