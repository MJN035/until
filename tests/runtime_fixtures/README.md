# Assignment Runtime golden fixtures

These fixtures freeze Phase 0 input/output expectations without depending on a
particular official CLI. Kernel acceptance tests use a mock `LocalAgent` to
exercise probe, plan preview, approval-bound execution, validation, and repair.

Fixture rules:

- All identifiers, paths, commands, and results are fixed and deterministic.
- Paths are repository-relative POSIX paths; no absolute paths or user data are
  stored.
- `source_inputs` must exist unless the scenario intentionally declares a
  missing input.
- `generated_artifacts` are declarations for the mock plugin. They are not
  checked-in fake PPTX/DOCX/ZIP files.
- `network_allowed` is always false. A fixture requesting network access must
  be blocked before execution.
- `approval_granted=false` guarantees execute is not called and packaging is
  `not_run`.
- A submission bundle may be created only after an approved successful run and
  when `expected.package_status` is `pass`.

The ten cases cover report, HDL, data/Rmd, presentation, and form assignments
twice each. Across the catalog they include normal operation, missing input,
policy blocking, run failure, packaging failure, and honest unsupported
termination.
