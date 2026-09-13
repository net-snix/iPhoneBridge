# iPhoneBridge native protocol v1

This is the implementation contract for the native mirror replacement. All
integers are unsigned and big endian. TCP binds only to 127.0.0.1:15901 and is
carried by the existing owned SSH session over USB. SSH supplies authentication.

## Framing

Every message is `u32 body_length | u8 type | u8 flags | u16 reserved |
u32 request_id | payload`. Body length includes the eight-byte message header,
excludes its own four bytes, and is bounded to 8..16777216. Reserved and flags
must be zero in v1. Request IDs are nonzero for client requests and echoed in
responses. Unsolicited server messages use zero. Reject invalid lengths before
allocating; handle partial reads/writes. Unknown commands produce ERROR.
VIDEO access units contain at most 1,024 NAL units; each NAL includes its
two-byte HEVC header. This bounds parser work as well as payload size.

V1 carries SDR sRGB pixels. STILL contains sRGB BGRA; PNG clients preserve those
RGB values and declare sRGB. HEVC uses BT.709 primaries, the sRGB transfer function
and BT.709 YCbCr matrix (ISO code points **1/13/1**), with limited-range YCbCr.
The encoder validates its actual format description. The Mac reconstructs that
canonical colour description and rejects conflicting explicit metadata. The
retained private capture API renders in sRGB; Display P3 preservation is not
supported, even when the phone's independent USB screenshots are tagged P3.

## Messages

| Type | Name | Payload |
| --- | --- | --- |
| 1 | HELLO (server) | `"IPBM" u16 version=1 u16 capabilities=7 u32 portrait_width u32 portrait_height u32 quarter_turns u32 generation` |
| 2 | FORMAT (server) | `u32 generation u32 codec='hvc1' u32 portrait_width u32 portrait_height u32 quarter_turns u32 parameter_count`, then each parameter `u32 length bytes` (VPS,SPS,PPS); NAL length field is always 4 bytes |
| 3 | VIDEO (server) | `u32 generation u64 pts_ns u32 keyframe` followed by VT's four-byte-length-prefixed NAL units |
| 4 | STILL (server) | `u32 generation u32 portrait_width u32 portrait_height u32 quarter_turns u64 pts_ns` then tightly packed BGRA pixels, no row padding |
| 5 | GEOMETRY (server) | `u32 portrait_width u32 portrait_height u32 quarter_turns u32 generation` |
| 6 | ACK (server) | empty |
| 7 | ERROR (server) | `u32 code` then UTF-8 diagnostic; codes 1 malformed, 2 busy, 3 stale geometry, 4 unavailable |
| 8 | PONG (server) | echoed PING payload |
| 9 | STATS (server response) | UTF-8 JSON object, bounded to 64 KiB |
| 16 | SUBSCRIBE | `u32 enabled` (0/1); ACK then FORMAT + keyframe; only one video subscriber permitted |
| 17 | REQ_KEYFRAME | empty; ACK; forces a new capture even on static screen |
| 18 | REQ_STILL | empty; fresh STILL response on a following capture tick, including when screen dirty count did not change |
| 19 | GET_GEOMETRY | empty; GEOMETRY response |
| 20 | ACQUIRE_INPUT | empty; ACK grants connection-exclusive input ownership, ERROR busy otherwise |
| 21 | RELEASE_INPUT | empty; release all held input owned by this connection, ACK |
| 22 | POINTER | `u32 generation u32 action u32 x u32 y`; actions 0 up, 1 down, 2 move, displayed framebuffer pixel space |
| 23 | KEY | `u32 generation u32 USB_HID_keyboard_usage u32 down` (0/1); usage page 7 only, US layout |
| 24 | BUTTON | `u32 generation u32 button`; 1 Home, 2 App Switcher; complete native press sequence, then ACK |
| 25 | PING | at most 32 opaque bytes |
| 26 | GET_STATS | empty; STATS response |

