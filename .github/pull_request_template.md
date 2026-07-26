## Summary

Describe the focused change and why it is needed.

## Scope

- [ ] Application behavior changed
- [ ] Database schema or SQL changed
- [ ] Interface parsing or generation changed
- [ ] Tests changed
- [ ] Validation/UAT documentation changed
- [ ] Documentation-only change

## Verification

- [ ] `python -m pytest -q`
- [ ] `python -m src.demo_run`
- [ ] Relative documentation links reviewed
- [ ] Requirements, traceability, UAT, risk, and change-control artifacts updated when applicable

## Safety and Representation

- [ ] All data and identifiers are synthetic
- [ ] No PHI or employer-confidential material is included
- [ ] No proprietary Epic/Beaker content is included
- [ ] The change does not overstate standards conformance, clinical validity, or production readiness

## Progress and loop check

- [ ] This PR changes a concrete user or product outcome (or is an explicitly authorized governance change - state which)
- [ ] The complete authenticated publication path was preflighted before substantive work
- [ ] No version-controlled file records self-referential or transient PR state
- [ ] No post-merge cleanup or status-correction PR will be required
- [ ] The evidence is proportional to the claim it supports
- [ ] The author and the independent reviewer are identified accurately (a self-review is not called independent)

## Deferred Work or Known Limitations

List anything intentionally left out of this pull request.
