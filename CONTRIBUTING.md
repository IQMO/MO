# Project participation

MO Agent is an owner-maintained product for people who install and use it. It is
not community-governed and does not maintain a contributor roadmap.

The PolyForm Shield license lets you use, change and share MO for any purpose
except providing a competing product. It does not imply that upstream pull requests or unsolicited implementation work will be
reviewed or accepted. Please do not open a pull request unless the maintainer has
explicitly requested that exact change.

## Product defect reports

Use the repository issue form only for a reproducible product defect or a
documentation error in the current published source. One report should describe
one problem, the smallest reproduction, the expected result, the observed result,
and the affected MO surface.

Before submitting, remove credentials, tokens, private URLs, personal names,
account details, local absolute paths, conversation content, and unrelated logs.
Do not upload a whole MO state home, configuration file, credentials file, memory
database, or raw diagnostic archive. If a safe minimal report cannot be made
public, do not open a public issue.

Feature proposals, general support requests, private deployment details, and
requests for access to owner-only profile or Android client sources are outside
the public issue scope.

Suspected vulnerabilities must not enter a public issue. Follow the private
[security reporting policy](.github/SECURITY.md) instead.

## Source boundary

The tracked repository is the single product codebase. User profile data and
operator extensions live in each installation's private MO home. Maintainer test
overlays, Android client implementation, and Android release tooling remain
outside Git. Their absence from a public checkout is intentional and does not
create a second public product branch; the shared Hub and authenticated protocol
remain part of the tracked MO runtime.
