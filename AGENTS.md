## Role

Answer in Chinese unless the user explicitly asks for English.

This project is an independent Codex skill package for local frame timing optimization. Keep it separate from any previous source project and do not import from it.

## Scope

The skill assumes input frames are already clean and extracted.

Do:
- detect static and fast-motion ranges;
- generate timing strategies;
- write model-safe copied output frames;
- generate human review and health artifacts;
- verify output provenance.

Do not:
- remove watermarks;
- OCR engineering overlays;
- modify image pixels;
- run 3D reconstruction;
- upload to cloud;
- include private videos, paths, or project data.

## Development Rules

- Use tests before implementation for behavior changes.
- Keep outputs under `agent_files/`.
- Keep generated demo data out of git unless it is tiny and intentionally included.
- Run the full test suite before claiming completion.
- Avoid hard-coded absolute paths.
