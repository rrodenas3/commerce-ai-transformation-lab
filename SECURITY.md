# Security and responsible disclosure

## Project boundary

This repository contains a local, synthetic transformation laboratory. It must not contain credentials, tokens, personal data, confidential company information, real customer records, or production connection details.

The public verifier and CI workflow are preventive controls, not a security audit or certification.

## Reporting a concern

Use GitHub's private vulnerability reporting feature when available. Do not include an exploit, credential, or personal information in a public issue.

For a public documentation concern that carries no sensitive detail, open an issue describing the affected file and the minimum information needed to reproduce the problem.

## Supported surface

Only the latest default-branch revision is maintained. No production service, live API, customer system, or operational availability commitment is offered.

## Data handling rules

- Use generated, fictional data only.
- Keep secrets in local environment variables that are ignored by Git; never commit secret files.
- Use least-privilege, read-only, or dry-run adapters during development.
- Treat financial, customer-facing, and irreversible actions as consequential.
- Require independent verification of simulated action postconditions.
- Preserve traceability without storing sensitive payloads.
