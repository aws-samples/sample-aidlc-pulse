# Security

## Reporting a vulnerability

If you discover a potential security issue in this project, notify AWS/Amazon Security through the [vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/). Please do **not** create a public GitHub issue.

## Security model

AI-DLC Pulse is sample code for local, single-user use. It has no authentication, so it is designed to be reachable only from your own machine.

- **Loopback only.** The server binds to `127.0.0.1` by default. `--host` accepts only loopback addresses and refuses anything else.
- **Host header check.** Requests whose `Host` header isn't a loopback name are rejected, which guards against DNS rebinding.
- **Read-only.** The server never writes to the AI-DLC project it reads.
- **Fixed routes.** The server serves a fixed list of its own page, script and style files plus three read-only JSON endpoints. There is no general file server.
- **Contained file access.** The artifact endpoint serves only text files (`.md`, `.json`, `.yaml`, `.yml`, `.txt`, `.html`, `.mmd`) up to 512 KB inside the selected run's folder. File names may contain spaces and non-ASCII letters. It rejects `..` and `.` segments, hidden (dot) files and folders, absolute paths, control characters and separators inside a name, and paths that resolve outside that folder, including through symlinks.
- **Response headers.** Every response sends a Content-Security-Policy (`default-src 'none'; script-src 'self'; style-src 'self'`, no framing, no form posts), `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. Scripts and styles load only from Pulse's own files; inline scripts, `<style>` blocks and `style` attributes are refused.
- **No markup injection.** File content is rendered as text. The markdown viewer builds DOM elements directly and never inserts HTML.
- **Bounded reads.** Each project file is read up to 16 MiB, parsed files are cached up to 128 MiB, and query strings are limited to 2,048 characters.

## Your responsibilities

- Don't expose the dashboard on a network interface, through a reverse proxy, or with port forwarding. Anyone who can reach it can read the AI-DLC records of the project it points at.
- Point `--project` only at projects you are allowed to read.
- Work with your security and legal teams to meet your organization's requirements before using this sample with sensitive projects.
