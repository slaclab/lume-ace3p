# External reference material

## ACE3P command references

Eight SLAC ACD command-syntax references, one per ACE3P module plus the shared
`acdtool` utility. These are the authoritative specification of each module's
input containers and of `acdtool`'s command surface. Publicly available; copied
here from the CW23 tutorial archive on 2026-08-13 so the repo does not depend on
an external directory.

| File | Pages | Covers |
|---|---|---|
| `acdtool-commands.pdf` | 32 | 19 commands (3 top-level, 5 `mesh`, 11 `postprocess`); all 24 `.rfpost` blocks |
| `omega3p-commands.pdf` | 15 | Frequency-domain eigensolver |
| `s3p-commands.pdf` | 13 | S-parameter / frequency-scan solver |
| `t3p-commands.pdf` | 25 | Time-domain solver, monitors, wakefields |
| `track3p-commands.pdf` | 16 | Particle tracking, multipacting, dark current |
| `pic3p-commands.pdf` | 16 | Particle-in-cell solver |
| `gun3p-commands.pdf` | 17 | DC gun / electrostatic / magnetostatic + tracker |
| `TEM3P-commands.pdf` | 24 | Thermal / mechanical / multiphysics |

### What they do and do not tell you

**They specify inputs, not outputs.** Every module reference ends with *"Refer to
acdtool command syntax for postprocessing capabilities"*, and none documents its
own output file formats — S3P's `Reflection.out`, `SParameter.out` and
`PortRef<n>_<m>.out` are undocumented, as are `acdtool`'s `kickFactor` and
`maxFieldsOnSurface` blocks. Output formats have to come from real runs; the
frozen examples in `tests/fixtures/acdtool/` are this repo's substitute.

**`JobName` is not a documented input container** for omega3p, s3p, t3p, track3p,
pic3p or TEM3P. Only `gun3p` documents it, inside its `Tracker` container, noting
it must match the name used in the job submission script. The per-solver default
(`omega3p_results`, `s3p_results`, `t3p_results`, …) is the authoritative results
directory, and the real override lives in the batch script rather than the input
file.

`docs/acdtool_reference.md` transcribes the parts of `acdtool-commands.pdf` that
this codebase depends on. `docs/acdtool_rework_plan.md` records where these
documents corrected earlier reverse-engineered assumptions.

### Extracting text

No `pdftotext`/poppler is assumed to be present. With `pypdf`:

```python
import pypdf
pages = pypdf.PdfReader('references/t3p-commands.pdf').pages
text = '\n'.join(p.extract_text() or '' for p in pages)
```

The **module** references are slide-derived and collapse line breaks into runs of
tabs — replace tabs with newlines before reading, or the output is one long line.
`acdtool-commands.pdf` is document-derived and extracts normally.
