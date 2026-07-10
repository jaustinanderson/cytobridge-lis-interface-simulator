# Security Policy

## Project Scope

CytoBridge is an educational, synthetic-data LIS and interface simulator. It is not a production clinical system, is not intended for diagnostic or operational use, and must never contain protected health information, real patient identifiers, employer-confidential information, credentials, or proprietary clinical-system configuration.

## Supported Version

Security and safety fixes are applied to the current `main` branch. Historical branches and old commits are not maintained as separate supported releases.

## Reporting a Vulnerability

Do not publish sensitive exploit details, credentials, private data, or other harmful material in a public issue.

Use GitHub's private vulnerability-reporting or Security Advisory feature when it is available for this repository. Otherwise, contact the repository owner through the GitHub profile and provide only the minimum information needed to establish a private reporting channel.

A useful report includes:

- The affected file, function, or workflow
- Reproduction steps using synthetic data only
- Expected and observed behavior
- Potential impact
- A proposed mitigation, when known

## Data-Safety Incidents

If real patient data, credentials, private keys, tokens, employer-confidential material, or proprietary clinical-system content is accidentally committed:

1. Treat the material as compromised.
2. Revoke or rotate affected credentials immediately.
3. Remove the material from the current branch.
4. Assess whether Git history must be rewritten.
5. Document the remediation without reproducing the sensitive material.

Deleting a file in a later commit does not remove it from Git history.

## Clinical Safety Boundary

The HL7- and FHIR-style artifacts in this repository are educational simulations. They are not certified, conformance-validated, or suitable for connection to production interfaces. Security review does not convert this project into a clinical product or validate it for patient care.
