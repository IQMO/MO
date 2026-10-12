# Project participation

MO Agent is an owner-maintained product for people who install and use it. It is
not community-governed and does not maintain a contributor roadmap.
The implementation is private. See [installation availability](README.md#installation-availability)
for the status of public downloads and user documentation.

The [license](LICENSE) governs the copies distributed under it; it does not
provide access to the private implementation. Public participation is through
product reports, not source contributions. Please do not open a pull request
unless the maintainer has explicitly requested that exact change.

## Product defect reports

Use the [product defect form](https://github.com/IQMO/MO-Downloads/issues/new?template=bug_report.yml)
for one reproducible problem. Include its smallest reproduction, the expected
and observed result, and the affected MO surface and product version.
For general help, use [MO support](https://github.com/IQMO/MO-Downloads/issues).

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

Public downloads contain the installed product and user guidance, without
the private source checkout or its history. User profile data and credentials
remain separate from the application.

Repository privacy does not change the licenses of previously distributed copies.
