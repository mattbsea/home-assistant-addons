// Auto-continue: watches a Claude tab's PTY output for rate/usage-limit
// messages, parses when the limit resets, and types "continue" + Enter
// one minute after the reset so the session resumes unattended.
//
// Messages handled (matched on ANSI-stripped text):
//   "5-hour limit reached - resets 3pm (UTC)"
//   "Claude usage limit reached. Resets at 2pm"
//   "You're out of extra usage · resets 3pm"
//   "Please try again in 5 hours"
//   "You've hit your limit · resets 3pm (Europe/Dublin)"
//   "Rate limit hit. Resets at 4pm"

const MAX_TAIL = 4000;                          // rolling window of recent output
const RESUME_GRACE_MS = 60 * 1000;              // fire 1 minute after the reset time
const FALLBACK_DELAY_MS = 30 * 60 * 1000;       // retry delay when no time can be parsed
const MAX_WAIT_MS = 24 * 60 * 60 * 1000;        // sanity cap on how long we'll wait
const COOLDOWN_MS = 30 * 1000;                  // ignore detections right after firing
const RESCHEDULE_TOLERANCE_MS = 2 * 60 * 1000;  // same reset time => same limit event

// Apostrophes may be ASCII or typographic depending on the terminal/font path.
const LIMIT_PATTERNS = [
    /\d+[- ]hour limit reached/i,
    /(?:usage|session|rate) limit reached/i,
    /out of extra usage/i,
    /you.{0,3}ve hit your (?:[a-z]+ )?limit/i,
    /rate limit (?:hit|reached)/i,
    /please try again in \d+/i,
];

function stripAnsi(text) {
    return text
        .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?/g, '')          // OSC sequences
        .replace(/\x1b\[[0-9;:?]*[\x20-\x2f]*[\x40-\x7e]/g, '')      // CSI sequences
        .replace(/\x1b[\x40-\x5f]/g, '')                             // other ESC sequences
        .replace(/[\x00-\x09\x0b-\x1f\x7f]/g, ' ');
}

function detectLimit(text) {
    for (const re of LIMIT_PATTERNS) {
        const m = text.match(re);
        if (m) return m[0];
    }
    return null;
}

// Current wall-clock position within the day for a given IANA timezone
// (or the server's local timezone when tz is null). Returns null if the
// timezone name is unusable even after falling back to local time.
function wallClock(tz, nowMs) {
    try {
        const fmt = new Intl.DateTimeFormat('en-US', {
            timeZone: tz || undefined,
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        });
        const parts = {};
        for (const p of fmt.formatToParts(new Date(nowMs))) {
            parts[p.type] = p.value;
        }
        return {
            minutes: (parseInt(parts.hour, 10) % 24) * 60 + parseInt(parts.minute, 10),
            seconds: parseInt(parts.second, 10),
        };
    } catch (e) {
        return tz ? wallClock(null, nowMs) : null;
    }
}

// Returns the epoch ms when the limit lifts, or null if no time is found.
function parseResetTime(text, nowMs) {
    // Relative: "Please try again in 5 hours" / "... in 30 minutes"
    const relMatches = [...text.matchAll(/try again in (\d+)\s*(hour|minute|second)s?/gi)];
    if (relMatches.length > 0) {
        const m = relMatches[relMatches.length - 1];
        const n = parseInt(m[1], 10);
        const unitMs = { hour: 3600000, minute: 60000, second: 1000 }[m[2].toLowerCase()];
        return nowMs + n * unitMs;
    }

    // Absolute: "resets 3pm (UTC)" / "Resets at 2:30pm" / "resets 15:00 (Europe/Dublin)"
    const absMatches = [...text.matchAll(
        /resets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([^)]+)\))?/gi
    )];
    if (absMatches.length === 0) return null;
    const m = absMatches[absMatches.length - 1];

    let hour = parseInt(m[1], 10);
    const minute = m[2] ? parseInt(m[2], 10) : 0;
    const ampm = m[3] ? m[3].toLowerCase() : null;
    const tz = m[4] ? m[4].trim() : null;
    if (ampm === 'pm' && hour !== 12) hour += 12;
    if (ampm === 'am' && hour === 12) hour = 0;
    if (hour > 23 || minute > 59) return null;

    const now = wallClock(tz, nowMs);
    if (now === null) return null;
    let deltaMin = hour * 60 + minute - now.minutes;
    if (deltaMin <= 0) deltaMin += 24 * 60;  // already past today => next occurrence
    return nowMs - now.seconds * 1000 + deltaMin * 60000;
}

class AutoContinueWatcher {
    constructor(label, write, log) {
        this.label = label;
        this.write = write;    // (data) => void, writes to the PTY stdin
        this.log = log || console.log;
        this.tail = '';
        this.timer = null;
        this.scheduledResetAt = null;
        this.cooldownUntil = 0;
    }

    onData(chunk) {
        this.tail = (this.tail + stripAnsi(chunk.toString())).slice(-MAX_TAIL);

        const now = Date.now();
        if (now < this.cooldownUntil) return;

        const matched = detectLimit(this.tail);
        if (!matched) return;

        const resetAt = parseResetTime(this.tail, now);
        if (resetAt !== null) {
            if (this.timer && this.scheduledResetAt !== null &&
                Math.abs(resetAt - this.scheduledResetAt) < RESCHEDULE_TOLERANCE_MS) {
                return;  // same limit event, already scheduled
            }
            this.schedule(resetAt, matched);
        } else if (!this.timer) {
            // Limit detected but no reset time found; retry conservatively.
            // If the limit is still active the message reappears and we loop.
            this.schedule(now + FALLBACK_DELAY_MS - RESUME_GRACE_MS, matched + ' (no reset time parsed)');
        }
    }

    schedule(resetAt, reason) {
        const now = Date.now();
        const fireAt = Math.min(resetAt + RESUME_GRACE_MS, now + MAX_WAIT_MS);
        const delay = Math.max(fireAt - now, 5000);

        if (this.timer) clearTimeout(this.timer);
        this.scheduledResetAt = resetAt;
        this.timer = setTimeout(() => this.fire(), delay);
        this.timer.unref?.();

        const mins = Math.round(delay / 60000);
        this.log(`[auto-continue] "${this.label}": detected "${reason}"; ` +
                 `sending "continue" at ${new Date(fireAt).toISOString()} (in ~${mins} min)`);
    }

    fire() {
        this.timer = null;
        this.scheduledResetAt = null;
        this.tail = '';
        this.cooldownUntil = Date.now() + COOLDOWN_MS;
        this.log(`[auto-continue] "${this.label}": limit should have reset; sending "continue"`);
        try {
            this.write('continue');
            // Send Enter separately so the TUI registers the text before submit
            setTimeout(() => {
                try { this.write('\r'); } catch (e) {}
            }, 500);
        } catch (e) {
            this.log(`[auto-continue] "${this.label}": failed to write to PTY: ${e.message}`);
        }
    }

    dispose() {
        if (this.timer) clearTimeout(this.timer);
        this.timer = null;
        this.scheduledResetAt = null;
        this.tail = '';
    }
}

module.exports = { AutoContinueWatcher, stripAnsi, detectLimit, parseResetTime };
