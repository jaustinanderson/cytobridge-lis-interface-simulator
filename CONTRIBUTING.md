# Contributing to CytoBridge

CytoBridge is a focused portfolio project demonstrating LIS/interface-analysis, validation, SQL, and synthetic cytogenetics/FISH workflow concepts. Contributions should strengthen that purpose without overstating clinical capability.

## Before Making a Change

- Read the README, validation package, and known issues.
- Keep the project synthetic-data only.
- Do not add PHI, real patient identifiers, employer-confidential information, internal procedures, screenshots from clinical systems, or proprietary Epic/Beaker content.
- Preserve the explicit boundary: this is educational, Beaker-adjacent learning, not Epic build experience or a production clinical application.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m src.demo_run
```

On Windows, activate the environment with `.venv\Scripts\activate`.

## Change Workflow

1. Create a focused branch from `main`.
2. Make the smallest coherent change.
3. Add or update tests when behavior changes.
4. Update requirements, traceability, UAT, risk, or change-control documents when the validated behavior or scope changes.
5. Run the complete test suite and demo.
6. Open a pull request explaining scope, verification, safety boundaries, and any deferred work.

## Pull-Request Expectations

A pull request should state:

- What changed and why
- Whether application behavior, schema, tests, sample messages, or documentation changed
- Test and demo results
- Whether requirements or UAT artifacts require updates
- Confirmation that all data is synthetic and no PHI is present
- Any limitations or intentionally deferred work

## Code and Data Rules

- Runtime dependencies should remain minimal and justified.
- SQL must remain explicit and reviewable.
- Database writes should preserve existing integrity constraints and audit behavior.
- Interface examples must use clearly fictional identifiers and local training codes.
- New parser or mapping behavior must include positive and negative tests.
- Avoid silent partial processing when the safer behavior is to reject or queue an invalid message.

## Documentation Rules

- Use plain, readable Markdown.
- Keep relative links valid.
- Distinguish implemented behavior from roadmap items.
- Do not claim standards conformance, clinical validation, Epic expertise, or production readiness without evidence.
- Update `validation/change-control-log.md` for material validated-scope changes.

## Scope Control

Large additions such as a UI, additional panels, a production-grade HL7 engine, authentication, or deployment infrastructure should be proposed separately. They should not be bundled into unrelated maintenance or documentation changes.
