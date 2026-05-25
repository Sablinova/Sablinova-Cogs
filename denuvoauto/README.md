# Denuvoauto

## Purpose

Denuvoauto is a guided support wizard for legitimate offline/manual-activation troubleshooting in a staff-managed Discord support channel. It collects structured answers, optionally parses `pub_dep.txt` and `pub_crash.txt`, and routes users to human staff at sensitive steps; it is explicitly **not** a DRM-bypass tool and does not automate token, anti-tamper, or other protected actions.

## Commands

| Command | Description |
| --- | --- |
| `/denuvoauto` | Start the wizard |
| `[p]denuvoautoset staffrole <role>` | Set the role to ping for staff handoff |
| `[p]denuvoautoset staffchannel <channel>` | Set the primary staff handoff channel |
| `[p]denuvoautoset logchannel <channel>` | Set an optional log/report mirror channel |
| `[p]denuvoautoset show` | Show current guild configuration |
| `[p]denuvoautoset reset` | Clear active sessions for the guild |

## Workflow Overview

- Start the wizard and identify whether there is a visible error.
- If there is an error, choose the closest matching branch from the error selector.
- If there is no clear error, the flow checks Steam presence, startup attempts, Bitdefender context, and ColdClient/PCL/UE conditions.
- The wizard then waits for optional `pub_dep.txt` / `pub_crash.txt` uploads.
- Parsed logs inform the follow-up branch, with fallback yes/no questions when logs are missing or incomplete.
- Verification and token-related outcomes always route to staff for manual review.
- Staff handoff is the final step; the cog posts a structured session report for humans to handle.

## Manual Staff Gates

The following nodes are deliberately manual-only because they involve sensitive review or token/DRM-adjacent handling that must not be automated:

- `COLDCLIENT_CHECK` — staff must review context before any ColdClient-related action.
- `APPLY_TOKEN` — token application is always a human-only step.
- `ANTI_TAMPER_SUBCODE` — anti-tamper code handling requires manual review for every listed subcode and unknown cases.
- `HYPERVISOR` — hypervisor-related issues are routed to staff instead of being automated.

## Diagnostic Files

`pub_dep.txt` is used for a best-effort dependency summary, including simple installed/missing markers and whether Steam appears in the text. `pub_crash.txt` is used for a best-effort crash summary, including `PubCrashLogger` detection and any memory/RAM value found in the file. Parse failures never block the wizard; if parsing fails or logs are unavailable, the flow continues with fallback questions and still hands off cleanly.

## Notes / Limitations

- Active sessions are stored only in memory and are lost if the cog or bot restarts.
- This v1 implementation focuses on the main approved flowchart branches and staff routing behavior.
- Staff handoff is terminal for the wizard session; further troubleshooting happens manually.
