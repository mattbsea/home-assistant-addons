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
const STALE_ECHO_MS = 12 * 60 * 60 * 1000;      // window to treat a repeat banner as stale, not new

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

const ABSOLUTE_RESET_RE = /resets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([^)]+)\))?/gi;

// Last "resets ..." match in `text`, or null. Exposed separately from
// parseResetTime so callers can compare the *literal* matched text across
// calls (e.g. to recognize a stale re-painted banner) without re-deriving it
// from an already-resolved epoch.
function lastAbsoluteResetMatch(text) {
    const matches = [...text.matchAll(ABSOLUTE_RESET_RE)];
    if (matches.length === 0) return null;
    const m = matches[matches.length - 1];
    return {
        raw: m[0],
        hour: parseInt(m[1], 10),
        minute: m[2] ? parseInt(m[2], 10) : 0,
        ampm: m[3] ? m[3].toLowerCase() : null,
        tz: m[4] ? m[4].trim() : null,
    };
}

// Calendar date + time, as rendered in `tz` at instant `atMs`. Null if `tz`
// isn't a name Intl recognizes (caller falls back to the server's own zone).
function zonedParts(tz, atMs) {
    try {
        const fmt = new Intl.DateTimeFormat('en-US', {
            timeZone: tz || undefined,
            hour12: false,
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        });
        const parts = {};
        for (const p of fmt.formatToParts(new Date(atMs))) parts[p.type] = p.value;
        return {
            year: Number(parts.year),
            month: Number(parts.month),
            day: Number(parts.day),
            // Some locale/timeZone combinations render midnight as hour "24"
            // under hour12:false; normalize back to 0.
            hour: parts.hour === '24' ? 0 : Number(parts.hour),
            minute: Number(parts.minute),
        };
    } catch (e) {
        return null;
    }
}

// UTC-offset (in minutes) of `tz` at instant `atMs` -- derived per-instant
// rather than assumed constant, so it's correct on either side of a DST
// transition.
function tzOffsetMinutes(tz, atMs) {
    const p = zonedParts(tz, atMs);
    if (p === null) return 0;
    const asUTC = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, 0);
    return Math.round((asUTC - atMs) / 60000);
}

// Epoch ms for the next occurrence (today, or tomorrow if today's has
// already passed) of `hour:minute` wall-clock time in `tz`, at-or-after
// `nowMs`. Null if `tz` is an unrecognized zone name.
//
// Resolves the *target* instant's own UTC offset (two-pass fixed point,
// converges immediately outside the transition itself) instead of adding
// "N wall-clock minutes" to now -- the latter silently drifts by an hour
// whenever the wait crosses a DST transition, which is exactly what parked a
// live, already-resumed session on a bogus next-day timer: the stale banner
// text still read "resets 11:10am" well after that time had passed, and the
// old delta-minutes math rolled it forward using today's offset even though
// "tomorrow" might use a different one.
function nextOccurrenceEpoch(tz, hour, minute, nowMs) {
    const today = zonedParts(tz, nowMs);
    if (today === null) return null;

    const epochForDay = (day) => {
        // Date.UTC normalizes an out-of-range day (e.g. day 32) into the
        // correct next month, so passing today.day + 1 here is safe.
        let guess = Date.UTC(today.year, today.month - 1, day, hour, minute, 0);
        for (let i = 0; i < 2; i++) {
            guess = Date.UTC(today.year, today.month - 1, day, hour, minute, 0)
                - tzOffsetMinutes(tz, guess) * 60000;
        }
        return guess;
    };

    const todayEpoch = epochForDay(today.day);
    return todayEpoch > nowMs ? todayEpoch : epochForDay(today.day + 1);
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
    const m = lastAbsoluteResetMatch(text);
    if (m === null) return null;

    let hour = m.hour;
    if (m.ampm === 'pm' && hour !== 12) hour += 12;
    if (m.ampm === 'am' && hour === 12) hour = 0;
    if (hour > 23 || m.minute > 59) return null;

    const epoch = nextOccurrenceEpoch(m.tz, hour, m.minute, nowMs);
    if (epoch !== null) return epoch;
    // Named zone Intl couldn't resolve -- fall back to the server's own zone.
    return m.tz ? nextOccurrenceEpoch(null, hour, m.minute, nowMs) : null;
}

class AutoContinueWatcher {
    constructor(label, write, log) {
        this.label = label;
        this.write = write;    // (data) => void, writes to the PTY stdin
        this.log = log || console.log;
        this.tail = '';
        this.timer = null;
        this.scheduledResetAt = null;
        this.pendingRaw = null;    // raw "resets ..." text behind the current schedule
        this.lastFiredRaw = null;  // raw text already acted on -- see onData
        this.lastFiredAt = 0;
        this.cooldownUntil = 0;
    }

