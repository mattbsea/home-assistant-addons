'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { AutoContinueWatcher, parseResetTime } = require('../auto-continue');

// Format an epoch back into a zone's wall-clock time, for round-trip assertions
// that don't require hand-computing UTC offsets.
function formatInZone(epochMs, tz) {
    return new Intl.DateTimeFormat('en-US', {
        timeZone: tz,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: 'numeric', minute: '2-digit', hour12: true,
    }).format(new Date(epochMs));
}

test('parseResetTime resolves a same-day future time in the named zone', () => {
    // 2026-03-07T21:00:00Z = 1:00 PM PST (America/Los_Angeles is UTC-8 in March, pre-DST).
    const now = Date.UTC(2026, 2, 7, 21, 0, 0);
    const epoch = parseResetTime("You've hit your session limit · resets 6pm (America/Los_Angeles)", now);
    assert.equal(formatInZone(epoch, 'America/Los_Angeles'), '03/07/2026, 6:00 PM');
});

test('parseResetTime rolls an already-passed time to tomorrow, correctly, across a DST transition', () => {
    // 2026-03-07T05:00:00Z = 9:00 PM PST on March 7. Target "6pm" has already passed
    // today, so this must roll to March 8's 6pm -- but America/Los_Angeles springs
    // forward (PST -> PDT, UTC-8 -> UTC-7) at 2am on March 8. A wait computed as
    // "N wall-clock minutes from now" (rather than re-resolving the target zone's
    // offset at the target instant) silently loses the hour the clocks skipped and
    // lands on 7pm instead of 6pm -- this is the exact bug that parked a live
    // session on a bogus next-day timer.
    const now = Date.UTC(2026, 2, 8, 5, 0, 0);
    const epoch = parseResetTime("You've hit your session limit · resets 6pm (America/Los_Angeles)", now);
    assert.equal(formatInZone(epoch, 'America/Los_Angeles'), '03/08/2026, 6:00 PM');
    assert.equal(epoch, Date.UTC(2026, 2, 9, 1, 0, 0), 'must land on the DST-correct instant, not 1h off');
});

test('parseResetTime rolls an already-passed time to tomorrow, correctly, across the fall-back transition', () => {
    // 2026-10-31T22:00:00Z = 3:00 PM PDT. Target "1pm" has passed today, rolls to
    // Nov 1, which falls back (PDT -> PST, UTC-7 -> UTC-8) at 2am that day.
    const now = Date.UTC(2026, 9, 31, 22, 0, 0);
    const epoch = parseResetTime("You've hit your session limit · resets 1pm (America/Los_Angeles)", now);
    assert.equal(formatInZone(epoch, 'America/Los_Angeles'), '11/01/2026, 1:00 PM');
    assert.equal(epoch, Date.UTC(2026, 10, 1, 21, 0, 0), 'must land on the DST-correct instant, not 1h off');
});

test('parseResetTime falls back to the server zone for an unrecognized timezone name', () => {
    const now = Date.UTC(2026, 2, 7, 10, 0, 0);
    const epoch = parseResetTime("resets 6pm (Not/AZone)", now);
    // Unresolvable zone -> falls back to null (server-local) rather than throwing.
    assert.equal(typeof epoch, 'number');
});

test('AutoContinueWatcher ignores a verbatim repeat of an already-fired reset banner', () => {
    const writes = [];
    const logs = [];
    const watcher = new AutoContinueWatcher('car-lights', (d) => writes.push(d), (m) => logs.push(m));

    // Simulate having already resolved this exact reset banner.
    watcher.pendingRaw = 'resets 11:10am (America/Los_Angeles)';
    watcher.fire();
    assert.equal(writes[0], 'continue');
    writes.length = 0;
    watcher.cooldownUntil = 0; // bypass the short post-fire cooldown for the test

    // The CLI redraws the exact same stale banner text (no new limit event).
    watcher.onData("You've hit your session limit · resets 11:10am (America/Los_Angeles)");

    assert.deepEqual(writes, [], 'must not type "continue" again for the same stale banner');
    assert.equal(watcher.timer, null, 'must not create a bogus next-day schedule');
    assert.ok(logs.some((l) => l.includes('ignoring stale repeat')), 'must log why nothing happened');

    watcher.dispose();
});

test('AutoContinueWatcher schedules normally for a genuinely different reset time after firing', () => {
    const writes = [];
    const watcher = new AutoContinueWatcher('car-lights', (d) => writes.push(d), () => {});

    watcher.pendingRaw = 'resets 11:10am (America/Los_Angeles)';
    watcher.fire();
    watcher.cooldownUntil = 0;

    watcher.onData("You've hit your session limit · resets 4:30pm (America/Los_Angeles)");

    assert.ok(watcher.timer, 'a new schedule should be created for a genuinely different reset time');
    watcher.dispose();
});
