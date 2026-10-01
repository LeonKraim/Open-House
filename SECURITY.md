# Security

The security policy for Open House lives at [`docs/security.md`](docs/security.md).
Read that file.

It states what the pack sandbox lets a pack reach and what it does not, what the
trust tiers, the revocation list and the pinned checksums actually enforce, that
the panel is an administrator surface, how secrets are kept out of git, and the
honest list of what is left unprotected — the anonymous broker, the privileged
container, the cached panel bundle, and the fact that the pinned digest and the
revocation list are not checked on the path a household installs through.

## Reporting a vulnerability

This project has no private security contact, no security mailing list and no PGP
key, so there is no address to send a report to. Open an issue on the repository
instead, and name the file and the command or pack that reached the problem. If
you would rather not state the finding in public, open an issue that says only
that you have a report and would like a private channel.

[`docs/reference/author-contact.md`](docs/reference/author-contact.md) explains
why there is no established private channel: the two upstream repositories that
grant nothing have authors who were never contacted, and a project that has not
opened one private channel has not established one for reporters either.