POINTER, KEY and BUTTON require an acquired lease and ACK only after dispatch
to the HID generator. A connection loses its lease on disconnect or 25 seconds
without an input/lease command; release held touches/keys on either event.
The agent acquires for its entire action and post-action still; the Mac acquires
for a gesture or typing sequence. Other clients get an explicit busy response.
Cap connections and pending commands; no unbounded queues. Health reads HELLO
without subscribing, capturing or taking input ownership.

## Geometry and frame ownership

The capture and encoder stay in native portrait dimensions (1170x2532 on the
tested phone). `quarter_turns` is the clockwise rotation from capture to display:
portrait 0, landscape-left 1, upside-down 2, landscape-right 3. Displayed width
and height swap for odd turns. The Mac and Python rotate output; the daemon
alone inverse maps displayed input to the physical portrait digitizer.
Generation changes on geometry changes or encoder recreation. Validate it for
each input operation, not only at connection time. Broadcast GEOMETRY changes;
FORMAT precedes each new generation's first VIDEO and every keyframe.

Video buffers must own their pixel storage until the encoder callback completes.
Use a bounded surface pool; gate capture before render when encoder/socket queues
are full. Never discard an encoded interframe and then send its dependants.
After congestion or an encoder error, request an IDR before resuming. Ordinary
static-screen idle retains the valid reference chain and does not force a new
IDR. A stuck subscriber is disconnected with bounded cleanup; control clients do
not subscribe to video. A requested STILL
owns its immutable bytes independently of subsequent capture and encoding.

The receiver decodes every accepted sample in sequence. It may coalesce only
already-decoded output for display. After a decoder error, discard dependent
samples, request a keyframe, and resume at FORMAT + IDR. Reject old-generation
asynchronous callbacks after rotation/reconnect. Static screens retain their
last displayed frame and send no periodic video or telemetry traffic.

## Requested timing diagnostics

`GET_STATS` includes a `timing` object with fixed rings of the latest 40 successful
key-down dispatches and 40 completed frames. Ring updates store scalar values;
JSON is created only when requested. `key_down_count`, `after_idle_submissions`
and capture counters are cumulative for the daemon session. Ring entries can be
overwritten during continuous motion, so collect stats immediately after a test.

All phone timestamps ending in `_ns` use `CLOCK_UPTIME_RAW` nanoseconds. Input
records identify the input sequence, connection, request and geometry generation,
with receive, main-thread handling, HID dispatch, next capture attempt and first
captured-frame timestamps. The first captured frame need not contain the visible
response. Frame records identify `input_sequence` and the exact VIDEO `pts_ns`,
with capture, dirty-count read, render, transfer, encode submission, callback,
completion and queue timestamps. `after_idle` marks a submission gap over 100 ms;
`forced_keyframe` and `keyframe` separately record the request and encoder output.
Direct rendering has no transfer stage, so its
render and transfer completion timestamps are equal. `queued_ns` records output
enqueue, not socket completion. Encoder submission may overlap the callback
interval; do not add their durations as independent work.

Mac benchmark events retain `phonePtsNs` as an exact unsigned 64-bit integer for
joining to those records. Mac receive, decode and display-submission timestamps
use Mac uptime milliseconds. The two clocks are not synchronized: compare
durations within each clock, never subtract a phone timestamp from a Mac one.
Display submission identifies its own frame because the display may coalesce
decoded frames. Neither timestamp measures physical display scanout.

## Opt-in benchmark endpoint

The disposable Mac fixture server on loopback port 15802 accepts native reports
only at `/metrics`, with `X-iPhoneBridge-Protocol: IPBM/1` and no browser Origin.
Reports are capped at 1 MiB, allowing 30-second decoded/display event arrays.
HTTP bodies have an absolute five-second deadline; idle connections time out
after five seconds and at most 16 handlers run concurrently. This endpoint is
separate from the daemon protocol and is started only by `bridge test-surface`.
