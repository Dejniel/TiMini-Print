# Phomemo BLE Integration

This is the connection/session layer for existing Phomemo recipes, not another
raster dialect. Use the ordinary prepared-printer API. Classic/SPP remains
preferred when available; a BLE-only target needs no invented Classic endpoint.
Print Master recipes keep their own transport profile and controller.

## GATT and native writing

The profile requires the standard FF02 characteristic UUID, but does not
require the FF00 service. Do not select a different writable UART if FF02 is
absent. Prefer write-without-response when supported; use write-with-response
only when that is the characteristic's actual supported mode. A GATT response
does not acknowledge a printed page.

Subscribe all discovered notify/indicate characteristics. Retain their actual
instances for subscription and cleanup, including repeated UUIDs in different
services. Each characteristic instance has a stable, session-local channel ID.
Deliver its bytes through
`controller.handle_ble_notification(session, payload, source=channel_id)`.
Never concatenate partial records from different channels.
Standard GAP/GATT, Device Information and Battery notifications remain metadata:
they are subscribed and logged separately, never decoded as printer credits or
page replies. Classification uses the owning service, not just a characteristic UUID.
These metadata subscriptions are best-effort: a refused Service Changed or
Battery subscription does not invalidate a usable printer reply channel.
Required printer-channel failures still abort setup and close earlier subscriptions.
An active metadata subscription alone does not provide a printer reply receiver.
Reply waits/queries require a subscribed printer-data channel; dedicated flow-control
subscriptions do not count either. Metadata-only connections still close their
subscriptions normally, and Phomemo preparation rejects them before sending queries.

Host-buffer readiness belongs to the native writer. Await it as required by the
platform even when the printer has granted credits. The desktop adapter awaits
the public Bleak write operation; native bridges must honor their platform's
readiness callbacks. No runtime timer manufactures host acceptance or printer
credits.

## Startup

Enable notifications before attempting metadata writes. Preserve every initial
notification until the first runtime attachment. Recent-reply history is not
sufficient: it can expire or discard the printer's initial grant. The built-in
adapter retains up to 64 KiB / 1024 startup records and reports an overflow
rather than continuing with incomplete state.

Attach and initialize the controller before sending. The runtime session
reports the actual opened transport through
`is_ble_transport()`; initializing a Classic/serial connection does not enable
BLE flow control. A BLE connection without an active notification receiver
fails preparation rather than reverting to unchecked streaming.

The ordinary initial credit count is zero. The first metadata write waits for a positive printer
grant; there is no unconditional first-command bypass. A missing initial grant
fails with an explicit write-credit timeout. The printer's exact grant timing
has not been hardware-verified.

BLE metadata queries use one batch and one bounded reply window, rather than
a separate wait for every optional field. Cover/paper checks and recipe-specific
requirements remain in the existing runtime. A recipe that requires a serial
or chip cannot prepare without it; missing optional information does not become
a new requirement. Wi-Fi provisioning, clock updates and mandatory MAC-based
application readiness are not required for this print path.

The built-in host-side credit budgets are at least 10 seconds outside a print
and 180 seconds during a print, or the longer caller timeout. These are limits
on waiting for permission, not firmware timing specifications, per-page
completion deadlines or permission to ignore a fault. Cancellation, a printer
fault or connection shutdown interrupts a blocked writer.
The desktop adapter also forwards an unsolicited native BLE disconnect:
blocked writers/reply waits are released immediately and runtime shutdown is
drained during cleanup before the I/O loop is disposed.

## Write allowances

| Incoming record | Runtime interpretation |
| --- | --- |
| `01 n` | Add unsigned `n` credits; zero permits nothing |
| `02 n` | Replace the application payload limit |
| `1A 36 n` | Configure writes per credit; `n=1` selects five writes |
| `AA AB AC 01 hi lo` | Diagnostic total only; never a spendable grant |

The fallback application payload limit is 182 bytes. A later `02 n` replaces
it, including values above 182; the writer still enforces the native
characteristic maximum and the profile cap. A zero advertised limit fails
before a native write.

Each physical write counts toward the configured burst. The ordinary mode
spends one credit per write. A partial burst persists across sends and pages;
starting another page or command does not reset the window. Only the documented
chip/serial path schedules a one-credit reset after one second. It does not
grant an unconditional startup allowance.

Use the same writer and
`await controller.before_write(session, size=proposed_size, timeout=timeout)`
for payload, step, control-command and bulk writes. Honor a returned smaller
size, advance by the actual bytes written, and retain the unsent remainder.
Do not refund, retry or replay data after a failed or cancelled native write.

## Replies and completion

The BLE decoder handles split/coalesced credit records and ordinary statuses.
A recognized status owns its whole payload: embedded bytes resembling credits
are not interpreted as another record. Each channel retains its own partial
frame; query completion uses validated current-session replies.

The existing seven-byte `1A 3B` capability framing is retained. A shorter
synthetic parser input is not evidence of a different printer wire format.
BLE transport alone does not select a new capability layout, geometry,
grayscale policy or page recipe.

Write credits, an empty send queue and a native write response are not physical
completion. Existing per-page result waits and local-delay policies still run.
An old successful page result received while waiting for the first write
credit cannot complete the new, unsent page.
Debug diagnostics expose window waits, limits and selected replies; MAC identity
replies are masked, and transmitted raster bytes stay at TRACE.

## Verification boundary

Regression tests cover startup/replay, fragmented and interleaved channels,
additive/burst credits, changing limits, host bounds, cancellation, faults,
cleanup and both payload and step-based jobs. They do not constitute printer
or iOS bridge hardware verification.