    onData(chunk) {
        this.tail = (this.tail + stripAnsi(chunk.toString())).slice(-MAX_TAIL);

        const now = Date.now();
        if (now < this.cooldownUntil) return;

        const matched = detectLimit(this.tail);
        if (!matched) return;

        const abs = lastAbsoluteResetMatch(this.tail);
        if (abs !== null && abs.raw === this.lastFiredRaw && (now - this.lastFiredAt) < STALE_ECHO_MS) {
            // The CLI re-painted the exact same "resets ..." text we already
            // resumed from -- not a new limit event. Recomputing "next
            // occurrence" for it now (the clock time has necessarily already
            // passed, or we wouldn't have fired on it) would roll a whole day
            // forward and park an already-working session on a bogus timer.
            this.log(`[auto-continue] "${this.label}": ignoring stale repeat of an ` +
                     `already-handled reset time — "${abs.raw}"`);
            return;
        }

        const resetAt = parseResetTime(this.tail, now);
        if (resetAt !== null) {
            if (this.timer && this.scheduledResetAt !== null &&
                Math.abs(resetAt - this.scheduledResetAt) < RESCHEDULE_TOLERANCE_MS) {
                return;  // same limit event, already scheduled
            }
            this.log(`[auto-continue] "${this.label}": limit detected — "${matched}"`);
            this.schedule(resetAt, abs ? abs.raw : null);
        } else if (!this.timer) {
            // Limit detected but no reset time found; retry conservatively.
            // If the limit is still active the message reappears and we loop.
            this.log(`[auto-continue] "${this.label}": limit detected — "${matched}" ` +
                     `(no reset time in message)`);
            this.schedule(now + FALLBACK_DELAY_MS - RESUME_GRACE_MS, null);
        }
    }

    schedule(resetAt, raw) {
        const now = Date.now();
        const fireAt = Math.min(resetAt + RESUME_GRACE_MS, now + MAX_WAIT_MS);
        const delay = Math.max(fireAt - now, 5000);

        if (this.timer) clearTimeout(this.timer);
        this.scheduledResetAt = resetAt;
        this.pendingRaw = raw;
        this.timer = setTimeout(() => this.fire(), delay);
        this.timer.unref?.();

        const mins = Math.round(delay / 60000);
        this.log(`[auto-continue] "${this.label}": will send "continue" at ` +
                 `${new Date(fireAt).toISOString()} (in ~${mins} min)`);
    }

    fire() {
        this.timer = null;
        this.scheduledResetAt = null;
        this.lastFiredRaw = this.pendingRaw;
        this.lastFiredAt = Date.now();
        this.pendingRaw = null;
        this.cooldownUntil = Date.now() + COOLDOWN_MS;
        // Log what the terminal actually shows right before we act. If the CLI has
        // moved on to some other screen (a banner, a menu, a re-auth prompt) instead
        // of sitting at an idle input line, typing "continue" into it silently does
        // nothing -- this snapshot is what tells us that happened after the fact.
        this.log(`[auto-continue] "${this.label}": limit should have reset; sending "continue". ` +
                 `Screen right before sending: ${JSON.stringify(this.tail.slice(-300))}`);
        this.tail = '';
        try {
            this.write('continue');
            this.log(`[auto-continue] "${this.label}": wrote "continue" to PTY`);
            // Send Enter separately so the TUI registers the text before submit
            setTimeout(() => {
                try {
                    this.write('\r');
                    this.log(`[auto-continue] "${this.label}": wrote Enter to PTY`);
                } catch (e) {
                    this.log(`[auto-continue] "${this.label}": failed to write Enter to PTY: ${e.message}`);
                }
            }, 500);
        } catch (e) {
            this.log(`[auto-continue] "${this.label}": failed to write "continue" to PTY: ${e.message}`);
        }
    }

    dispose() {
        if (this.timer) {
            // If the PTY restarts (e.g. the CLI process exited and the add-on respawned
            // it) while a "continue" was scheduled, that schedule is silently lost here
            // -- attachPtyHandlers builds a brand new watcher for the new process, and
            // the reset time this one learned isn't carried over. Log it so a lost
            // auto-continue shows up as a respawn-timing log line instead of nothing.
            this.log(`[auto-continue] "${this.label}": disposed with a pending "continue" ` +
                     `scheduled for ${new Date(this.scheduledResetAt).toISOString()} -- it will not fire`);
        }
        if (this.timer) clearTimeout(this.timer);
        this.timer = null;
        this.scheduledResetAt = null;
        this.tail = '';
    }
}

module.exports = { AutoContinueWatcher, stripAnsi, detectLimit, parseResetTime };
